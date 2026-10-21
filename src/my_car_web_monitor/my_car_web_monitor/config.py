from __future__ import annotations

from dataclasses import dataclass
import os
import math


@dataclass(slots=True)
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8443"))
    camera_source: str = os.getenv("CAMERA_SOURCE", "picamera2")
    camera_streams: str = os.getenv("CAMERA_STREAMS", "")
    width: int = int(os.getenv("CAMERA_WIDTH", "1280"))
    height: int = int(os.getenv("CAMERA_HEIGHT", "720"))
    fps: int = int(os.getenv("CAMERA_FPS", "15"))
    webrtc_packet_max: int = int(os.getenv("WEBRTC_PACKET_MAX", "1100"))
    cmd_vel_topic: str = os.getenv("CMD_VEL_TOPIC", "/cmd_vel")
    motor_status_topic: str = os.getenv("MOTOR_STATUS_TOPIC", "/motor_bridge_node/status")
    control_linear_speed: float = float(os.getenv("CONTROL_LINEAR_SPEED", "2.0"))
    control_angular_speed: float = float(os.getenv("CONTROL_ANGULAR_SPEED", "3.0"))
    control_linear_speed_min: float = float(os.getenv("CONTROL_LINEAR_SPEED_MIN", "0.0"))
    control_linear_speed_default: float = float(os.getenv("CONTROL_LINEAR_SPEED_DEFAULT", "0.2"))
    control_angular_speed_min: float = float(os.getenv("CONTROL_ANGULAR_SPEED_MIN", "0.0"))
    control_angular_speed_default: float = float(os.getenv("CONTROL_ANGULAR_SPEED_DEFAULT", "0.3"))
    control_watchdog_timeout: float = float(os.getenv("CONTROL_WATCHDOG_TIMEOUT", "0.3"))
    log_level: str = os.getenv("LOG_LEVEL", "info")

    def __post_init__(self) -> None:
        for axis in ("linear", "angular"):
            maximum = getattr(self, f"control_{axis}_speed")
            minimum = getattr(self, f"control_{axis}_speed_min")
            default = getattr(self, f"control_{axis}_speed_default")
            if not all(math.isfinite(value) for value in (minimum, default, maximum)) or not 0 <= minimum <= default <= maximum:
                raise ValueError(f"control_{axis}_speed requires finite 0 <= min <= default <= max")


settings = Settings()
