# my_car_motor_bridge

ROS 2 `ament_python` package for Wheel-Dragoon. In `control` mode it sends `/cmd_vel` as milli-unit velocity commands and publishes System Status v2. In `bridge` mode it exposes the MCU's raw CAN diagnostic tunnel.

The MCU owns skid-steer mixing, MD200T command/polling scheduling, telemetry freshness, and low-level stop/fault handling. The ROS node forwards physical velocity commands and reports the MCU's state. It does not compute wheel targets or implement driver control loops.

## Modes and ROS interfaces

| Mode | Subscriptions | Publications | Serial traffic |
| --- | --- | --- | --- |
| `control` (default) | `/cmd_vel` (`geometry_msgs/msg/Twist`) | `~/status` (`std_msgs/msg/String`, JSON) | Send `0x01`, receive `0x82` or legacy `0x81` |
| `bridge` | `~/can_tx` (`std_msgs/msg/String`, JSON) | `~/can_rx` (`std_msgs/msg/String`, JSON) | Send requested `0x20`, receive continuous `0xA0` |

All parameters are startup-only. Restart the node to change modes or settings. Bridge mode creates no command subscription, command timer or status publisher and sends no `0x01`, including on shutdown.

The ROS mode selects local behavior. The first valid `0x20` actually switches the MCU to BRIDGE. Before that request, received `0x81`/`0x82` packets are consumed without publication. The MCU sends TQ-OFF on BRIDGE entry and stops automatic commands, polling and status output. Raw CAN commands can subsequently operate the drivers directly.

Closing the bridge node or CLI leaves the MCU in BRIDGE. A valid `0x01` or MCU reboot returns it to CONTROL. A control node normally sends zero/disable while waiting for `/cmd_vel`; that packet also returns the MCU to CONTROL. The RC STOP/MANUAL/AUTO selection then determines the output source: ROS velocity is applied only in AUTO. In particular, CONTROL entry is not an unconditional stop when RC selects MANUAL.

Use one process per serial port. Stop this node before running Wheel-Dragoon's `can_cli.py` on the same port.

## Packet framing

All packets use `AA 55 length payload checksum`. Length includes type and excludes checksum. Total packet size is `length + 4`. Checksum is the XOR of every preceding byte including the header. Multi-byte integers use little endian.

| Type | Payload bytes | Total bytes | Purpose |
| --- | --- | --- | --- |
| `0x01` | 7 | 11 | Velocity command / enter CONTROL |
| `0x81` | 7 | 11 | Legacy basic status; disabled by default in current MCU |
| `0x82` | 39 | 43 | System Status v2, 10 Hz in CONTROL |
| `0x20` | 15 | 19 | Raw CAN request / enter BRIDGE |
| `0xA0` | 14 | 18 | Continuous CAN frame or request status |

The stream parser handles split headers/packets, multiple packets per read, noise and checksum/type/length errors. Unsupported v2 versions are consumed with a warning; only wire version **1** is decoded. Status sequences wrap at 255 and are not command acknowledgements. Neither status nor tunnel packets are filtered by sequence.

### Command `0x01`

```text
AA 55 07 01 seq v_lo v_hi w_lo w_hi flags checksum
```

`v_cmd` and `w_cmd` are signed int16: `linear.x * 1000` in milli m/s and `angular.z * 1000` in milli rad/s. For example 0.1 m/s becomes 100, and -0.5 rad/s becomes -500. Flags: bit 0 enable, bit 1 emergency stop. The existing converter saturates at int16 limits; the current MCU separately accepts v=±2000 and w=±5000 and rejects commands outside those limits. Non-finite velocity input is discarded and clears the latest command.

When no command arrives within `cmd_timeout_sec`, control mode sends zero/disable if `send_zero_when_timeout` is true. Command ages use monotonic time, independent of ROS simulation time. Control shutdown attempts a final zero/disable. MCU timeout and fault handling remain responsible for serial loss and low-level safety.

### System Status v2 `0x82`

| Packet bytes | Field | Format |
| --- | --- | --- |
| 3 | type | `0x82` |
| 4 | version | `1` (despite the v2 packet name) |
| 5 | seq | uint8 |
| 6 | state | uint8, MCU final state |
| 7–8 | system_error | uint16 |
| 9 | validity | uint8 |
| 10–11 | Driver A voltage | uint16 mV |
| 12–13 | Driver B voltage | uint16 mV |
| 14–20 | LF | Driver A/MOT1 |
| 21–27 | RF | Driver B/MOT1 |
| 28–34 | LR | Driver B/MOT2 |
| 35–41 | RR | Driver A/MOT2 |
| 42 | checksum | XOR of bytes 0–41 |

The fixed payload portion is `<BBBBHBHH` (11 bytes). Each wheel is `<BhHh` (7 bytes): raw status (uint8), signed RPM (int16), current in 0.1 A (uint16), signed controller output (int16, -32768..32767). Negative controller output is preserved in Python and JSON. Driver voltages are already mV; no additional scale factor is applied.

`~/status` JSON has this shape:

```json
{
  "version": 1, "seq": 16, "state": 1, "system_error": 0, "validity": 63,
  "drivers": {
    "a": {"voltage_mv": 24000, "valid": true},
    "b": {"voltage_mv": 23800, "valid": true}
  },
  "wheels": {
    "lf": {"status": 0, "rpm": 100, "current_deci_amp": 123, "controller_output": 500, "valid": true},
    "rf": {"status": 0, "rpm": -100, "current_deci_amp": 120, "controller_output": 490, "valid": true},
    "lr": {"status": 0, "rpm": -80, "current_deci_amp": 110, "controller_output": 450, "valid": true},
    "rr": {"status": 0, "rpm": 80, "current_deci_amp": 115, "controller_output": 460, "valid": true}
  }
}
```

Validity bits 0–3 correspond to LF/RF/LR/RR; bits 4–5 to A/B voltage. The MCU marks wheel telemetry fresh for 500 ms and voltages for 3 s. Invalid fields can retain old measurements: always check `valid` before using a value. The ROS node preserves these raw values and does not invent a fresh measurement or overwrite them with zero. The snapshot's age is separate from its MCU validity bits; consumers must also track whether status packets are still arriving. The web monitor exposes `motor_status_age_sec` for this purpose.

No representative battery field is added to v2. Consumers needing one must use min(A, B) only while both voltages are valid, and otherwise mark it invalid.

MCU states: 0 DISABLED, 1 ENABLED, 2 TIMEOUT_STOP, 3 ESTOP, 4 FAULT, 5 BOOTING, 6 CALIBRATION. `system_error != 0` does not imply FAULT. Bit 2 CAN_TX_FAILURE can indicate a sticky drive/safety TX fault or a recoverable telemetry polling failure. The node preserves the MCU's final state rather than inferring a fault from this bit. Bit 12 is reserved, not BC_INITIALIZATION_FAILURE. Wheel raw status and system_error are separate fields; legacy error bit definitions must not be applied to system_error.

### Legacy fallback

The old packet remains `AA 55 07 81 seq state error_lo error_hi batt_lo batt_hi checksum`. Its JSON remains exactly `seq`, `state`, `error`, `battery_mv`.

Each successful serial connection starts a `legacy_fallback_timeout_sec` wait (default 1 s). V2 publishes immediately on receipt and becomes canonical for that connection. Legacy publishes only if fallback is enabled, the wait has elapsed, and no supported v2 has arrived. Legacy packets received during the wait are not cached for delayed publication. Fallback switches immediately to v2 once v2 arrives. A v2 silence does not cause a downgrade; reconnect starts selection again.

### CAN diagnostic JSON

Publish a request to `~/can_tx`:

```json
{"can_id": 1, "dlc": 2, "data": [4, 143], "timeout_ms": 100}
```

`can_id` must be an integer 0..2047; `dlc` 0..8; `data` exactly dlc integer bytes 0..255; optional `timeout_ms` 0..100 (default 100). Other fields, including user-supplied seq, are rejected. The node assigns seq and pads the wire data to 8 bytes with zero. The example sends PID 4 requesting PID 143; for an MDROBOT DLC-8 request use `[4,143,0,0,0,0,0,0]` and `dlc:8`.

`~/can_rx` publishes every valid `0xA0` in order:

```json
{"seq": 0, "status": 0, "can_id": 1793, "dlc": 3, "data": [143, 240, 0, 0, 0, 0, 0, 0]}
```

RX data always contains 8 wire bytes; only the first dlc bytes are CAN data. Status values: 0 ok, 1 can_tx_failed, 2 can_rx_timeout, 3 invalid_request. A timeout is a request notification and does not stop monitoring. Repeated seq and unrelated IDs/PIDs are retained. The first response is not guaranteed to match the request; higher-level diagnostics must classify by CAN ID/PID as appropriate. MCU buffer pressure may drop tunnel frames, so this interface is not a lossless recorder.

Wire layouts:

```text
AA 55 0F 20 seq id_lo id_hi dlc d0 d1 d2 d3 d4 d5 d6 d7 timeout_lo timeout_hi checksum
AA 55 0E A0 seq status id_lo id_hi dlc d0 d1 d2 d3 d4 d5 d6 d7 checksum
```

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `serial_mode` | `control` | `control` or `bridge`, restart to change |
| `port` | `/dev/ttyACM0` | Serial device |
| `baudrate` | `115200` | Positive baud rate |
| `send_rate_hz` | `50.0` | Positive control command rate |
| `read_rate_hz` | `100.0` | Positive receive polling rate |
| `cmd_timeout_sec` | `0.3` | Positive command timeout |
| `enable_on_start` | `true` | Enable flag for received velocity commands |
| `send_zero_when_timeout` | `true` | Send zero/disable on missing/expired commands in control |
| `legacy_fallback_enabled` | `true` | Allow legacy-only firmware in control |
| `legacy_fallback_timeout_sec` | `1.0` | Nonnegative wait from connection before legacy fallback |

## Reconnection and serial load

Read/write errors and short writes close the port and reset the connection's parser, v2 selection and last velocity command. Reconnect is retried at most once per second. A fresh `/cmd_vel` is needed after reconnect. CAN requests submitted while disconnected are dropped; startup and reconnect never replay requests or send an automatic bridge request.

Each read callback drains up to 4096 bytes in 512-byte chunks. This bounds callback work while accommodating bursts. Default v2 traffic is 43 bytes × 10 Hz = 430 bytes/s (about 3.73% of 115200 baud with 8N1). Command traffic at 50 Hz brings the simple combined total to about 8.5%.

## Build, run and test

Use a sourced ROS environment with rclpy, geometry_msgs, std_msgs, rcl_interfaces, pyserial, PyYAML and pytest available for the same Python interpreter.

```bash
cd /home/jina/car_ws/ros2_ws
colcon build --symlink-install --packages-select my_car_motor_bridge
source install/setup.bash
ros2 launch my_car_motor_bridge motor_bridge.launch.py
# In another sourced shell:
ros2 topic echo /motor_bridge_node/status
```

Bridge launch and a Driver A voltage request (after verifying the intended device):

```bash
ros2 launch my_car_motor_bridge motor_bridge.launch.py serial_mode:=bridge
# In another sourced shell:
ros2 topic echo /motor_bridge_node/can_rx
ros2 topic pub --once /motor_bridge_node/can_tx std_msgs/msg/String \
  "{data: '{\"can_id\":1,\"dlc\":8,\"data\":[4,143,0,0,0,0,0,0],\"timeout_ms\":100}'}"
```

Launch argument defaults come from the YAML file; explicitly supplied arguments override them. Fallback settings are launch arguments too:

```bash
ros2 launch my_car_motor_bridge motor_bridge.launch.py legacy_fallback_enabled:=false
```

Tests use golden packets and fake serial devices; the node tests exercise real ROS entities/topics without hardware. Preserve the sourced environment's PYTHONPATH when running:

```bash
python3 -m pytest src/my_car_motor_bridge/test
colcon test --packages-select my_car_motor_bridge
colcon test-result --verbose
```

Hardware verification remains separate: wheel direction/polarity and diagonal mapping, actual voltage units, polling response rate/freshness, disconnect recovery, and MCU BRIDGE/CONTROL transitions require the real vehicle. These are not established by the software tests.

Source protocol references: Wheel-Dragoon `README.md` and `agent/ros2_update_report.md`. ROS implementation plan: `agent/plan/ros2_motor_bridge_update_plan.md` in the workspace root.
