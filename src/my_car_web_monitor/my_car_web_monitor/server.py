from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rclpy.node import Node

from my_car_web_monitor.config import Settings
from my_car_web_monitor.control import RosControlBridge
from my_car_web_monitor.streams import StreamRegistry

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web" / "static"


class OfferRequest(BaseModel):
    stream_id: str
    sdp: str
    type: str


def create_app(node: Node, settings: Settings) -> FastAPI:
    app = FastAPI(title="my_car_web_monitor")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    stream_registry: StreamRegistry | None = None
    control_bridge: RosControlBridge | None = None
    active_control_socket: WebSocket | None = None

    @app.on_event("startup")
    async def startup_event() -> None:
        nonlocal stream_registry, control_bridge
        stream_registry = StreamRegistry(settings)
        control_bridge = RosControlBridge(node, settings)

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        if control_bridge is not None:
            await control_bridge.stop()
        if stream_registry is not None:
            await stream_registry.close()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/streams")
    async def streams() -> dict[str, list[dict[str, str]]]:
        if stream_registry is None:
            raise HTTPException(status_code=503, detail="Stream registry is not ready")
        return {"streams": stream_registry.list_streams()}

    @app.post("/offer")
    async def offer(payload: OfferRequest) -> dict[str, str]:
        if stream_registry is None:
            raise HTTPException(status_code=503, detail="Stream registry is not ready")
        try:
            return await stream_registry.create_answer(
                payload.stream_id,
                {"sdp": payload.sdp, "type": payload.type},
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown stream '{payload.stream_id}'") from exc

    @app.get("/control/config")
    async def control_config() -> dict[str, float]:
        return {
            "linear_speed": settings.control_linear_speed,
            "angular_speed": settings.control_angular_speed,
        }

    @app.get("/control/status")
    async def control_status() -> dict:
        if control_bridge is None:
            raise HTTPException(status_code=503, detail="ROS control bridge is not ready")
        return control_bridge.status()

    @app.websocket("/ws/control")
    async def control_ws(websocket: WebSocket) -> None:
        nonlocal active_control_socket
        await websocket.accept()

        if control_bridge is None:
            await websocket.send_json({"type": "error", "message": "ROS control bridge is not ready"})
            await websocket.close(code=1011)
            return

        if active_control_socket is not None:
            await websocket.send_json(
                {"type": "error", "message": "Another control session is already active"}
            )
            await websocket.close(code=1008)
            return

        active_control_socket = websocket
        await websocket.send_json({"type": "status", **control_bridge.status()})
        loop = asyncio.get_running_loop()
        last_command_at = loop.time()

        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=settings.control_watchdog_timeout,
                    )
                except TimeoutError:
                    now = loop.time()
                    if now - last_command_at >= settings.control_watchdog_timeout:
                        await control_bridge.stop()
                        last_command_at = now
                    continue

                message_type = message.get("type")
                if message_type == "command":
                    status = await control_bridge.send_command(
                        float(message.get("linear", 0.0)),
                        float(message.get("angular", 0.0)),
                    )
                    last_command_at = loop.time()
                    await websocket.send_json({"type": "status", **status})
                elif message_type == "stop":
                    status = await control_bridge.stop()
                    last_command_at = loop.time()
                    await websocket.send_json({"type": "status", **status})
                elif message_type == "status":
                    await websocket.send_json({"type": "status", **control_bridge.status()})
                else:
                    await websocket.send_json(
                        {"type": "error", "message": f"Unknown message type '{message_type}'"}
                    )
        except WebSocketDisconnect:
            logger.info("Control websocket disconnected")
        except Exception as exc:
            logger.exception("Control websocket error: %s", exc)
            try:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            if active_control_socket is websocket:
                active_control_socket = None
            try:
                await control_bridge.stop()
            except Exception:
                logger.warning("Failed to publish stop command on websocket shutdown", exc_info=True)

    return app

