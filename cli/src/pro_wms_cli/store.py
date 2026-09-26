"""Process-wide kernel access, or HTTP transport when `PRO_WMS_API` is set.

Two modes, selected purely by environment:
    * in-process — a single `Warehouse` instance, optionally persisted to a JSON file
    * HTTP       — every CLI command is forwarded to the FastAPI service

Environment variables:
    PRO_WMS_API       base URL; when set, HTTP mode is used
    PRO_WMS_STATE     snapshot file path (in-process mode only)
    PRO_WMS_EPHEMERAL "1" disables snapshot reads/writes (used by tests)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from pro_wms_cli.kernel import Warehouse
from pro_wms_cli.logging_setup import get_logger

__all__ = [
    "api_base",
    "api_call",
    "ephemeral",
    "kernel",
    "save_kernel",
    "state_path",
    "using_http",
]

_LOG = get_logger("store")

_KERNEL = Warehouse()
_LOADED = False
_LOAD_LOCK = Lock()


def state_path() -> Path:
    """Path of the JSON snapshot file (default `.pro-wms-state.json`)."""
    return Path(os.environ.get("PRO_WMS_STATE", ".pro-wms-state.json"))


def ephemeral() -> bool:
    """True when snapshot persistence is disabled (`PRO_WMS_EPHEMERAL=1`)."""
    return os.environ.get("PRO_WMS_EPHEMERAL") == "1"


def kernel() -> Warehouse:
    """Return the process-wide kernel, loading the snapshot on first access.

    The load happens at most once per process and is guarded by a lock, so
    concurrent first calls cannot read the snapshot twice or see a half-loaded state.

    Note:
        Callers that need isolation should construct `Warehouse()` directly.
    """
    global _LOADED
    if not _LOADED:
        with _LOAD_LOCK:
            if not _LOADED:
                path = state_path()
                if not ephemeral() and path.exists():
                    _KERNEL.load_dict(json.loads(path.read_text(encoding="utf-8")))
                _LOADED = True
    return _KERNEL


def save_kernel() -> None:
    """Write the kernel snapshot to disk atomically. No-op in ephemeral mode.

    The payload is written to a sibling temp file and `os.replace`d into position, so
    a concurrent reader (or a crash mid-write) never observes a truncated snapshot.
    """
    if ephemeral():
        return
    path = state_path()
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(
        json.dumps(kernel().to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def api_base() -> str | None:
    """Base URL of the remote service, or None when running in-process."""
    raw = os.environ.get("PRO_WMS_API", "").strip()
    return raw.rstrip("/") or None


def api_call(method: str, path: str, body: dict[str, Any] | None = None, user: str | None = None,
             idempotency_key: str | None = None) -> dict[str, Any]:
    """Perform one HTTP request against the service.

    Args:
        user: Identity forwarded as `X-User`. Always pass it for mutating calls —
            without it the service falls back to its least-privileged default.

    Returns:
        The decoded JSON body, or `{}` for an empty response.

    Raises:
        RuntimeError: the service returned a non-2xx status; the message carries the
            status code and the error payload.
    """
    url = f"{api_base()}{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if user:
        # Identity must travel on every call, otherwise the service falls back to its
        # own default user and RBAC is silently bypassed.
        req.add_header("X-User", user)
    if idempotency_key is not None:
        req.add_header("Idempotency-Key", idempotency_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"API {exc.code}: {detail}") from exc


@lru_cache(maxsize=1)
def using_http() -> bool:
    """True when `PRO_WMS_API` is set. Cached — tests must clear it after changing env."""
    return api_base() is not None
