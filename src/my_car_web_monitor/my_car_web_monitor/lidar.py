"""Latest-only LaserScan snapshots for the read-only browser view."""
from __future__ import annotations

import math
import struct
import threading
import time

from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from sensor_msgs.msg import LaserScan

# Little endian: magic, total rays, start/step angle, radius, ROS stamp, receive age.
HEADER = struct.Struct("<4sIfffdf")
POINT = struct.Struct("<IH")  # Original ray index, distance in millimetres.
RADIUS = 7.0


class LidarStream:
    def __init__(self, node) -> None:
        self._node = node
        self._lock = threading.Lock()
        self._latest = None
        self._revision = 0
        self._closed = False
        self._subscription = node.create_subscription(
            LaserScan, "/scan", self._on_scan,
            QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1,
                       reliability=ReliabilityPolicy.BEST_EFFORT,
                       durability=DurabilityPolicy.VOLATILE),
        )

    def _on_scan(self, message: LaserScan) -> None:
        received_at = time.monotonic()
        if not all(math.isfinite(value) for value in
                   (message.angle_min, message.angle_increment, message.range_min, message.range_max)):
            return
        if message.range_min < 0 or message.range_max < message.range_min:
            return
        points = bytearray()
        limit = min(RADIUS, message.range_max)
        for index, distance in enumerate(message.ranges):
            if math.isfinite(distance) and message.range_min <= distance <= limit and distance > 0:
                points.extend(POINT.pack(index, round(distance * 1000)))
        stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
        snapshot = (len(message.ranges), message.angle_min, message.angle_increment,
                    stamp, received_at, bytes(points))
        with self._lock:
            if not self._closed:
                self._latest = snapshot
                self._revision += 1

    def packet_after(self, revision: int):
        with self._lock:
            latest, current = self._latest, self._revision
        if latest is None or current == revision:
            return current, None
        total, start, step, stamp, received_at, points = latest
        age = max(0.0, time.monotonic() - received_at)
        if age > 1.0:
            return current, None
        return current, HEADER.pack(b"LDR1", total, start, step, RADIUS, stamp, age) + points

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._latest = None
        self._node.destroy_subscription(self._subscription)
