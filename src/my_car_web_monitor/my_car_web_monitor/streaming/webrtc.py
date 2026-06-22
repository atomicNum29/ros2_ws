from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

from my_car_web_monitor.sources.base import FrameSource

logger = logging.getLogger(__name__)


class WebRTCVideoTrack(VideoStreamTrack):
    """aiortc video track backed by a local frame source."""

    kind = "video"

    def __init__(self, source: FrameSource) -> None:
        super().__init__()
        self._source = source

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()
        image = await self._source.read()
        frame = VideoFrame.from_ndarray(image, format=self._source.pixel_format)
        frame.pts = pts
        frame.time_base = time_base
        return frame


class PeerManager:
    """Owns active WebRTC peer connections for one stream."""

    def __init__(self, source: FrameSource) -> None:
        self._source = source
        self._pcs: set[RTCPeerConnection] = set()
        self._source_started = False
        self._lock = asyncio.Lock()

    async def ensure_source_started(self) -> None:
        async with self._lock:
            if self._source_started:
                return
            await self._source.start()
            self._source_started = True
            logger.info("Video source started")

    async def create_answer(self, offer: dict[str, Any]) -> dict[str, str]:
        await self.ensure_source_started()

        pc = RTCPeerConnection()
        self._pcs.add(pc)
        pc.addTrack(WebRTCVideoTrack(self._source))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            logger.info("Peer connection state changed: %s", pc.connectionState)
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await self._discard_peer(pc)

        offer_description = RTCSessionDescription(sdp=offer["sdp"], type=offer["type"])
        await pc.setRemoteDescription(offer_description)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        }

    async def close(self) -> None:
        for pc in list(self._pcs):
            await self._discard_peer(pc)
        if self._source_started:
            await self._source.stop()
            self._source_started = False

    async def _discard_peer(self, pc: RTCPeerConnection) -> None:
        if pc not in self._pcs:
            return
        self._pcs.discard(pc)
        await pc.close()
        logger.info("Peer connection closed")
        if not self._pcs and self._source_started:
            await self._source.stop()
            self._source_started = False
            logger.info("Video source stopped")

