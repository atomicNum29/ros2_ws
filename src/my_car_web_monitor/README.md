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
  - sends normalized MCU serial packets
  - publishes /motor_bridge_node/status
```

## ROS Interfaces

Planned control interfaces:

- Publish: `/cmd_vel` (`geometry_msgs/msg/Twist`)
- Subscribe: `/motor_bridge_node/status` (`std_msgs/msg/String`)

Planned camera interfaces:

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
- `camera_streams`: stream list such as `front:picamera2:0,debug:ros_image:/camera/image_raw`
- `cmd_vel_topic`: default `/cmd_vel`
- `motor_status_topic`: default `/motor_bridge_node/status`
- `control_linear_speed`: default `0.3`
- `control_angular_speed`: default `1.0`
- `control_publish_rate_hz`: default `20.0`
- `control_watchdog_timeout_sec`: default `0.3`

## Runtime Dependencies

This package needs both ROS2 Python modules and web streaming Python modules at runtime. A successful colcon build does not automatically prove those modules are available in the shell that runs ros2 run.

ROS-side modules are provided by a sourced ROS2 environment: rclpy, geometry_msgs, and std_msgs. Web/media Python dependencies are declared in pyproject.toml and are installed with uv sync.

Do not install web/media dependencies into the global system Python. On modern Ubuntu and Raspberry Pi OS systems, global pip installs are commonly blocked by PEP 668 and should be avoided anyway.

Recommended uv setup:

    cd src/my_car_web_monitor
    # Run this from a shell where your ROS2 distribution has already been sourced.
    uv venv --python python3 --system-site-packages
    source .venv/bin/activate
    uv sync --active

The --python python3 and --system-site-packages options are both intentional. ROS2 Python modules such as rclpy, geometry_msgs, and std_msgs are normally installed by the ROS distribution for the system Python ABI. The local uv venv must use that same Python interpreter and must have visibility into those ROS packages, while keeping the web/media packages isolated from the global environment. Do not let uv create this venv with a managed Python such as Python 3.14, because rclpy binary extensions will not match that ABI.

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
- WebRTC streaming supports direct picamera2 and synthetic sources.
- Browser control WebSocket publishes geometry_msgs/msg/Twist to /cmd_vel.
- Motor status is read from /motor_bridge_node/status.
- ROS image topic camera sources are still planned for the next implementation phase.

Development run without Raspberry Pi Camera hardware:

    CAMERA_SOURCE=synthetic ros2 run my_car_web_monitor web_monitor_node

Raspberry Pi Camera direct run:

    ros2 run my_car_web_monitor web_monitor_node

Multiple direct camera stream example:

    CAMERA_STREAMS=front:picamera2:0,rear:picamera2:1 ros2 run my_car_web_monitor web_monitor_node

