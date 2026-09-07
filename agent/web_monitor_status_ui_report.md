# 웹 모니터 상태 UI 적용 결과

작성일: 2026-09-07

## 변경 사항

웹 모니터 상단에 차량 상태 패널을 추가했다.

- MCU의 운용 상태, 수신 경과 시간, seq, protocol 종류를 표시한다.
- Driver A/B 전압을 V로 표시하고 각 driver의 담당 모터를 명시한다.
- LF/RF, LR/RR 모터 카드를 실제 배치에 맞게 표시한다. 좁은 화면에서는 세로로 배치한다.
- 모터별 RPM, 전류(A), signed controller_output과 데이터 유효성을 표시한다.
- System Status v2와 legacy 오류 비트를 별도로 해석해 한국어 원인, bit 번호, mask, wire code와 설명을 표시한다.
- 모터의 8개 status bit를 각각 해석하고 여러 오류를 동시에 표시한다. 모터 오류 배지에 마우스를 올리면 상세 설명을 확인할 수 있다.
- CAN_TX_FAILURE 하나만으로 MCU 상태를 FAULT로 변경하지 않으며, system_error bit 12를 포함한 reserved bit는 예약 비트로 표시한다.
- 전체 시스템 비트와 원문 JSON은 접어서 확인할 수 있다.

## 조회와 데이터 유효성

`/ws/status` 전용 읽기 WebSocket으로 ROS 수신 메시지마다 즉시 전달한다. 초기 구현의 500 ms GET 폴링은 중간 상태를 생략하는 원인이 되어 제거했다. 조종 WebSocket을 열거나 주행 명령을 송신하지 않으므로 조종권 없이도 상태를 확인할 수 있다. `/control/status`는 수동 조회용 최신 snapshot API로 유지한다.

메시지 수신 경과 시간이 1 s를 초과하거나 상태 WebSocket 연결이 끊기면 수치 표시를 `—`로 바꾸고, 이전 MCU 상태 및 오류에는 마지막 수신 기록임을 표시한다. 브라우저 내부 250 ms timer가 경과 시간을 갱신하므로 응답이 멈춘 경우에도 이전 데이터가 계속 정상으로 보이지 않는다.

모터/전압은 JSON `valid`와 MCU validity bit가 모두 유효하며 예상 자료형/범위를 만족할 때 표시한다. 이 UI의 메시지 경과 시간 기준과 MCU의 wheel 500 ms / voltage 3 s freshness 기준은 구분한다.

## 변경 파일

- `src/my_car_web_monitor/my_car_web_monitor/web/static/index.html`
- `src/my_car_web_monitor/my_car_web_monitor/web/static/motor-status.js`
- `src/my_car_web_monitor/my_car_web_monitor/web/static/motor-status.css`
- `src/my_car_web_monitor/test/test_motor_status_ui.py`
- 웹 README, 테스트 의존성 설정 및 lockfile

## 검증

- 실제 headless Chromium: 8개 테스트 통과. HTML/CSS/JS는 실제 파일을 로드하고 HTTP 응답만 가상 데이터로 주입했다.
- 브라우저 검증: 단위와 음수 표시, 실제 wheel 배치, 모든 오류 bit 매핑, reserved/legacy 의미, 복수 모터 오류, invalid/stale/서버 단절 처리, 수신 대기, 미지원 형식, 로컬 시간 경과, 텍스트 삽입 안전성, 가로 넘침 없는 모바일 배치.
- 데스크톱 1440 px 및 모바일 390 px 화면을 캡처해 레이아웃을 확인했다. 테스트 환경에는 한국어 글꼴이 없어 캡처의 한글 glyph는 확인하지 못했으며, DOM의 한국어 문구와 실제 데이터 표시를 검증했다. 실제 브라우저는 시스템의 한국어 글꼴을 사용한다.
- 웹 패키지 빌드 성공. 설치된 package와 share 디렉터리에 JS/CSS 배포 확인.
- `colcon test`: 브라우저 주소를 지정한 최종 실행에서 API·stream·WebSocket·Chromium 테스트 총 15개 통과, 0 failures, 0 skipped. 브라우저 주소를 지정하지 않은 실행에서는 UI 테스트 8개가 skip된다.
- `git diff --check` 통과.

기존 license TOML 형식 및 FastAPI on_event에 대한 deprecation 경고는 남아 있다. MCU/차량 실장 시험은 수행하지 않았다.

## 반영

현재 workspace의 web 패키지는 재빌드했다. 상태 WebSocket 서버 경로가 추가되었으므로 실행 중인 web 노드를 재시작한 뒤 브라우저를 새로고침해야 한다. 이 작업에서 사용자의 실행 중인 조종 프로세스를 종료하거나 재시작하지 않았다.

## 10 Hz 상태 생략 회귀 수정

초기 UI가 HTTP 응답 후 500 ms를 기다린 뒤 최신 상태 하나만 읽어, 10 Hz 입력 중 여러 상태를 화면에서 생략했다. HTTP 응답 시간까지 더해 실제 갱신률은 2 Hz 이하였다. 이 현상은 웹 표시 경로에서 확인했으며 MCU→ROS Serial 손실을 측정한 결과는 아니다.

- executor thread의 각 ROS callback에서 파싱한 상태·원문·수신시각·수신번호를 묶어 보존하고 asyncio event loop로 전달한다.
- `/ws/status`는 각 클라이언트에 메시지를 순서대로 전달한다. 시퀀스 중복이나 wrap으로 메시지를 버리지 않는다.
- 클라이언트별 queue는 256개로 제한하고 overflow 횟수를 `stream_dropped_count`로 노출한다. 느린 클라이언트가 다른 클라이언트나 ROS callback을 막지 않는다.
- 초기 연결/재연결에는 최신 snapshot을 보내며 단절 중 과거 메시지를 재생하지 않는다.
- 상태가 없으면 1초마다 heartbeat를 보내지만 telemetry의 나이를 초기화하지 않는다.
- seq와 RPM 등이 변해도 오류/전체 bit DOM은 재사용하며, 원문 JSON은 펼친 경우만 렌더링한다.

회귀 검증: ROS executor 역할의 별도 thread에서 200개 burst를 두 구독자에게 순서대로 전달, WebSocket 경로에서 100개 burst 및 10 Hz 20개를 두 클라이언트에게 누락 없이 전달, 느린 구독자 overflow 진단 및 다른 구독자 격리, 관찰자 종료/재접속의 주행 명령 미발행을 확인했다. Chromium에서는 10 Hz 20개의 seq 연속성과 DOM 재사용을 확인했다. 실제 차량 통신 속도나 CPU 부하는 측정하지 않았다.
