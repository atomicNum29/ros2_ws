from __future__ import annotations

import asyncio
from fractions import Fraction
import time

import numpy as np

from my_car_web_monitor.sources.base import FrameSource

try:
    from picamera2 import Picamera2
except ImportError:  # pragma: no cover - target device dependency
    Picamera2 = None


class Picamera2Source(FrameSource):
    """Frame source backed directly by Raspberry Pi Camera through Picamera2."""

    pixel_format = "rgb24"

    def __init__(self, width: int, height: int, fps: int, camera_index: int = 0) -> None:
        if Picamera2 is None:
            raise RuntimeError(
                "Picamera2 is not installed. Set CAMERA_SOURCE=synthetic for local development."
            )

        self.width = width
        self.height = height
        self.fps = fps
        self.camera_index = camera_index
        self.time_base = Fraction(1, fps)
        self._camera = Picamera2(camera_num=camera_index)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        def _start() -> None:
            config = self._camera.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"},
                controls={"FrameRate": self.fps},
            )
            self._camera.configure(config)
            self._camera.start()

        await asyncio.to_thread(_start)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await asyncio.to_thread(self._camera.stop)
        self._started = False

    async def read(self) -> np.ndarray:
        if not self._started:
            raise RuntimeError("Camera source is not started.")
        return await asyncio.to_thread(self._camera.capture_array)


class SyntheticVideoSource(FrameSource):
    """Fallback source for development environments without camera hardware."""

    pixel_format = "rgb24"

    def __init__(self, width: int, height: int, fps: int) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.time_base = Fraction(1, fps)
        self._started = False
        self._frame_index = 0
        self._start_time = time.monotonic()

    async def start(self) -> None:
        self._started = True
        self._frame_index = 0
        self._start_time = time.monotonic()

    async def stop(self) -> None:
        self._started = False

    async def read(self) -> np.ndarray:
        if not self._started:
            raise RuntimeError("Synthetic source is not started.")

        await asyncio.sleep(1 / self.fps)
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        x = np.linspace(0, 255, self.width, dtype=np.uint8)
        y = np.linspace(0, 255, self.height, dtype=np.uint8)

        frame[:, :, 0] = (x + self._frame_index * 3) % 255
        frame[:, :, 1] = ((y[:, None] // 2) + self._frame_index * 5) % 255
        frame[:, :, 2] = 120

        band_center = (self._frame_index * 12) % self.width
        band_end = min(self.width, band_center + 80)
        frame[:, band_center:band_end, :] = (255, 255, 255)

        self._frame_index += 1
        return frame

