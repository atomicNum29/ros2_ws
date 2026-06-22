from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import String

from my_car_web_monitor.config import Settings


class RosControlBridge:
    """Publishes browser teleoperation commands to ROS and mirrors motor status."""

    def __init__(self, node: Node, settings: Settings) -> None:
        self._node = node
        self._settings = settings
        self._publisher = node.create_publisher(Twist, settings.cmd_vel_topic, 10)
        self._status_sub = node.create_subscription(
            String,
            settings.motor_status_topic,
            self._on_motor_status,
            10,
        )
        self._lock = asyncio.Lock()
        self._last_command = {"linear": 0.0, "angular": 0.0, "age_sec": None}
        self._last_command_time: float | None = None
        self._last_status: dict[str, Any] | None = None
        self._last_status_raw = ""
        self._last_status_time: float | None = None

    async def send_command(self, linear: float, angular: float) -> dict[str, Any]:
        linear = self._clamp(linear, self._settings.control_linear_speed)
        angular = self._clamp(angular, self._settings.control_angular_speed)

        async with self._lock:
            self._publish_twist(linear, angular)
            now = time.monotonic()
            self._last_command_time = now
            self._last_command = {"linear": linear, "angular": angular, "age_sec": 0.0}
            return self.status()

    async def stop(self) -> dict[str, Any]:
        return await self.send_command(0.0, 0.0)

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        command = dict(self._last_command)
        if self._last_command_time is not None:
            command["age_sec"] = round(now - self._last_command_time, 3)

        motor_status_age = None
        if self._last_status_time is not None:
            motor_status_age = round(now - self._last_status_time, 3)

        return {
            "cmd_vel_topic": self._settings.cmd_vel_topic,
            "motor_status_topic": self._settings.motor_status_topic,
            "last_command": command,
            "motor_status": self._last_status,
            "motor_status_raw": self._last_status_raw,
            "motor_status_age_sec": motor_status_age,
        }

    def _publish_twist(self, linear: float, angular: float) -> None:
        message = Twist()
        message.linear.x = float(linear)
        message.angular.z = float(angular)
        self._publisher.publish(message)

    def _on_motor_status(self, message: String) -> None:
        self._last_status_raw = message.data
        self._last_status_time = time.monotonic()
        try:
            self._last_status = json.loads(message.data)
        except json.JSONDecodeError:
            self._last_status = None

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        limit = abs(float(limit))
        return max(-limit, min(limit, float(value)))

