# ROS 2 motor bridge 연동 변경 계획

작성일: 2026-09-07
상태: 소프트웨어 구현 및 자동 검증 완료, 실제 MCU/차량 시험은 미실행. 결과는 `../ros2_motor_bridge_update_report.md` 참고.

## 1. 목표와 근거

현재 MCU의 기본 상태 패킷인 System Status v2(`0x82`)를 ROS에서 수신하고, 네 바퀴와 두 드라이버의 상태를 freshness와 함께 발행한다. 구형 MCU의 `0x81` 호환을 유지하며, 정상 주행 통신과 raw CAN 진단 통신을 분리한다.

참고 문서:

- `/home/jina/car_ws/Wheel-Dragoon/agent/ros2_update_report.md`
- `/home/jina/car_ws/Wheel-Dragoon/README.md`: 요청의 `readme.md`는 존재하지 않으며 실제 파일명은 `README.md`이다.

위 문서의 프로토콜을 기준으로 현재 `src/my_car_motor_bridge`와 상태 소비자인 `src/my_car_web_monitor`를 확인했다. 보고서의 “ROS 패키지는 수정하지 않는다”는 원래 MCU 작업의 범위 설명이며, 이 계획은 그 후속 ROS 작업을 다룬다. 이번 요청에서는 계획 파일만 작성한다.

## 2. 현재 코드와 차이

| 위치 | 현재 동작 | 필요한 변경 |
| --- | --- | --- |
| `my_car_motor_bridge/protocol.py` | 길이 7, `0x81`만 수신 | type별 길이 검사, v2 및 CAN tunnel 지원 |
| `my_car_motor_bridge/motor_bridge_node.py` | 항상 `/cmd_vel` 구독과 command timer 생성, 평면 JSON 발행 | 모드별 인터페이스, v2 우선 발행, legacy fallback |
| 위 노드의 종료 처리 | 항상 disable/zero `0x01` 송신 | bridge 종료에서는 송신 금지: `0x01`은 MCU를 CONTROL로 전환함 |
| 위 노드의 재연결 처리 | 포트만 다시 열고 수신 버퍼 유지 | 연결별 파서/상태 선택 정보 초기화 |
| `my_car_motor_bridge/serial_transport.py` | 64-byte read, write 반환 길이 미검사 | burst 수신 예산과 partial write 실패 처리 |
| `config/motor_bridge.yaml`, launch | control 전용 설정 | 모드 및 fallback 설정 노출 |
| 웹 `control.py`, `web/static/index.html` | JSON을 그대로 저장하고 문자열로 표시 | 중첩 JSON 호환 확인; 상세 시각화는 후속 작업 |
| 패키지 README | `0x81` 명세와 일부 과거 MCU 책임 설명 | 현재 wire 명세, 모드, 진단 의미로 갱신 |

가장 먼저 해결할 문제는 현재 노드가 기본 MCU의 `0x82`를 모두 버려 상태를 발행하지 못한다는 점이다.

## 3. 반드시 지킬 프로토콜 계약

- 공통 framing: `AA 55 length payload checksum`. length는 type을 포함하고 checksum을 제외한다. 전체 크기는 `length + 4`; checksum은 헤더부터 payload 끝까지 XOR이다.
- payload 길이: command `0x01=7`, legacy `0x81=7`, v2 `0x82=39`, bridge request `0x20=15`, bridge response `0xA0=14`.
- v2라는 이름과 wire `version`을 혼동하지 않는다. 현재 지원 version은 **1**이다.
- v2 고정부는 `<BBBBHBHH`(11 bytes), wheel은 `<BhHh`(7 bytes)이며 `11 + 4 × 7 = 39`를 검증한다.

| 전체 packet byte | 필드 | 해석 |
| --- | --- | --- |
| 3–9 | type, version, seq, state, system_error, validity | error는 uint16 LE |
| 10–13 | Driver A/B voltage | 각각 uint16 LE, 이미 mV이므로 추가 배율 적용 금지 |
| 14–20 | LF | Driver A/MOT1, validity bit 0 |
| 21–27 | RF | Driver B/MOT1, validity bit 1 |
| 28–34 | LR | Driver B/MOT2, validity bit 2 |
| 35–41 | RR | Driver A/MOT2, validity bit 3 |
| 42 | checksum | 앞선 42 bytes의 XOR |

- Wheel 필드는 raw status(uint8), rpm(int16), current_deci_amp(uint16), controller_output(int16)이다. 사용자 정정에 따라 controller_output은 signed로 해석하며 음수를 JSON까지 보존한다. 전류 표시 시에만 0.1 A 단위를 환산한다.
- Driver A/B 전압 validity는 bit 4/5다. Wheel freshness 500 ms, 전압 freshness 3 s 판단은 MCU가 수행한다. invalid인 값은 마지막 측정값일 수 있으며 현재 유효값으로 사용하면 안 된다.
- `system_error != 0`을 `FAULT`로 재해석하지 않는다. 최종 운용 상태는 MCU의 state를 따른다. bit 2는 sticky 구동/안전 TX 실패 또는 회복 가능한 polling TX 실패일 수 있어 이 bit만으로 원인을 단정하지 않는다.
- bit 12는 reserved이며 과거 `BC_INITIALIZATION_FAILURE`로 표시하지 않는다. legacy error와 v2 system_error의 의미는 동일하지 않으므로 단순 치환하지 않는다.
- status seq는 command ACK가 아니다. wrap 또는 중복 seq를 이유로 정상 상태나 tunnel frame을 버리지 않는다.

## 4. 구현 순서

### 단계 1 — 공통 파서와 v2 모델 (최우선)

대상: `src/my_car_motor_bridge/my_car_motor_bridge/protocol.py`, 새 `test/test_protocol.py`.

1. type별 기대 길이 상수와 dispatch 구조를 만들고 기존 command pack의 wire 호환을 유지한다.
2. `SystemStatusV2`, wheel telemetry 모델을 추가하고 offset, signed RPM, wire version을 검증한다. 미지원 version은 현재 모델로 발행하지 않고 진단 후 다음 packet을 처리한다.
3. 기존 `MotorStatus`의 legacy 필드를 유지한다. 공통 파서는 정상 packet을 소비하고 종류별 모델을 반환하며 노드가 발행 대상을 결정한다.
4. partial header, split read, 연속 packet, 잡음, 잘못된 길이/type/checksum에서 다음 `AA 55`로 복구한다. 불완전한 정상 후보는 보존하고, 오류 후보는 앞으로 진행하며, 마지막 단독 `AA`는 보존한다. 정상 payload 안의 `AA 55`를 새 packet으로 오인하지 않는다.
5. 미지원 type/잘못된 길이가 정상 후속 packet 처리를 막지 않도록 하고, 잡음으로 버퍼가 무한 증가하지 않게 한다.

완료 기준: README의 43-byte 예시를 그대로 decode하여 seq=16, A/B=24000/23800 mV, LF/RF/LR/RR RPM=100/-100/-80/80, checksum=0x5A를 확인한다. fixture의 기대값은 구현 serializer로 재생성하지 않는다.

### 단계 2 — control 상태 발행과 legacy fallback

대상: `motor_bridge_node.py`, 필요시 ROS 의존성 없는 상태 선택/JSON 변환 모듈.

1. 기본 모드를 `control`로 두고 `/cmd_vel` milli-unit 변환과 command timeout 동작을 유지한다.
2. `~/status`는 기존 `std_msgs/msg/String`을 유지한다. v2 JSON은 보고서의 `version`, `seq`, `state`, `system_error`, `validity`, `drivers.a/b`, `wheels.lf/rf/lr/rr` 구조와 필드명을 그대로 사용한다. 각 driver/wheel에 validity에서 계산한 `valid`를 넣는다.
3. 대표 배터리값은 기본 v2 필수 필드에 추가하지 않는다. 소비자가 필요로 하면 A/B가 모두 valid일 때만 min(A, B)를 쓰고 명시적인 유효성 표시를 함께 제공한다.
4. 연결별 상태를 `v2 대기 → legacy fallback 또는 v2 확정`으로 관리한다. 정상 v2는 대기시간 없이 즉시 발행하며 한 번 수신한 뒤에는 연결이 끊길 때까지 legacy 발행을 억제한다.
5. 계획상의 초기 설정은 `legacy_fallback_enabled=true`, `legacy_fallback_timeout_sec=1.0`이다. 성공적인 포트 연결부터 monotonic 시간으로 기다리고 v2를 못 받았을 때만 legacy를 발행한다. 대기 중 legacy를 받았더라도 오래된 값을 재발행하지 않고 만료 이후 새 legacy packet부터 발행한다. 이 시간은 MCU 명세값이 아닌 ROS 호환 정책이다.
6. fallback 중 v2가 오면 즉시 v2로 승격한다. v2 수신 중단만으로 legacy로 강등하지 않는다. 통신 무응답과 MCU telemetry invalid는 별도 현상으로 취급한다.
7. legacy fallback JSON은 기존 `seq/state/error/battery_mv`를 유지한다. legacy에는 없는 세부 telemetry나 validity를 만들어 넣지 않는다.

완료 기준: v2/legacy 혼합 stream에서 v2 확정 후 중복 발행이 없고, legacy 전용 장비에서는 대기시간 이후 기존 JSON이 나온다.

### 단계 3 — 재연결 및 전송 경계 처리

대상: `motor_bridge_node.py`, `serial_transport.py`.

1. 연결이 끊기거나 다시 열릴 때 partial RX buffer, v2 선택 여부, fallback 시작시간을 초기화한다. 재연결 전 command가 자동 재사용되지 않도록 마지막 Twist와 수신시각도 초기화한다.
2. pyserial write 반환 길이를 확인한다. short write/timeout은 성공으로 간주하지 않으며 연결 오류 처리와 재연결 경로로 보낸다.
3. read callback은 여러 packet을 처리하되 byte 또는 시간 예산을 둬 command timer를 굶기지 않는다. 현재 64 bytes × 100 Hz가 v2 430 bytes/s에는 충분하지만 CAN tunnel burst에도 충분하다고 가정하지 않는다.
4. 고정주기 명령, timeout disable, control 종료 시 disable/zero가 유지되는지 검증한다.

완료 기준: 이전 연결의 partial packet이 새 연결 packet과 결합되지 않고, 재연결된 구형 MCU에서도 fallback이 다시 동작한다. partial write를 성공으로 보고하지 않는다.

### 단계 4 — CAN bridge 모드

대상: `protocol.py`, `motor_bridge_node.py`, 설정/launch, 새 bridge 테스트.

1. `serial_mode`는 시작 시 `control|bridge`만 허용하고 잘못된 값은 포트 사용 전에 실패시킨다. 첫 구현은 실행 중 모드 변경을 지원하지 않으며 변경은 노드 재시작으로 한다.
2. bridge에서는 `/cmd_vel` subscriber와 command 송신 timer 및 `~/status` publisher를 만들지 않는다. read/reconnect 경로는 유지한다. 종료 시에도 `0x01`을 보내거나 전송을 위해 포트를 다시 열지 않는다.
3. ROS 진단 인터페이스는 초기안으로 `~/can_tx` 구독, `~/can_rx` 발행을 사용하고 둘 다 `std_msgs/msg/String` JSON으로 한다. 이는 원문에 없는 ROS 인터페이스 설계안이다.
   - TX: `can_id`, `dlc`, `data`(dlc개의 byte 배열), `timeout_ms`(기본 100, 허용 0..100). seq는 노드가 부여한다.
   - RX: `seq`, `status`, `can_id`, `dlc`, `data`(wire의 8-byte 배열). status 오류도 같은 토픽으로 전달한다.
4. `can_id=0..0x7FF`, `dlc=0..8`, 정수 byte=0..255 및 배열 길이를 검증한 뒤 `0x20`을 구성한다. padding은 0으로 채운다. 잘못된 JSON/필드는 로그와 함께 거부하고 노드는 계속 실행한다.
5. `0xA0`은 수신 순서대로 모두 전달한다. 반복 seq, 다른 driver/PID, 요청과 무관한 frame도 유지한다. status 1/2/3을 처리하며 `can_rx_timeout` 뒤에도 monitoring을 계속한다.
6. 로컬 `bridge` 설정만으로 MCU 모드가 바뀌지는 않는다. 첫 유효 `0x20`이 MCU를 BRIDGE로 전환한다. 임의 CAN frame을 시작/재연결 때 자동 송신하거나 이전 요청을 재전송하지 않는다. 첫 요청 이전 상태 packet은 발행하지 않는다.
7. MCU는 BRIDGE 진입 시 TQ-OFF 후 자동 제어/telemetry를 중단한다. BRIDGE 종료는 정상 `0x01` 또는 MCU 재부팅이며, CLI/ROS 노드 종료 자체는 종료 신호가 아니다. CONTROL 복귀 후 출력은 RC의 STOP/MANUAL/AUTO 선택에 따른다는 점을 문서화한다.
8. 기존 `can_cli.py`와 ROS 노드의 동일 포트 동시 사용을 피하도록 실행 절차를 기록한다. 별도 CLI 사용 시 ROS 노드를 먼저 종료한다.

완료 기준: bridge 실행부터 종료까지 ROS command `0x01`이 한 번도 송신되지 않고, 입력 요청만 `0x20`으로 나가며 연속 `0xA0`이 필터 없이 전달된다.

### 단계 5 — 설정, 문서, 소비자 확인

- `config/motor_bridge.yaml`에 모드 및 fallback 설정과 설명을 추가하고 launch에서 선택 가능하게 한다.
- motor bridge README를 v2 중심으로 갱신한다. 모드별 토픽/JSON, 구형 호환, 재연결, invalid 표시, MCU와 ROS 책임 및 실장 시험 한계를 기록한다.
- 웹은 현재 임의 JSON을 그대로 저장/표시하므로 먼저 v2와 legacy 양쪽 입력으로 기존 화면/API 호환을 검증한다. 전용 wheel 계기판은 별도 후속 범위로 둔다.
- 웹 README의 “normalized MCU serial packets” 설명을 실제 milli-unit 속도 전달 방식으로 정정한다.
- command converter는 현재 int16 범위만 제한하지만 MCU 기본 허용 범위는 v=±2000, w=±5000이다. 이번 전환에서는 기존 변환 정책을 유지하고 제한 밖 명령이 MCU에서 거부된다는 점을 문서화한다. ROS에서 제한/거부하는 정책 변경은 후속 항목으로 분리한다.

## 5. 검증 계획

| 검증 계층 | 필수 사례 | 합격 기준 |
| --- | --- | --- |
| 파서 단위 | README v2 fixture, 음수/경계 RPM, 서로 다른 네 wheel 값, legacy 단독 | offset·단위·부호·길이가 명세와 일치 |
| stream 복구 | 모든 split 위치, 단독 AA, 연속/혼합 packet, 잡음, type/length/checksum 오류, payload 내부 header | 정상 후속 packet 복구, 중복/무한 대기 없음 |
| 상태 의미 | 개별 validity, stale 값 잔존, bit 2와 non-FAULT state, reserved bit 12, 미지원 version | invalid 값 유효 취급 금지, state 임의 승격 없음 |
| 발행 정책 | timeout 전후 legacy, v2 즉시 수신, fallback 후 v2, v2 후 legacy/무응답 | 연결별 canonical 선택 규칙 준수 |
| 연결 경계 | partial RX 후 단절, 재연결 후 다른 firmware, short write/timeout | 버퍼 및 선택 상태 초기화, 실패 감지 |
| bridge | ID/DLC/data 경계, 오류 JSON, 반복 seq, 무관한 PID, timeout 후 정상 RX, 종료 | `0x01`/상태 발행 없음, tunnel 유지 |
| control 회귀 | milli-unit pack, seq wrap, cmd timeout, 종료 disable | 기존 command wire 및 정지 동작 유지 |
| 소비자 | v2 및 legacy를 웹 API/화면에 입력 | JSON 처리/표시 오류 없음 |

순수 Python 테스트에는 실제 MCU 없이 고정 fixture, fake transport, 제어 가능한 clock을 사용한다. 노드 통합 테스트는 ROS 환경에서 토픽과 실제 callback 흐름을 확인한다. 필요 테스트 의존성은 `package.xml`/패키지 설정에 함께 추가한다.

구현 후 검증 명령(ROS 환경을 source한 workspace 루트):

```bash
python3 -m pytest src/my_car_motor_bridge/test
colcon build --symlink-install --packages-select my_car_motor_bridge
source install/setup.bash
colcon test --packages-select my_car_motor_bridge
colcon test-result --verbose
```

실장 확인은 소프트웨어 검증 이후 별도로 수행한다. CONTROL에서 v2 약 10 Hz, 네 wheel 부호/배치, A/B 전압과 validity를 확인하고, 단절·복귀와 BRIDGE 진입/CONTROL 복귀를 확인한다. 전압 raw 단위, 방향 극성, polling 응답률과 freshness timeout 적정성은 문서에도 미검증 항목이므로 장비 확인 전 확정하지 않는다. 실제 주행 명령을 쓰는 검증은 정지 상태 확인 후 저속으로 진행한다.

## 6. 완료 조건과 범위

아래 항목은 고정 packet fixture, 가상 Serial, 실제 ROS 토픽 및 웹 API 검증 기준으로 완료했다. 실제 MCU 연결과 차량 동작 검증은 별도로 남아 있다.

- [x] 현재 MCU의 기본 `0x82`만으로 ROS 상태를 수신·발행한다.
- [x] JSON에 네 wheel과 두 driver 및 올바른 validity가 담긴다.
- [x] legacy fallback과 연결별 v2 우선 정책을 만족한다.
- [x] bridge가 control 송신과 분리되고 tunnel stream을 유지한다.
- [x] 재연결/손상 packet/partial write 검증과 control 회귀 검증을 통과한다.
- [x] 설정, 실행 예제, 웹 호환 검증 및 장비 시험 결과/미실행 항목을 기록한다.

작업 순서는 단계 1 → 2 → 3 → 4 → 5이며, 단계 1~3 완료 시 현재 MCU의 정상 주행 상태 연동을 먼저 검증할 수 있다. MCU firmware 수정, legacy 송신 재활성화, CAN polling 재구현, ACK 동기화, odometry 및 새 웹 계기판은 이번 구현 범위에 포함하지 않는다.
