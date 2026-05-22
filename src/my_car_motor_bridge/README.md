# my_car_motor_bridge

`my_car_motor_bridge` is a ROS2 Jazzy `ament_python` package that bridges ROS `/cmd_vel` commands to an MCU serial protocol for a 4WD skid-steer UGV.

The ROS node does not implement vehicle kinematics, kick-start, minimum PWM, ramp limiting, per-wheel gain, or motor watchdog logic. It normalizes `/cmd_vel` into `v_cmd` and `w_cmd`, sends those values to the MCU, receives MCU status packets, and publishes status as ROS text.

## Package Structure

```text
my_car_motor_bridge/
├── package.xml
├── setup.py
├── setup.cfg
├── README.md
├── resource/
│   └── my_car_motor_bridge
├── launch/
│   └── motor_bridge.launch.py
├── config/
│   └── motor_bridge.yaml
└── my_car_motor_bridge/
    ├── __init__.py
    ├── motor_bridge_node.py
    ├── serial_transport.py
    ├── protocol.py
    └── command_converter.py
```

## Responsibility Split

ROS node responsibilities:

- Subscribe to `/cmd_vel`.
- Convert `linear.x` and `angular.z` into normalized integer commands from `-1000` to `+1000`.
- Send command packets to the MCU at a fixed rate.
- Receive status packets from the MCU.
- Publish parsed status to `~/status` as `std_msgs/msg/String`.
- Send disable/zero commands on command timeout and shutdown.
- Reconnect when the serial port is closed.

MCU responsibilities:

- Convert normalized `v_cmd` and `w_cmd` into wheel-level commands.
- Implement skid-steer motor mixing.
- Implement kick-start, ramp limiting, minimum PWM, per-wheel gain, and motor watchdog behavior.
- Enforce low-level timeout and safety behavior.

## Packet Protocol

All packets start with `0xAA 0x55`. The checksum is an XOR of every byte from byte 0 through the byte immediately before the checksum.

### Command Packet: ROS to MCU

| Byte | Field | Type | Description |
| --- | --- | --- | --- |
| 0 | header[0] | uint8 | `0xAA` |
| 1 | header[1] | uint8 | `0x55` |
| 2 | length | uint8 | `7` |
| 3 | type | uint8 | `0x01` |
| 4 | seq | uint8 | Sequence counter |
| 5-6 | v_cmd | int16 LE | Normalized linear command, `-1000` to `+1000` |
| 7-8 | w_cmd | int16 LE | Normalized angular command, `-1000` to `+1000` |
| 9 | flags | uint8 | bit 0: enable, bit 1: emergency_stop |
| 10 | checksum | uint8 | XOR checksum |

Payload is `type + seq + v_cmd + w_cmd + flags`, length `7`.

### Status Packet: MCU to ROS

| Byte | Field | Type | Description |
| --- | --- | --- | --- |
| 0 | header[0] | uint8 | `0xAA` |
| 1 | header[1] | uint8 | `0x55` |
| 2 | length | uint8 | `7` |
| 3 | type | uint8 | `0x81` |
| 4 | seq | uint8 | Sequence counter |
| 5 | state | uint8 | MCU state |
| 6-7 | error | uint16 LE | MCU error bitfield/code |
| 8-9 | battery_mv | uint16 LE | Battery voltage in millivolts |
| 10 | checksum | uint8 | XOR checksum |

Payload is `type + seq + state + error + battery_mv`, length `7`.

## Parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `port` | `/dev/ttyACM0` | Serial device path |
| `baudrate` | `115200` | Serial baud rate |
| `max_linear_x` | `0.5` | `linear.x` value mapped to `v_cmd=1000` |
| `max_angular_z` | `1.5` | `angular.z` value mapped to `w_cmd=1000` |
| `send_rate_hz` | `50.0` | Command packet send rate |
| `read_rate_hz` | `100.0` | Serial read polling rate |
| `cmd_timeout_sec` | `0.3` | Timeout after the latest `/cmd_vel` |
| `enable_on_start` | `true` | Enable flag for valid commands |
| `send_zero_when_timeout` | `true` | Send zero/disable packets after command timeout |

## Build

```bash
cd ~/ros2_ws
colcon build --symlink-install --packages-select my_car_motor_bridge
source install/setup.bash
```

## Launch

```bash
ros2 launch my_car_motor_bridge motor_bridge.launch.py
```

## Test Commands

Forward:

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}, angular: {z: 0.0}}" -r 10
```

Reverse:

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: -0.1}, angular: {z: 0.0}}" -r 10
```

In-place rotation:

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.5}}" -r 10
```

Status:

```bash
ros2 topic echo /motor_bridge_node/status
```

## Timeout Behavior

If no `/cmd_vel` message is received within `cmd_timeout_sec`, the node sends a command packet with `v_cmd=0`, `w_cmd=0`, and `enable=false` when `send_zero_when_timeout` is enabled. On shutdown, the node also attempts to send a final disable/zero command.

Both ROS and MCU firmware should have timeout safety. ROS timeout handles upstream command loss, while the MCU watchdog handles serial loss, MCU-side control faults, and low-level actuator safety.

## Design Principles

- The MCU owns low-level drive control.
- The ROS node normalizes `/cmd_vel` and forwards it to the MCU.
- Kick-start, ramp limiting, minimum PWM, per-wheel gain, and watchdog logic belong in MCU firmware.
- ROS and MCU should both implement timeout safety mechanisms.
