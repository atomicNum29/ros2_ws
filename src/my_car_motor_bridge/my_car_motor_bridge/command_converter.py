"""Convert ROS velocity commands to milli-unit MCU command values."""

from __future__ import annotations


MILLI_SCALE = 1000
INT16_MIN = -(2**15)
INT16_MAX = 2**15 - 1


def _clamp(value: int, lower: int = INT16_MIN, upper: int = INT16_MAX) -> int:
    return max(lower, min(upper, value))


def convert_cmd_vel(linear_x: float, angular_z: float) -> tuple[int, int]:
    """Convert m/s and rad/s values to milli m/s and milli rad/s."""
    v_cmd = _clamp(round(linear_x * MILLI_SCALE))
    w_cmd = _clamp(round(angular_z * MILLI_SCALE))
    return int(v_cmd), int(w_cmd)
