"""
The on-disk form of a model artifact, and the hash that guards it.

Trust boundary
--------------
An artifact is a ``gzip``-compressed **JSON** document. There is no ``pickle`` and
no ``joblib`` anywhere on this path, on purpose: loading a pickle executes
whatever the file says, so a stored model could only ever be as trustworthy as
the disk it sat on. JSON cannot run code. What the JSON carries:

  * LightGBM models as LightGBM's own text format, XGBoost models as XGBoost's
    own UBJSON bytes (base64) - the libraries' native formats, read by their own
    parsers;
  * fitted parameters of the statistical models as plain numbers;
  * numpy arrays as ``{dtype, shape, base64}`` so a round trip is bit-exact.

The caller records the SHA-256 of the stored bytes when the artifact is written
(``ArtifactFile.sha256``) and passes it back when loading; ``verify`` refuses a
file whose bytes no longer hash to it BEFORE any parser sees them. So the
residual trust placed in the files is exactly this: whoever can write the
database row that holds the expected hash can also point it at a different file.
That is the same trust the application already places in its own database.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from typing import Any

import numpy as np

# Bumped when the layout of a payload changes in a way an old reader would
# misread. A reader refuses a version it does not know rather than guessing.
SCHEMA_VERSION = 1
FORMAT = "stockai-artifact/json+gzip/v1"


class ArtifactIntegrityError(Exception):
    """The stored bytes do not hash to the digest recorded when they were written
    (or are not an artifact at all). Never load such a file."""

    code = "artifact_integrity_failed"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- numpy-aware JSON ------------------------------------------------------

_ND = "__nd__"


def encode(obj: Any) -> Any:
    """Turn ``obj`` into something ``json.dumps`` accepts, losing nothing."""
    if isinstance(obj, np.ndarray):
        arr = np.ascontiguousarray(obj)
        return {_ND: True, "dtype": str(arr.dtype), "shape": list(arr.shape),
                "b64": base64.b64encode(arr.tobytes()).decode("ascii")}
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes__": True, "b64": base64.b64encode(bytes(obj)).decode("ascii")}
    return obj


def decode(obj: Any) -> Any:
    """Inverse of ``encode``."""
    if isinstance(obj, dict):
        if obj.get(_ND) is True:
            raw = base64.b64decode(obj["b64"])
            return np.frombuffer(raw, dtype=np.dtype(obj["dtype"])).reshape(obj["shape"]).copy()
        if obj.get("__bytes__") is True:
            return base64.b64decode(obj["b64"])
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    return obj


# -- the container ---------------------------------------------------------

def pack(payload: dict) -> bytes:
    """Serialise a payload deterministically (``mtime=0``, sorted keys), so the
    same content always hashes the same."""
    body = json.dumps(encode(payload), sort_keys=True, separators=(",", ":"),
                      allow_nan=True, default=str).encode("utf-8")
    return gzip.compress(body, compresslevel=6, mtime=0)


def verify(data: bytes, expected_sha256: str) -> None:
    """Raise ``ArtifactIntegrityError`` unless ``data`` hashes to the digest."""
    if not expected_sha256 or sha256_hex(data) != str(expected_sha256).lower():
        raise ArtifactIntegrityError(
            "artifact bytes do not match the recorded content hash")


def unpack(data: bytes, expected_sha256: str) -> dict:
    """Verify, then parse. The hash is checked first, always."""
    verify(data, expected_sha256)
    try:
        payload = decode(json.loads(gzip.decompress(data).decode("utf-8")))
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same refusal
        raise ArtifactIntegrityError(f"artifact is not readable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise ArtifactIntegrityError(
            f"unsupported artifact schema {payload.get('schema') if isinstance(payload, dict) else None!r}")
    return payload
