# my_car_web_monitor

ROS2 package plan for a browser-based vehicle monitoring and control interface.

이 패키지는 `tmp/web_streaming2`의 FastAPI/WebRTC 기반 카메라 스트리밍 실험을 ROS2 패키지 형태로 정리하고, `src/my_car_motor_bridge/my_car_motor_bridge`의 모터 브리지 노드와 ROS 토픽으로 상호작용하는 웹 모니터링/제어 UI를 제공하는 것을 목표로 한다.

초기 목적은 다음 세 가지다.

- Raspberry Pi Camera 영상을 웹 브라우저에서 모니터링한다.
- ROS2 이미지 토픽(`sensor_msgs/msg/Image`, `sensor_msgs/msg/CompressedImage`)을 같은 웹 UI에서 모니터링한다.
- 브라우저 조작 입력을 `geometry_msgs/msg/Twist`로 `/cmd_vel`에 publish하여 `my_car_motor_bridge` 노드가 차량을 제어하도록 한다.

## Background

참고 구현인 `tmp/web_streaming2`는 다음 요소를 이미 실험했다.

- `FastAPI` 기반 HTTP/WebSocket 서버
- `aiortc` 기반 WebRTC video track
- Raspberry Pi Camera source
- ROS2 image/compressed image topic source
- 브라우저 기반 다중 스트림 UI
- WebSocket 조종 입력과 watchdog 정지 로직

이 패키지는 그 구조를 ROS2 워크스페이스 안에서 빌드/실행 가능한 형태로 옮기되, 차량 제어 경로는 직접 Teensy serial을 만지는 방식이 아니라 기존 `my_car_motor_bridge` ROS2 노드와 연동하는 방식으로 시작한다.

## Target Architecture

```text
[Browser UI]
  - camera monitor
  - stream selector
  - keyboard/button teleoperation
  - emergency stop
        |
        | HTTP/WebSocket + WebRTC
        v
[my_car_web_monitor]
  - FastAPI app
  - WebRTC signaling endpoint
  - camera source registry
  - ROS2 image subscribers
  - /cmd_vel publisher
  - motor status subscriber
        |
        | ROS2 topics
        v
[my_car_motor_bridge]
  - subscribes /cmd_vel
  - sends milli-unit physical velocity MCU serial packets
  - publishes /motor_bridge_node/status
```

## ROS Interfaces

Control interfaces:

- Publish: `/cmd_vel` (`geometry_msgs/msg/Twist`)
- Subscribe: `/motor_bridge_node/status` (`std_msgs/msg/String`)

Camera interfaces:

- Subscribe: configurable raw image topics (`sensor_msgs/msg/Image`)
- Subscribe: configurable compressed image topics (`sensor_msgs/msg/CompressedImage`)
- Optional direct Raspberry Pi Camera source through `picamera2`

The default control behavior should match `my_car_motor_bridge`:

- The web node publishes repeated `/cmd_vel` while a key/button is held.
- Releasing input publishes zero velocity.
- Losing the browser control socket publishes zero velocity.
- If no command arrives within the web watchdog interval, the web node publishes zero velocity.
- `my_car_motor_bridge` still keeps its own `cmd_timeout_sec` as the lower-level safety net.

## Proposed Package Shape

```text
src/my_car_web_monitor/
  README.md
  PLAN.md
  package.xml
  setup.py
  setup.cfg
  resource/
    my_car_web_monitor
  my_car_web_monitor/
    __init__.py
    web_monitor_node.py
    config.py
    ros_executor.py
    streams.py
    control.py
    sources/
      __init__.py
      base.py
      picamera2_source.py
      ros2_source.py
    streaming/
      __init__.py
      webrtc.py
    web/
      static/
        index.html
```

The package should be an `ament_python` package, matching `my_car_motor_bridge`.

## Initial Runtime Model

The first implementation should run as a single process:

```bash
ros2 run my_car_web_monitor web_monitor_node
```

The process will own both:

- a ROS2 node for topic subscription/publication
- an async web server for browser UI, WebSocket control, and WebRTC signaling

This keeps deployment simple on Raspberry Pi. If executor/web-loop integration becomes unstable, the ROS2 node can later be split into a separate process and connected to the web app through ROS topics or an internal queue.

## Configuration Draft

Expected ROS parameters or environment-backed settings:

- `host`: web bind host, default `0.0.0.0`
- `port`: web port, default `8443`
- `camera_width`: default `1280`
- `camera_height`: default `720`
- `camera_fps`: default `15`
- `webrtc_packet_max`: encoded RTP payload limit, default `1100` for tunnel MTU safety
- `camera_streams`: stream list such as `front:picamera2:0,debug:ros_image:/camera/image_raw`
- `cmd_vel_topic`: default `/cmd_vel`
- `motor_status_topic`: default `/motor_bridge_node/status`
- `control_linear_speed`: default `0.3`
- `control_angular_speed`: default `1.0`
- `control_publish_rate_hz`: default `20.0`
- `control_watchdog_timeout_sec`: default `0.3`

## Runtime Dependencies

This package needs both ROS2 Python modules and web streaming Python modules at runtime. A successful colcon build does not automatically prove those modules are available in the shell that runs ros2 run.

ROS-side modules are provided by a sourced ROS2 environment: rclpy, geometry_msgs, std_msgs, and sensor_msgs. Web/media Python dependencies are declared in pyproject.toml and are installed with uv sync. ROS image conversion uses NumPy and Pillow; cv_bridge is not required.

Do not install web/media dependencies into the global system Python. On modern Ubuntu and Raspberry Pi OS systems, global pip installs are commonly blocked by PEP 668 and should be avoided anyway.

Recommended uv setup:

    cd src/my_car_web_monitor
    # Run this from a shell where your ROS2 distribution has already been sourced.
    uv venv --python /usr/bin/python3 --system-site-packages
    source .venv/bin/activate
    uv sync --active

The --python /usr/bin/python3 and --system-site-packages options are both intentional. ROS2 Python modules such as rclpy, geometry_msgs, and std_msgs are normally installed by the ROS distribution for the system Python ABI. The local uv venv must use that same Python interpreter and must have visibility into those ROS packages, while keeping the web/media packages isolated from the global environment. Do not let uv create this venv with a managed Python such as Python 3.14, because rclpy binary extensions will not match that ABI.

Build and run from the workspace with the same activated uv venv and sourced ROS2 environment:

    cd ../..
    python -m colcon build --packages-select my_car_web_monitor
    source install/setup.bash
    CAMERA_SOURCE=synthetic ros2 run my_car_web_monitor web_monitor_node

Use python -m colcon, not plain colcon, when relying on the uv venv. The generated ROS console script records the Python interpreter used during the build. If plain colcon resolves outside the active venv, the installed script can end up with a system Python shebang and then fail to import modules installed in .venv, such as uvicorn.

A quick check after rebuilding:

    head -1 install/my_car_web_monitor/lib/my_car_web_monitor/web_monitor_node

The first line should point at the uv venv Python, not a system Python outside the venv.

If rclpy fails with a message mentioning a path like _rclpy_pybind11.cpython-314-*.so, the uv venv was created with the wrong Python version. Recreate the venv with uv venv --python python3 --system-site-packages, then rebuild this package with python -m colcon build.

Raspberry Pi Camera direct mode also needs Picamera2 from the Raspberry Pi OS/system camera stack. It is intentionally not declared as a PyPI dependency because it is normally installed as a system package on Raspberry Pi.

## Development Notes

- Keep video transport and vehicle control transport logically separate.
- Prefer WebRTC for low-latency browser video.
- Keep `/cmd_vel` as the main integration point with `my_car_motor_bridge`.
- Treat emergency stop and disconnect handling as first-class behavior, not UI extras.
- Start with one operator and one vehicle.
- Do not expose the service publicly by default; assume local network or Tailscale access.

## Current Status

Initial implementation exists.

- ament_python package skeleton is present.
- web_monitor_node console script is defined.
- FastAPI serves the browser UI and health endpoint.
- WebRTC streaming supports direct picamera2, synthetic, ROS raw image and ROS compressed image sources.
- Browser control WebSocket publishes geometry_msgs/msg/Twist to /cmd_vel.
- Motor status is read from /motor_bridge_node/status.
- ROS image subscriptions share the existing web node and executor and start/stop with stream viewers.

Development run without Raspberry Pi Camera hardware:

    CAMERA_SOURCE=synthetic ros2 run my_car_web_monitor web_monitor_node

Raspberry Pi Camera direct run:

    ros2 run my_car_web_monitor web_monitor_node

Multiple direct camera stream example:

    CAMERA_STREAMS=front:picamera2:0,rear:picamera2:1 ros2 run my_car_web_monitor web_monitor_node

## ROS image streams (T265 Fisheye)

Register ROS sources in `CAMERA_STREAMS`, using the same stream IDs and browser start/stop controls as Picamera2. A nonempty `CAMERA_STREAMS` overrides `CAMERA_SOURCE`. Source types are `picamera2`, `synthetic`, `ros_image`, and `ros_compressed`; IDs must be unique, and ROS sources require a topic name.

After sourcing the sensor workspace, run the T265 driver in a separate terminal:

```bash
ros2 launch realsense2_camera rs_launch.py device_type:=t265 enable_fisheye1:=true enable_fisheye2:=true
```

Run `ros2 topic list -t` in another sourced terminal while the driver runs. Adjust the following topic names if the driver's namespace differs. From the vehicle workspace root, with its ROS environment and web venv active:

```bash
CAMERA_STREAMS=left:ros_image:/camera/fisheye1/image_raw,right:ros_image:/camera/fisheye2/image_raw \
  ros2 run my_car_web_monitor web_monitor_node

# Alternatively, include a directly attached Pi camera:
CAMERA_STREAMS=front:picamera2:0,left:ros_image:/camera/fisheye1/image_raw \
  ros2 run my_car_web_monitor web_monitor_node
```

| Source type | ROS message | Example |
| --- | --- | --- |
| `ros_image` | `sensor_msgs/msg/Image` | `left:ros_image:/camera/fisheye1/image_raw` |
| `ros_compressed` | `sensor_msgs/msg/CompressedImage` | `left:ros_compressed:/camera/fisheye1/image_raw/compressed` |

Compressed streams require an existing compressed-image publisher. The web node does not create an image_transport republisher. Raw encodings supported are `mono8` (T265 Fisheye), `8UC1`, `rgb8`, `bgr8`, `rgba8`, and `bgra8`. Row padding is respected and grayscale is converted to RGB for the existing WebRTC track. Compressed JPEG/PNG images are supported; depth images, compressedDepth and other raw encodings are rejected with throttled warnings. A malformed image is skipped and a later valid image resumes streaming.

ROS sources implement the same `FrameSource.start/stop/read` interface as direct cameras. The registry passes the existing ROS node to each source. The first viewer creates the subscription; the last viewer disconnecting or server shutdown destroys it. Restarting waits for a new image, and callbacks from a retired subscription are ignored. `/streams` exposes the topic as `device`, so the existing UI displays it without special camera controls.

Subscriptions use best-effort, volatile, keep-last depth 1 QoS. The ROS callback stores only the latest message under a thread lock; it does not decode images or enqueue work into the web event loop. Each viewer samples the latest image independently at `CAMERA_FPS`, and decoded pixels are cached across viewers. Faster incoming images are dropped between samples, rather than building a delayed playback queue. `CAMERA_WIDTH` and `CAMERA_HEIGHT` do not resize ROS images: the published resolution is preserved. WebRTC timestamps follow the output FPS, not the ROS header clock, so bag replay clock changes do not rewind video timestamps.

Before the first valid image, video waits without blocking the web server. If publication stops after a valid image, the last image is repeated until a new one arrives; there is currently no camera-staleness indicator. Use `ros2 topic hz /camera/fisheye1/image_raw` to check reception when the picture appears frozen.

After updating, rebuild with the active web venv and restart the web node:

```bash
python -m colcon build --symlink-install --packages-select my_car_web_monitor
source install/local_setup.bash
python -m pytest src/my_car_web_monitor/test/test_ros_image_source.py
```

Tests cover padded grayscale/color images, JPEG/PNG, invalid-frame recovery, cross-thread bursts, multiple readers, stop/restart, stream discovery, and real ROS best-effort/reliable publishers feeding a WebRTC video track without sensor hardware. A local WebRTC test also negotiates two viewers, receives encoded video, and checks subscription cleanup when the last peer closes; it needs local network interface/socket access.

## Motor status compatibility

The motor bridge publishes System Status v2 as nested JSON (`drivers`, `wheels`, and per-value `valid`) on `/motor_bridge_node/status`. Legacy fallback retains `seq/state/error/battery_mv`. The web monitor preserves either JSON schema and presents a vehicle status dashboard above the camera/control panels. LF/RF and LR/RR cards follow the physical wheel layout, with Driver A mapped to LF/RR and Driver B to RF/LR. Each card shows RPM, current in amperes, signed controller output, validity and decoded motor error bits. System errors show Korean labels, bit numbers, masks, wire names and explanations; all 16 system bits and the original JSON are available in expandable details. Check each `valid` flag before treating telemetry as a current measurement, and also check `motor_status_age_sec` for loss of status traffic. CAN bridge mode does not publish motor status; previously received values will age rather than update.

Status API compatibility tests (workspace root, with the web virtual environment and sourced ROS dependencies):

```bash
src/my_car_web_monitor/.venv/bin/python -m pytest src/my_car_web_monitor/test/test_motor_status_api.py
```

## Vehicle status dashboard

- MCU state and Driver A/B voltage appear above the four motor cards. Voltage is shown in V and current in A; RPM and controller output keep their signs.
- System Status v2 and legacy errors have separate mappings. CAN_TX_FAILURE alone does not turn the displayed MCU state into FAULT. Reserved bits (including bit 12) are labelled as reserved.
- Each motor decodes ALARM, CTRL_FAIL, OVER_VOLT, OVER_TEMP, OVER_LOAD, HALL_FAIL, INV_VEL and STALL. Multiple active errors appear together; hover over a motor error for its bit/mask and explanation.
- A read-only `/ws/status` WebSocket starts automatically. Each ROS status callback is forwarded as its own immutable message in arrival order; there is no timer sampling of the latest value. It does not open the control WebSocket or send velocity commands, so multiple spectators can monitor without taking the control session. `/control/status` remains a latest-snapshot debugging API.
- A 250 ms browser timer ages the last received snapshot even if status messages stop. If message age exceeds 1 s or the status socket closes, measurements become `—` and state/errors are marked as last received. The server sends a heartbeat after 1 s without status, the browser reconnects if no message/heartbeat arrives for 3 s, and a closed socket retries after 1 s. Heartbeats never reset the age of telemetry. This UI threshold is distinct from MCU wheel/voltage freshness (500 ms / 3 s).
- A value is displayed only when both the MCU validity bit and its JSON `valid` flag permit it, the packet is recent, and its numeric fields have the expected types/ranges. Invalid old values remain accessible in the raw message section. Legacy messages show their representative battery voltage and identify per-motor information as unavailable.
- Each client has a bounded 256-message queue. ROS callbacks never wait for a slow browser. If that queue overflows, the oldest queued message is removed and `stream_dropped_count` is explicitly shown in the UI. `motor_status_received_count` counts ROS messages seen by the web process and is displayed separately from the MCU seq. It does not prove whether packets were lost before ROS. A reconnect begins with the latest snapshot; disconnected history is not replayed.
- The UI updates numeric values on every status frame while reusing error/bit DOM nodes until those flags change. Number formatters are cached, and raw JSON is rendered only when expanded.
- The layout stacks for narrow screens. Korean labels use the browser's available Korean fonts.

Browser regression tests use actual page HTML/CSS/JS in Chromium with mocked read-only responses; no MCU, camera or ROS commands are used. With Chromium running locally at debug port 9229, run:

```bash
MOTOR_UI_BROWSER_URL=http://127.0.0.1:9229 src/my_car_web_monitor/.venv/bin/python -m pytest src/my_car_web_monitor/test/test_motor_status_ui.py
```

Without `MOTOR_UI_BROWSER_URL`, browser tests skip. API tests run independently. Rebuild the web package and restart the web monitor after updating its installed static assets; reload the browser page.
