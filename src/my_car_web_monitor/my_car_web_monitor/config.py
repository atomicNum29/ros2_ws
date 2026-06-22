from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(slots=True)
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8443"))
    camera_source: str = os.getenv("CAMERA_SOURCE", "picamera2")
    camera_streams: str = os.getenv("CAMERA_STREAMS", "")
    width: int = int(os.getenv("CAMERA_WIDTH", "1280"))
    height: int = int(os.getenv("CAMERA_HEIGHT", "720"))
    fps: int = int(os.getenv("CAMERA_FPS", "15"))
    cmd_vel_topic: str = os.getenv("CMD_VEL_TOPIC", "/cmd_vel")
    motor_status_topic: str = os.getenv("MOTOR_STATUS_TOPIC", "/motor_bridge_node/status")
    control_linear_speed: float = float(os.getenv("CONTROL_LINEAR_SPEED", "0.3"))
    control_angular_speed: float = float(os.getenv("CONTROL_ANGULAR_SPEED", "1.0"))
    control_watchdog_timeout: float = float(os.getenv("CONTROL_WATCHDOG_TIMEOUT", "0.3"))
    log_level: str = os.getenv("LOG_LEVEL", "info")


settings = Settings()

