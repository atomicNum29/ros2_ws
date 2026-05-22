"""Convert ROS velocity commands to normalized MCU command units."""

from __future__ import annotations


COMMAND_SCALE = 1000
COMMAND_MIN = -1000
COMMAND_MAX = 1000


def _clamp(value: int, lower: int = COMMAND_MIN, upper: int = COMMAND_MAX) -> int:
    return max(lower, min(upper, value))


def normalize_cmd_vel(
    linear_x: float,
    angular_z: float,
    max_linear_x: float,
    max_angular_z: float,
) -> tuple[int, int]:
    """Convert linear.x and angular.z into normalized integer commands."""
    if max_linear_x <= 0.0:
        v_cmd = 0
    else:
        v_cmd = _clamp(round(linear_x / max_linear_x * COMMAND_SCALE))

    if max_angular_z <= 0.0:
        w_cmd = 0
    else:
        w_cmd = _clamp(round(angular_z / max_angular_z * COMMAND_SCALE))

    return int(v_cmd), int(w_cmd)
