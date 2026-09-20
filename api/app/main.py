"""FastAPI wrapper around the in-process `Warehouse` kernel.

This layer is deliberately thin: it resolves the caller's identity, forwards to the
kernel, and serializes the result. Business rules live in
`pro_wms_cli.kernel` — do not re-implement them here.

Identity: `X-User` header (preferred), `?as=` query, or a `user` field in the body,
in that order. When none is supplied the request runs as `operator` (least
privilege), so privileged actions must state their role explicitly.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pro_wms_cli.errors import (
    IllegalTransition,
    InsufficientStock,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    StockConflict,
)
from pro_wms_cli.kernel import Warehouse
from pydantic import BaseModel, Field

from app import __version__

_WAREHOUSE = Warehouse()


def get_warehouse() -> Warehouse:
    """Return the process-global kernel."""
    return _WAREHOUSE


def reset_warehouse() -> Warehouse:
    """Replace the process-global kernel with a fresh one (used by tests)."""
    global _WAREHOUSE
    _WAREHOUSE = Warehouse()
    return _WAREHOUSE


def _serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(_serialize(value), default=str))


def _resolve_user(
    *,
    default: str,
    x_user: str | None = None,
    as_query: str | None = None,
    body_user: str | None = None,
) -> str:
    """Resolve the acting identity: `X-User` header > `?as=` > body `user` > `default`.

    `default` is always the least-privileged role that can still perform the common
    case; endpoints requiring more must receive an explicit identity.
    """
    if x_user and x_user.strip():
        return x_user.strip()
    if as_query and as_query.strip():
        return as_query.strip()
    if body_user and body_user.strip():
        return body_user.strip()
    return default


def allowed_origins() -> list[str]:
    """Origins allowed by CORS, from `PRO_WMS_ALLOWED_ORIGINS` (comma separated).

    Falls back to the local dev origins. A wildcard is never used because it cannot
    be combined with credentialed requests and would expose the service to any site.
    """
    raw = os.environ.get("PRO_WMS_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]


app = FastAPI(title="pro-wms API", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-User"],
)


@app.exception_handler(InvalidRequest)
async def _invalid_request(_request: Request, exc: InvalidRequest) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc), "error": "InvalidRequest"})


# Single place where domain errors become HTTP statuses. Adding a domain error means
# adding one handler here — never scattering status codes across endpoints.
# `KeyError` is deliberately left unhandled: a stray dict-key bug inside the kernel
# should surface as a 500, not be disguised as "resource not found".


@app.exception_handler(IllegalTransition)
async def _illegal_transition(_request: Request, exc: IllegalTransition) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "error": "IllegalTransition"})


@app.exception_handler(PermissionDenied)
async def _permission_denied(_request: Request, exc: PermissionDenied) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc), "error": "PermissionDenied"})


@app.exception_handler(StockConflict)
async def _stock_conflict(_request: Request, exc: StockConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "error": "StockConflict"})


@app.exception_handler(InsufficientStock)
async def _insufficient_stock(_request: Request, exc: InsufficientStock) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "error": "InsufficientStock"})


@app.exception_handler(NotFound)
async def _not_found(_request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc), "error": "NotFound"})


class PutawayBody(BaseModel):
    to: str
    user: str | None = None


class AllocateBody(BaseModel):
    # A `Literal` rather than `str`: an unknown strategy is a 422 instead of being
    # silently coerced to fefo, and the contract can verify the enum.
    strategy: Literal["fifo", "fefo"] = "fefo"
    user: str | None = None


class WaveBody(BaseModel):
    outbounds: list[str] = Field(default_factory=list)
    user: str | None = None


class RaceBody(BaseModel):
    sku: str = "SKU-MILK"
    warehouse: str = "WH-EAST"
    # Bounded: the harness spawns one thread per worker, so an unbounded value would
    # let any caller exhaust the service.
    workers: int = Field(8, ge=1, le=32)
    user: str | None = None


class UserBody(BaseModel):
    user: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/snapshot")
def snapshot() -> Any:
    """Full kernel dump for the admin UI and `pro-wms snapshot`."""
    return _jsonable(get_warehouse().to_dict())


@app.post("/seed/demo")
def seed_demo() -> Any:
    """Reload the demo dataset. Destructive: discards current state."""
    return _jsonable(get_warehouse().seed_demo())


@app.post("/inbounds/{inbound_id}/receive")
def inbound_receive(
    inbound_id: str,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    body: UserBody | None = None,
) -> Any:
    user = _resolve_user(
        default="operator",
        x_user=x_user,
        as_query=as_,
        body_user=body.user if body else None,
    )
    return _jsonable(get_warehouse().inbound_receive(inbound_id, user=user))


@app.post("/inbounds/{inbound_id}/putaway")
def inbound_putaway(
    inbound_id: str,
    body: PutawayBody,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
) -> Any:
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=body.user)
    return _jsonable(get_warehouse().inbound_putaway(inbound_id, body.to, user=user))


@app.post("/outbounds/{outbound_id}/allocate")
def outbound_allocate(
    outbound_id: str,
    body: AllocateBody | None = None,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
) -> Any:
    payload = body or AllocateBody()
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=payload.user)
    return _jsonable(get_warehouse().allocate_outbound(outbound_id, strategy=payload.strategy, user=user))


@app.post("/waves")
def wave_create(
    body: WaveBody,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
) -> Any:
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=body.user)
    ids = [x.strip() for x in body.outbounds if x and x.strip()]
    return _jsonable(get_warehouse().wave_create(ids, user=user))


@app.post("/waves/{wave_id}/pick")
def wave_pick(
    wave_id: str,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    body: UserBody | None = None,
) -> Any:
    user = _resolve_user(
        default="operator",
        x_user=x_user,
        as_query=as_,
        body_user=body.user if body else None,
    )
    return _jsonable(get_warehouse().wave_pick(wave_id, user=user))


@app.post("/stocktakes/{stocktake_id}/approve")
def stocktake_approve(
    stocktake_id: str,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    body: UserBody | None = None,
) -> Any:
    user = _resolve_user(
        default="operator",
        x_user=x_user,
        as_query=as_,
        body_user=body.user if body else None,
    )
    return _jsonable(get_warehouse().stocktake_approve(stocktake_id, user=user))


@app.post("/race")
def race(body: RaceBody | None = None) -> Any:
    payload = body or RaceBody()
    wh = get_warehouse()
    if not wh.stock:
        wh.seed_demo()
    return _jsonable(wh.race_allocate(payload.sku, payload.warehouse, workers=payload.workers))


@app.get("/ledger")
def ledger(
    sku: str = Query(..., description="SKU to report on"),
    warehouse: str | None = Query(None, description="Optional warehouse filter"),
) -> Any:
    """Return `{"on_hand": int, "ledger": [...]}` for a SKU."""
    wh = get_warehouse()
    rows = [
        r.__dict__
        for r in wh.ledger
        if r.sku == sku and (warehouse is None or r.warehouse == warehouse)
    ]
    return _jsonable({"on_hand": wh.qty_on_hand(sku, warehouse), "ledger": rows})
