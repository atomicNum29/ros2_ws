"""ROS velocity/control status bridge or raw CAN diagnostic tunnel."""

from __future__ import annotations

import json
import math
import time

from geometry_msgs.msg import Twist
import rclpy
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from std_msgs.msg import String

from my_car_motor_bridge.command_converter import convert_cmd_vel
from my_car_motor_bridge.protocol import (
    CanRequest, CanResponse, MotorCommand, MotorStatus, SystemStatusV2,
    try_parse_packet,
)
from my_car_motor_bridge.serial_transport import SerialTransport
from my_car_motor_bridge.status_policy import StatusPolicy


class MotorBridgeNode(Node):
    def __init__(self, **kwargs) -> None:
        super().__init__("motor_bridge_node", **kwargs)
        # These settings determine entity lifetimes and require a node restart.
        defaults = {
            "port": "/dev/ttyACM0", "baudrate": 115200,
            "send_rate_hz": 50.0, "read_rate_hz": 100.0,
            "cmd_timeout_sec": 0.3, "enable_on_start": True,
            "send_zero_when_timeout": True, "serial_mode": "control",
            "legacy_fallback_enabled": True, "legacy_fallback_timeout_sec": 1.0,
        }
        for name, default in defaults.items():
            self.declare_parameter(name, default, ParameterDescriptor(read_only=True))
            setattr(self, name, self.get_parameter(name).value)
        try:
            if self.serial_mode not in ("control", "bridge"):
                raise ValueError("serial_mode must be control or bridge")
            for name in ("send_rate_hz", "read_rate_hz", "cmd_timeout_sec"):
                value = getattr(self, name)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} must be finite and positive")
            if (not math.isfinite(self.legacy_fallback_timeout_sec)
                    or self.legacy_fallback_timeout_sec < 0):
                raise ValueError("legacy_fallback_timeout_sec must be finite and nonnegative")
            if self.baudrate <= 0:
                raise ValueError("baudrate must be positive")
        except ValueError:
            super().destroy_node()
            raise

        self.transport = SerialTransport(
            port=self.port, baudrate=self.baudrate,
            read_timeout=0.0, write_timeout=0.05,
        )
        self.rx_buffer = bytearray()
        self.status_policy = StatusPolicy(
            self.legacy_fallback_enabled, self.legacy_fallback_timeout_sec,
        )
        self.seq = 0
        self.last_twist: Twist | None = None
        self.last_cmd_time: float | None = None
        self.last_reconnect_time = time.monotonic()
        self.reconnect_period_sec = 1.0
        self._connected = False
        self.status_pub = self.cmd_sub = self.send_timer = None
        self.can_tx_sub = self.can_rx_pub = None
        if self.serial_mode == "control":
            self.status_pub = self.create_publisher(String, "~/status", 10)
            self.cmd_sub = self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
            self.send_timer = self.create_timer(1.0 / self.send_rate_hz, self._on_send_timer)
        else:
            self.can_rx_pub = self.create_publisher(String, "~/can_rx", 100)
            self.can_tx_sub = self.create_subscription(String, "~/can_tx", self._on_can_tx, 10)
        self.read_timer = self.create_timer(1.0 / self.read_rate_hz, self._on_read_timer)
        self._try_reconnect(force=True)

    def _on_cmd_vel(self, msg: Twist) -> None:
        if self.serial_mode != "control" or not self.transport.is_open():
            return
        if not math.isfinite(msg.linear.x) or not math.isfinite(msg.angular.z):
            self.last_twist = self.last_cmd_time = None
            self.get_logger().warn("Ignoring non-finite cmd_vel", throttle_duration_sec=2.0)
            return
        self.last_twist = msg
        self.last_cmd_time = time.monotonic()

    def _is_cmd_timed_out(self, now: float) -> bool:
        return self.last_cmd_time is None or now - self.last_cmd_time > self.cmd_timeout_sec

    def _next_seq(self) -> int:
        seq = self.seq
        self.seq = (seq + 1) & 0xFF
        return seq

    def _reset_session(self, connected_at: float | None = None) -> None:
        self.rx_buffer.clear()
        self.status_policy.reset(connected_at)
        self.last_twist = self.last_cmd_time = None

    def _mark_disconnected(self) -> None:
        self.transport.close()
        self._connected = False
        self._reset_session()

    def _try_reconnect(self, force: bool = False) -> None:
        if self.transport.is_open():
            return
        if self._connected:
            self._mark_disconnected()
        now = time.monotonic()
        if not force and now - self.last_reconnect_time < self.reconnect_period_sec:
            return
        self.last_reconnect_time = now
        if self.transport.open():
            self._connected = True
            self._reset_session(time.monotonic())
            self.get_logger().info(
                f"Opened {self.port} at {self.baudrate} baud ({self.serial_mode})"
            )
        else:
            self.get_logger().warn(f"Failed to open serial port {self.port}",
                                   throttle_duration_sec=5.0)

    def _build_command(self, now: float) -> MotorCommand | None:
        if self._is_cmd_timed_out(now) or self.last_twist is None:
            if not self.send_zero_when_timeout:
                return None
            return MotorCommand(self._next_seq(), 0, 0, False)
        v_cmd, w_cmd = convert_cmd_vel(self.last_twist.linear.x, self.last_twist.angular.z)
        return MotorCommand(self._next_seq(), v_cmd, w_cmd, self.enable_on_start)

    def _write(self, data: bytes) -> None:
        if not self.transport.write(data):
            self._mark_disconnected()
            self.get_logger().warn("Serial write failed; will attempt reconnect",
                                   throttle_duration_sec=2.0)

    def _on_send_timer(self) -> None:
        if self.serial_mode != "control":
            return
        self._try_reconnect()
        if self.transport.is_open():
            command = self._build_command(time.monotonic())
            if command is not None:
                self._write(command.pack())

    def _on_can_tx(self, msg: String) -> None:
        if self.serial_mode != "bridge":
            return
        try:
            request = CanRequest.from_dict(json.loads(msg.data), self.seq)
        except (ValueError, TypeError) as exc:
            self.get_logger().warn(f"Rejected CAN request: {exc}", throttle_duration_sec=2.0)
            return
        if not self.transport.is_open():
            self.get_logger().warn("CAN request dropped: serial port is disconnected",
                                   throttle_duration_sec=2.0)
            return
        self._next_seq()
        self._write(request.pack())

    def _protocol_error(self, reason: str) -> None:
        self.get_logger().warn(reason, throttle_duration_sec=2.0)

    def _on_read_timer(self) -> None:
        self._try_reconnect()
        if not self.transport.is_open():
            return
        # Bounded draining accommodates tunnel bursts without an unbounded loop.
        # At the default rate this budget exceeds 115200 baud wire capacity.
        for _ in range(8):
            data = self.transport.read(512)
            if not self.transport.is_open():
                self._mark_disconnected()
                return
            self.rx_buffer.extend(data)
            while True:
                packet = try_parse_packet(self.rx_buffer, self._protocol_error)
                if packet is None:
                    break
                if isinstance(packet, CanResponse):
                    if self.can_rx_pub is not None:
                        self._publish_json(self.can_rx_pub, packet.to_dict())
                elif self.status_pub is not None:
                    self._publish_status(packet)
            if len(data) < 512:
                break

    @staticmethod
    def _publish_json(publisher, value: dict) -> None:
        publisher.publish(String(data=json.dumps(value, separators=(",", ":"))))

    def _publish_status(self, status: MotorStatus | SystemStatusV2) -> None:
        if (self.status_pub is not None
                and self.status_policy.should_publish(status, time.monotonic())):
            self._publish_json(self.status_pub, status.to_dict())

    def send_disable_zero(self) -> None:
        if self.serial_mode != "control":
            return
        if not self.transport.is_open():
            self.transport.open()
        if self.transport.is_open():
            self._write(MotorCommand(self._next_seq(), 0, 0, False).pack())

    def destroy_node(self) -> None:
        try:
            self.send_disable_zero()
        finally:
            self.transport.close()
            super().destroy_node()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = MotorBridgeNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
