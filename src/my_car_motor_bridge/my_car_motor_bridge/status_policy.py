"""Per-connection status selection, independent of ROS and wall clock changes."""

from dataclasses import dataclass

from my_car_motor_bridge.protocol import MotorStatus, SystemStatusV2


@dataclass
class StatusPolicy:
    legacy_enabled: bool = True
    fallback_timeout: float = 1.0
    connected_at: float | None = None
    v2_seen: bool = False

    def reset(self, connected_at: float | None = None) -> None:
        self.connected_at = connected_at
        self.v2_seen = False

    def should_publish(self, status: MotorStatus | SystemStatusV2, now: float) -> bool:
        if self.connected_at is None:
            return False
        if isinstance(status, SystemStatusV2):
            self.v2_seen = True
            return True
        return (self.legacy_enabled and not self.v2_seen
                and now - self.connected_at >= self.fallback_timeout)
