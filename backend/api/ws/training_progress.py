"""
WebSocket endpoint for real-time training progress.

Client connects to: ws://host/ws/jobs/{job_id}/progress?token=<access_token>

Messages from server:
  {"type": "state",    "job_id": "...", "status": "RUNNING", "progress": {...}}
  {"type": "progress", "job_id": "...", "percent": 45, "step": "training", "message": "..."}
  {"type": "completed","job_id": "..."}
  {"type": "failed",   "job_id": "...", "error": "..."}
  {"type": "pong"}

Messages from client:
  "ping"   → server replies with pong
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.auth.jwt_handler import decode_token
from backend.training.job_service import get_job
from backend.training.progress_broadcaster import broadcaster

router = APIRouter()
log = logging.getLogger(__name__)


@router.websocket("/ws/jobs/{job_id}/progress")
async def training_progress_ws(websocket: WebSocket, job_id: str):
    token = websocket.query_params.get("token", "")
    tenant_id = websocket.query_params.get("tenant_id", "")

    if not token or not tenant_id:
        await websocket.close(code=4001, reason="Missing token or tenant_id")
        return

    try:
        payload = decode_token(token)
        if payload.get("tenant_id") != tenant_id or payload.get("type") != "access":
            raise ValueError("Unauthorized")
    except ValueError:
        await websocket.close(code=4003, reason="Unauthorized")
        return

    job = get_job(tenant_id, job_id)
    if not job:
        await websocket.close(code=4004, reason="Job not found")
        return

    await broadcaster.connect(job_id, websocket)
    log.debug(f"WebSocket connected: job={job_id} tenant={tenant_id}")

    # Send immediate state snapshot
    await websocket.send_json({
        "type": "state",
        "job_id": job_id,
        "status": job["status"],
        "progress": job.get("progress", {}),
    })

    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(job_id, websocket)
        log.debug(f"WebSocket disconnected: job={job_id}")
