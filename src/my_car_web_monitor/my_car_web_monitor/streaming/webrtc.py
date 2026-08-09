from __future__ import annotations

import asyncio
import logging
from fractions import Fraction
from typing import Any

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.codecs import h264, vpx
from av import VideoFrame

from my_car_web_monitor.sources.base import FrameSource

logger = logging.getLogger(__name__)


def configure_packet_max(packet_max: int) -> None:
    """Keep encoded RTP payloads below tunnel MTUs such as Tailscale's 1280."""
    if not 256 <= packet_max <= 1200:
        raise ValueError("WEBRTC_PACKET_MAX must be between 256 and 1200 bytes")

    # aiortc 1.14 uses a module-level 1300-byte packetizer limit for both
    # codecs.  That exceeds a 1280-byte IPv6 tunnel MTU after RTP/SRTP, UDP,
    # and IPv6 headers are added, causing fragmentation and unrecoverable
    # video frames on Tailscale while still working on a 1500-byte LAN.
    vpx.PACKET_MAX = packet_max
    h264.PACKET_MAX = packet_max
    logger.info("WebRTC codec packetizer limit set to %d bytes", packet_max)


class WebRTCVideoTrack(VideoStreamTrack):
    """aiortc video track backed by a local frame source."""

    kind = "video"

    def __init__(self, source: FrameSource) -> None:
        super().__init__()
        self._source = source
        self._timestamp = 0
        self._time_base = Fraction(1, 90_000)

    async def recv(self) -> VideoFrame:
        try:
            image = await self._source.read()
        except Exception:
            logger.exception("Video source failed while reading a frame")
            raise

        # VideoStreamTrack.next_timestamp() is fixed at 30 FPS.  Using it here
        # made a 15 FPS camera look like a 30 FPS producer to the encoder and
        # could saturate a Raspberry Pi shortly after playback started.
        self._timestamp += max(1, round(90_000 / self._source.fps))
        frame = VideoFrame.from_ndarray(image, format=self._source.pixel_format)
        frame.pts = self._timestamp
        frame.time_base = self._time_base
        return frame


class PeerManager:
    """Owns active WebRTC peer connections for one stream."""

    def __init__(self, source: FrameSource, packet_max: int = 1100) -> None:
        configure_packet_max(packet_max)
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
