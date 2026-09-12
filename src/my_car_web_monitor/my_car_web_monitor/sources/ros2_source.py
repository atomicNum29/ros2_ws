from __future__ import annotations

import asyncio
from fractions import Fraction
from io import BytesIO
import logging
import threading
import time

import numpy as np
from PIL import Image as PillowImage
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Image

from my_car_web_monitor.sources.base import FrameSource

logger = logging.getLogger(__name__)


def decode_image(message: Image | CompressedImage) -> np.ndarray:
    """Convert supported ROS images to owned RGB pixels, respecting row padding."""
    if isinstance(message, CompressedImage):
        # compressedDepth carries depth data, not a display-ready JPEG/PNG.
        if "compressedDepth" in message.format:
            raise ValueError("compressedDepth is not a supported video format")
        with PillowImage.open(BytesIO(bytes(message.data))) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Only JPEG and PNG compressed images are supported")
            return np.array(image.convert("RGB"))

    channels = {"mono8": 1, "8UC1": 1, "rgb8": 3, "bgr8": 3,
                "rgba8": 4, "bgra8": 4}.get(message.encoding)
    if channels is None:
        raise ValueError(f"Unsupported ROS image encoding: {message.encoding}")
    width, height, step = message.width, message.height, message.step
    if width <= 0 or height <= 0 or step < width * channels:
        raise ValueError("Invalid ROS image dimensions or row step")
    if len(message.data) != height * step:
        raise ValueError("ROS image data size does not match height * step")
    rows = np.frombuffer(message.data, dtype=np.uint8).reshape(height, step)
    pixels = rows[:, :width * channels].reshape(height, width, channels)
    if channels == 1:
        return np.repeat(pixels, 3, axis=2)
    pixels = pixels[:, :, :3]
    if message.encoding in {"bgr8", "bgra8"}:
        pixels = pixels[:, :, ::-1]
    return pixels.copy()


class RosImageSource(FrameSource):
    """Latest-image mailbox shared by viewers; ROS callbacks never decode video."""

    pixel_format = "rgb24"

    def __init__(self, node: Node, topic: str, fps: int, *, compressed: bool = False) -> None:
        if not topic or fps <= 0:
            raise ValueError("ROS image sources require a topic and positive CAMERA_FPS")
        self.width = 0
        self.height = 0
        self.fps = fps
        self.time_base = Fraction(1, fps)
        self.topic = topic
        self._node = node
        self._message_type = CompressedImage if compressed else Image
        self._subscription = None
        self._lock = threading.Lock()
        self._generation = 0
        self._started = False
        self._latest = None
        self._decoded_message = None
        self._frame = None
        self._last_warning = float("-inf")

    async def start(self) -> None:
        if self._started:
            return
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._started = True

        def receive(message: Image | CompressedImage) -> None:
            with self._lock:
                if self._started and generation == self._generation:
                    self._latest = message

        # Best effort receives both best-effort sensor publishers and reliable
        # publishers. Keep only one sample, including at the DDS boundary.
        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1,
                         reliability=ReliabilityPolicy.BEST_EFFORT)
        try:
            self._subscription = self._node.create_subscription(
                self._message_type, self.topic, receive, qos,
            )
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        with self._lock:
            self._started = False
            self._generation += 1
            self._latest = None
        if self._subscription is not None:
            self._node.destroy_subscription(self._subscription)
            self._subscription = None
        self._decoded_message = None
        self._frame = None

    async def read(self) -> np.ndarray:
        generation = self._generation
        while True:
            # Each viewer samples the same latest frame independently. A slow
            # browser cannot consume another viewer's frame or grow a queue.
            await asyncio.sleep(1 / self.fps)
            with self._lock:
                if not self._started or generation != self._generation:
                    raise RuntimeError("ROS image source is not started.")
                message = self._latest
            if message is None:
                continue
            if message is not self._decoded_message:
                self._decoded_message = message
                self._frame = None
                try:
                    self._frame = decode_image(message)
                    self.height, self.width = self._frame.shape[:2]
                except (ValueError, OSError, TypeError) as exc:
                    now = time.monotonic()
                    if now - self._last_warning >= 5.0:
                        logger.warning("Skipping image on %s: %s", self.topic, exc)
                        self._last_warning = now
            if self._frame is not None:
                return self._frame
