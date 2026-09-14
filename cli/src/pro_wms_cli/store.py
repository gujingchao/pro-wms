"""Process-wide kernel, or HTTP when PRO_WMS_API is set."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

from pro_wms_cli.kernel import Warehouse

_KERNEL = Warehouse()
_LOADED = False


def state_path() -> Path:
    return Path(os.environ.get("PRO_WMS_STATE", ".pro-wms-state.json"))


def ephemeral() -> bool:
    return os.environ.get("PRO_WMS_EPHEMERAL") == "1"


def kernel() -> Warehouse:
    global _LOADED
    if not _LOADED:
        _LOADED = True
        path = state_path()
        if not ephemeral() and path.exists():
            _KERNEL.load_dict(json.loads(path.read_text(encoding="utf-8")))
    return _KERNEL


def save_kernel() -> None:
    if ephemeral():
        return
    path = state_path()
    path.write_text(
        json.dumps(kernel().to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def api_base() -> str | None:
    raw = os.environ.get("PRO_WMS_API", "").strip()
    return raw.rstrip("/") or None


def api_call(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{api_base()}{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"API {exc.code}: {detail}") from exc


@lru_cache(maxsize=1)
def using_http() -> bool:
    return api_base() is not None
