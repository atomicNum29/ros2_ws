from __future__ import annotations

from abc import ABC, abstractmethod
from fractions import Fraction
from typing import Any


class FrameSource(ABC):
    """Common interface for local video frame producers."""

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

