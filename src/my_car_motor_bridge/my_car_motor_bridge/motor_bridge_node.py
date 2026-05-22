"""ROS2 node that bridges /cmd_vel to normalized MCU serial packets."""

from __future__ import annotations

import json
from typing import Optional

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from std_msgs.msg import String

from my_car_motor_bridge.command_converter import normalize_cmd_vel
from my_car_motor_bridge.protocol import MotorCommand, MotorStatus, try_parse_status
from my_car_motor_bridge.serial_transport import SerialTransport


class MotorBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("motor_bridge_node")

        self.declare_parameter("port", "/dev/ttyACM0")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("max_linear_x", 0.5)
        self.declare_parameter("max_angular_z", 1.5)
        self.declare_parameter("send_rate_hz", 50.0)
        self.declare_parameter("cmd_timeout_sec", 0.3)
        self.declare_parameter("enable_on_start", True)
        self.declare_parameter("send_zero_when_timeout", True)
        self.declare_parameter("read_rate_hz", 100.0)

        self.port = self.get_parameter("port").get_parameter_value().string_value
        self.baudrate = (
            self.get_parameter("baudrate").get_parameter_value().integer_value
        )
        self.max_linear_x = (
            self.get_parameter("max_linear_x").get_parameter_value().double_value
        )
        self.max_angular_z = (
            self.get_parameter("max_angular_z").get_parameter_value().double_value
        )
        self.send_rate_hz = (
            self.get_parameter("send_rate_hz").get_parameter_value().double_value
        )
        self.cmd_timeout_sec = (
            self.get_parameter("cmd_timeout_sec").get_parameter_value().double_value
        )
        self.enable_on_start = (
            self.get_parameter("enable_on_start").get_parameter_value().bool_value
        )
        self.send_zero_when_timeout = (
            self.get_parameter("send_zero_when_timeout")
            .get_parameter_value()
            .bool_value
        )
        self.read_rate_hz = (
            self.get_parameter("read_rate_hz").get_parameter_value().double_value
        )

        self.transport = SerialTransport(
            port=self.port,
            baudrate=int(self.baudrate),
            read_timeout=0.0,
            write_timeout=0.05,
        )
        self.rx_buffer = bytearray()
        self.seq = 0
        self.last_twist: Optional[Twist] = None
        self.last_cmd_time: Optional[Time] = None
        self.last_reconnect_time = self.get_clock().now()
        self.reconnect_period_sec = 1.0

        self.status_pub = self.create_publisher(String, "~/status", 10)
        self.cmd_sub = self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)

        send_period = 1.0 / self.send_rate_hz if self.send_rate_hz > 0.0 else 0.02
        read_period = 1.0 / self.read_rate_hz if self.read_rate_hz > 0.0 else 0.01
        self.send_timer = self.create_timer(send_period, self._on_send_timer)
        self.read_timer = self.create_timer(read_period, self._on_read_timer)

        self._try_reconnect(force=True)

    def _on_cmd_vel(self, msg: Twist) -> None:
        self.last_twist = msg
        self.last_cmd_time = self.get_clock().now()

    def _is_cmd_timed_out(self, now: Time) -> bool:
        if self.last_cmd_time is None:
            return True
        age_sec = (now - self.last_cmd_time).nanoseconds * 1e-9
        return age_sec > self.cmd_timeout_sec

    def _next_seq(self) -> int:
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return seq

    def _try_reconnect(self, force: bool = False) -> None:
        if self.transport.is_open():
            return

        now = self.get_clock().now()
        elapsed_sec = (now - self.last_reconnect_time).nanoseconds * 1e-9
        if not force and elapsed_sec < self.reconnect_period_sec:
            return

        self.last_reconnect_time = now
        if self.transport.open():
            self.get_logger().info(
                f"Opened serial port {self.port} at {self.baudrate} baud"
            )
        else:
            self.get_logger().warn(
                f"Failed to open serial port {self.port}", throttle_duration_sec=5.0
            )

    def _build_command(self, now: Time) -> Optional[MotorCommand]:
        timed_out = self._is_cmd_timed_out(now)
        if timed_out:
            if not self.send_zero_when_timeout:
                return None
            return MotorCommand(seq=self._next_seq(), v_cmd=0, w_cmd=0, enable=False)

        twist = self.last_twist
        if twist is None:
            return MotorCommand(seq=self._next_seq(), v_cmd=0, w_cmd=0, enable=False)

        v_cmd, w_cmd = normalize_cmd_vel(
            linear_x=float(twist.linear.x),
            angular_z=float(twist.angular.z),
            max_linear_x=float(self.max_linear_x),
            max_angular_z=float(self.max_angular_z),
        )
        return MotorCommand(
            seq=self._next_seq(),
            v_cmd=v_cmd,
            w_cmd=w_cmd,
            enable=bool(self.enable_on_start),
        )

    def _on_send_timer(self) -> None:
        self._try_reconnect()
        if not self.transport.is_open():
            return

        command = self._build_command(self.get_clock().now())
        if command is None:
            return

        if not self.transport.write(command.pack()):
            self.get_logger().warn(
                "Serial write failed; will attempt reconnect", throttle_duration_sec=2.0
            )

    def _on_read_timer(self) -> None:
        self._try_reconnect()
        if not self.transport.is_open():
            return

        data = self.transport.read(64)
        if data:
            self.rx_buffer.extend(data)

        while True:
            status = try_parse_status(self.rx_buffer)
            if status is None:
                break
            self._publish_status(status)

    def _publish_status(self, status: MotorStatus) -> None:
        msg = String()
        msg.data = json.dumps(
            {
                "seq": status.seq,
                "state": status.state,
                "error": status.error,
                "battery_mv": status.battery_mv,
            },
            separators=(",", ":"),
        )
        self.status_pub.publish(msg)

    def send_disable_zero(self) -> None:
        if not self.transport.is_open():
            self.transport.open()
        if self.transport.is_open():
            command = MotorCommand(seq=self._next_seq(), v_cmd=0, w_cmd=0, enable=False)
            self.transport.write(command.pack())

    def destroy_node(self) -> None:
        try:
            self.send_disable_zero()
        finally:
            self.transport.close()
        super().destroy_node()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = MotorBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
