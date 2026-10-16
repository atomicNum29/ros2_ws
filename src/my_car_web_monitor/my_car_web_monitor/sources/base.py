from __future__ import annotations

from abc import ABC, abstractmethod
from fractions import Fraction
from typing import Any


class FrameSource(ABC):
    """Common interface for camera, synthetic and ROS video frame producers."""

    width: int
    height: int
    fps: int
    time_base: Fraction
    pixel_format: str

    @abstractmethod
    async def start(self) -> None:
        """Allocate resources before streaming starts."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources after streaming stops."""

    @abstractmethod
    async def read(self) -> Any:
        """Return the next frame as a numpy ndarray."""

    async def read_frame(self) -> tuple[Any, float | None, str]:
        """Pixels, capture time in server monotonic seconds, and time provenance.

        None means this source cannot provide a trustworthy capture timestamp.
        Existing array-only sources remain compatible and report no latency.
        """
        return await self.read(), None, ""
