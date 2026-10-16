"""Carry capture time through the codec without changing aiortc/RTP internals.

An appended 256x32 monochrome strip carries magic, monotonic microseconds and
CRC32. It is read from the decoded frame and clipped out of the displayed video.
The source pixels are never overwritten; unknown capture times remain unknown.
"""
from __future__ import annotations

import math
import struct
import zlib

import numpy as np

STRIP_WIDTH = 256
STRIP_HEIGHT = 32


def stamp_frame(image: np.ndarray, captured_at: float | None, kind: str) -> np.ndarray:
    if image.shape[1] < STRIP_WIDTH:
        return image
    if (captured_at is None or not math.isfinite(captured_at) or captured_at <= 0
            or kind not in {"P", "R", "S"}):
        # Keep dimensions stable even when capture metadata is unavailable.
        captured_at, kind = 0.0, "U"
    payload = struct.pack("!4sQ", b"LT1" + kind.encode("ascii"), round(captured_at * 1e6))
    payload += struct.pack("!I", zlib.crc32(payload))
    bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8)).reshape(4, 32)
    cells = np.repeat(np.repeat(bits, 8, axis=0), 8, axis=1) * 219 + 16
    height, width, channels = image.shape
    output = np.zeros((height + STRIP_HEIGHT, width, channels), dtype=np.uint8)
    output[:height] = image
    output[height:, :STRIP_WIDTH] = cells[:, :, None]
    return output
