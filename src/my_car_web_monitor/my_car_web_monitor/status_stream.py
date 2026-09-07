"""Ordered ROS status fan-out from executor threads to read-only web clients."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import threading
import time
from typing import Any


@dataclass(frozen=True)
class StatusSnapshot:
    value: Any = None
    raw: str = ""
    received_at: float | None = None
    count: int = 0

    def payload(self) -> dict:
        return {
            "motor_status": self.value,
            "motor_status_raw": self.raw,
            "motor_status_age_sec": (None if self.received_at is None else
                                     max(0.0, time.monotonic() - self.received_at)),
            "motor_status_received_count": self.count,
        }


@dataclass(eq=False)
class StatusSubscription:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    active: bool = True
    dropped: int = 0

    def offer(self, snapshot: StatusSnapshot) -> None:
        # Only called on this client's event loop; the ROS thread never blocks.
        if not self.active:
            return
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped += 1
        self.queue.put_nowait(snapshot)


class StatusStream:
    def __init__(self, queue_size: int = 256) -> None:
        self._lock = threading.Lock()
        self._latest = StatusSnapshot()
        self._clients: set[StatusSubscription] = set()
        self._queue_size = queue_size

    def publish(self, raw: str) -> None:
        received_at = time.monotonic()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = None
        with self._lock:
            snapshot = StatusSnapshot(value, raw, received_at, self._latest.count + 1)
            self._latest = snapshot
            for client in tuple(self._clients):
                try:
                    # Schedule immutable per-message snapshots, not a later read
                    # of _latest. Preserve callback order even during bursts.
                    client.loop.call_soon_threadsafe(client.offer, snapshot)
                except RuntimeError:  # Client event loop has already closed.
                    client.active = False
                    self._clients.discard(client)

    def latest_payload(self) -> dict:
        with self._lock:
            latest = self._latest
        return latest.payload()

    def subscribe(self) -> StatusSubscription:
        client = StatusSubscription(asyncio.get_running_loop(), asyncio.Queue(self._queue_size))
        with self._lock:
            client.offer(self._latest)
            self._clients.add(client)
        return client

    def unsubscribe(self, client: StatusSubscription) -> None:
        with self._lock:
            client.active = False
            self._clients.discard(client)
