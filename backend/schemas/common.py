from datetime import datetime, timezone
from typing import Any


def ok(data: Any = None, **meta) -> dict:
    return {
        "success": True,
        "data": data,
        "meta": {"timestamp": datetime.now(timezone.utc).isoformat(), **meta},
    }


def err(code: str, message: str, details: dict = {}) -> dict:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
    }
