from __future__ import annotations

from dataclasses import dataclass
import logging

from my_car_web_monitor.config import Settings
from my_car_web_monitor.sources import Picamera2Source, SyntheticVideoSource
from my_car_web_monitor.streaming import PeerManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamSpec:
    stream_id: str
    source_type: str
    device: str | None = None


def parse_stream_specs(settings: Settings) -> list[StreamSpec]:
    if settings.camera_streams.strip():
        specs: list[StreamSpec] = []
        for raw_item in settings.camera_streams.split(","):
            item = raw_item.strip()
            if not item:
                continue
            parts = [part.strip() for part in item.split(":")]
            if len(parts) < 2:
                raise ValueError(
                    "CAMERA_STREAMS entries must be 'stream_id:source_type[:device]'"
                )
            specs.append(
                StreamSpec(
                    stream_id=parts[0],
                    source_type=parts[1],
                    device=parts[2] if len(parts) >= 3 else None,
                )
            )
        if specs:
            return specs

    if settings.camera_source == "synthetic":
        return [StreamSpec(stream_id="synthetic", source_type="synthetic")]

    return [StreamSpec(stream_id="camera0", source_type="picamera2", device="0")]


def build_source(spec: StreamSpec, settings: Settings):
    if spec.source_type == "synthetic":
        logger.info("Using synthetic source for stream '%s'", spec.stream_id)
        return SyntheticVideoSource(settings.width, settings.height, settings.fps)

    if spec.source_type == "picamera2":
        camera_index = int(spec.device or "0")
        logger.info(
            "Using Picamera2 source for stream '%s' on camera index %s",
            spec.stream_id,
            camera_index,
        )
        return Picamera2Source(
            settings.width,
            settings.height,
            settings.fps,
            camera_index=camera_index,
        )

    raise ValueError(
        f"Unsupported source_type '{spec.source_type}' for stream '{spec.stream_id}'. "
        "This first implementation supports 'picamera2' and 'synthetic'."
    )


class StreamRegistry:
    def __init__(self, settings: Settings) -> None:
        specs = parse_stream_specs(settings)
        self._specs = {spec.stream_id: spec for spec in specs}
        self._managers = {
            spec.stream_id: PeerManager(build_source(spec, settings)) for spec in specs
        }

    def list_streams(self) -> list[dict[str, str]]:
        return [
            {
                "id": spec.stream_id,
                "source_type": spec.source_type,
                "device": spec.device or "",
            }
            for spec in self._specs.values()
        ]

    async def create_answer(self, stream_id: str, offer: dict[str, str]) -> dict[str, str]:
        if stream_id not in self._managers:
            raise KeyError(stream_id)
        return await self._managers[stream_id].create_answer(offer)

    async def close(self) -> None:
        for manager in self._managers.values():
            await manager.close()

