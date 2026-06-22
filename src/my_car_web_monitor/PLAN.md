# my_car_web_monitor PLAN

This document tracks the intended implementation plan for `my_car_web_monitor`.

## Goal

Create a ROS2 package that serves a browser UI for monitoring Raspberry Pi Camera and ROS image topic streams, while publishing vehicle control commands to the existing `my_car_motor_bridge` package through `/cmd_vel`.

## Constraints And Assumptions

- Target ROS distribution: same workspace target as `my_car_motor_bridge`, currently assumed ROS2 Jazzy.
- Package type: `ament_python`.
- Primary control integration: publish `geometry_msgs/msg/Twist` to `/cmd_vel`.
- Motor feedback integration: subscribe to `/motor_bridge_node/status`.
- First deployment target: Raspberry Pi or vehicle-side computer.
- First network model: direct browser access over LAN or Tailscale.
- First operator model: one browser control session at a time.

## Phase 0: Package Skeleton

Deliverables:

- Create `src/my_car_web_monitor`.
- Add `README.md` and `PLAN.md`.
- Add package.xml, setup.py, setup.cfg, resource marker, and Python module directory.

Acceptance criteria:

- The package direction is documented.
- The intended ROS interfaces are explicit.
- The relationship to tmp/web_streaming2 and my_car_motor_bridge is clear.

Status: complete.

## Phase 1: Minimal ROS/Web Server

Deliverables:

- Add an `ament_python` package skeleton.
- Add `web_monitor_node` console script.
- Start a FastAPI app from a ROS2 node process.
- Serve a static `index.html`.
- Provide `/health`.
- Add clean shutdown handling.

Acceptance criteria:

- `colcon build --packages-select my_car_web_monitor` succeeds.
- `ros2 run my_car_web_monitor web_monitor_node` starts a web server.
- Browser can open the landing UI.

Risks:

- ROS2 executor and asyncio event loop integration needs care.
- Shutdown needs to stop both the web server and ROS node cleanly.

Status: implemented, pending colcon build verification in a ROS-enabled shell.

## Phase 2: ROS Topic Control

Deliverables:

- Add a control WebSocket endpoint.
- Convert browser commands to `geometry_msgs/msg/Twist`.
- Publish commands to configurable `/cmd_vel`.
- Add one-active-controller policy.
- Add zero-command on key release, disconnect, and timeout.
- Subscribe to configurable motor status topic.
- Forward latest motor status to the browser.

Acceptance criteria:

- Running `my_car_motor_bridge` receives `/cmd_vel` from the web package.
- UI can command forward, reverse, left, right, and stop.
- Disconnecting the browser sends zero velocity.
- `ros2 topic echo /cmd_vel` shows bounded commands.
- `ros2 topic echo /motor_bridge_node/status` remains compatible with the displayed status.

Safety requirements:

- Never keep publishing a nonzero command after the active browser disconnects.
- Clamp linear and angular speeds before publishing.
- Emergency stop must publish zero immediately.
- Keep my_car_motor_bridge.cmd_timeout_sec enabled as the lower-level safety net.

Status: implemented for /cmd_vel publishing, single active control socket, disconnect stop, watchdog stop, and motor status mirroring. Hardware/runtime verification is still pending.

## Phase 3: Camera Source Registry

Deliverables:

- Port the `tmp/web_streaming2` stream registry concept.
- Support configurable stream specs:
  - `stream_id:picamera2:camera_index`
  - `stream_id:ros_image:/topic/name`
  - `stream_id:ros_compressed:/topic/name`
  - `stream_id:synthetic`
- Add `/streams` endpoint.
- Keep a common frame source interface.

Acceptance criteria:

- Synthetic stream works without camera hardware.
- ROS raw image stream works for supported encodings.
- ROS compressed image stream works for JPEG/PNG compressed messages.
- Stream discovery returns stable stream metadata.

## Phase 4: WebRTC Streaming

Deliverables:

- Port/adapt the `aiortc` WebRTC publisher structure from `tmp/web_streaming2`.
- Add `/offer` signaling endpoint.
- Create one video track per selected stream.
- Serve browser JavaScript that starts/stops streams.

Acceptance criteria:

- Browser receives at least one synthetic stream.
- Browser receives Raspberry Pi Camera stream on hardware.
- Browser receives ROS image topic stream when the topic is active.
- Closing the browser releases peer connections.

Risks:

- Raspberry Pi Camera support depends on system-level `picamera2` and camera stack.
- ROS image conversion must avoid blocking the web event loop.
- Multiple simultaneous streams can overload LTE or Raspberry Pi CPU.

## Phase 5: Operator UI

Deliverables:

- Video grid with stream cards.
- Connection and ICE state display.
- Control panel with keyboard and button input.
- Emergency stop button.
- Motor status panel.
- Basic latency/last-frame-age indicators.

Acceptance criteria:

- Operator can identify active stream, control state, and motor status at a glance.
- UI remains usable on laptop browser and tablet-sized viewport.
- Keyboard focus loss sends stop.

## Phase 6: Launch And Deployment

Deliverables:

- Add launch file or documented run command with parameters.
- Add example stream configurations.
- Add Raspberry Pi dependency notes.
- Add Tailscale/LAN access notes.

Acceptance criteria:

- Fresh workspace user can build and run the package from README.
- Vehicle-side deployment command is documented.
- Development command with synthetic stream is documented.

## Phase 7: Hardening

Deliverables:

- Better telemetry: FPS, dropped frames, last command age, last frame age.
- Reconnect behavior for camera sources.
- Optional bitrate or resolution presets.
- Tests for command conversion and watchdog behavior.
- Basic lint/test integration.

Acceptance criteria:

- Nonzero command timeout behavior is covered by tests.
- Stream config parsing is covered by tests.
- Runtime errors surface clearly in UI/logs.

## Open Questions

- Should browser control publish directly to `/cmd_vel`, or should it publish to a package-specific topic such as `/web_cmd_vel` and use a mux later?
- Should this package depend directly on `aiortc`, or should WebRTC be optional for environments that only need HTTP snapshot/MJPEG fallback?
- Should motor status remain `std_msgs/msg/String` JSON, or should a typed status message be introduced later?
- Should arming be a browser UI state, a ROS parameter, or delegated to `my_car_motor_bridge`/MCU firmware?
- What are the exact camera topics and encodings on the target vehicle?

## Immediate Next Step

Run colcon build --packages-select my_car_web_monitor in a ROS2 shell, then test with CAMERA_SOURCE=synthetic ros2 run my_car_web_monitor web_monitor_node.

