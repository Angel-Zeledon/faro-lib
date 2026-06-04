"""
In-process WebSocket broadcaster.
The worker thread calls broadcast_sync(); connected WebSocket clients receive
the update immediately. Dead connections are silently removed.
"""

import asyncio
import logging
from typing import Dict, Set

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ProgressBroadcaster:
    def __init__(self):
        self._sockets: Dict[str, Set[WebSocket]] = {}

    async def connect(self, job_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._sockets.setdefault(job_id, set()).add(ws)
        log.debug(f"WS connected for job {job_id}")

    def disconnect(self, job_id: str, ws: WebSocket) -> None:
        if job_id in self._sockets:
            self._sockets[job_id].discard(ws)

    async def broadcast(self, job_id: str, message: dict) -> None:
        dead: Set[WebSocket] = set()
        for ws in list(self._sockets.get(job_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        if dead:
            self._sockets[job_id] -= dead

    def broadcast_sync(self, job_id: str, message: dict) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.broadcast(job_id, message))
        except RuntimeError:
            pass


broadcaster = ProgressBroadcaster()
