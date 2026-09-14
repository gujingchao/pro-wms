"""FastAPI wrapper around the in-process Warehouse kernel."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pro_wms_cli.errors import (
    IllegalTransition,
    InsufficientStock,
    PermissionDenied,
    StockConflict,
)
from pro_wms_cli.kernel import Warehouse

_WAREHOUSE = Warehouse()


def get_warehouse() -> Warehouse:
    return _WAREHOUSE


def reset_warehouse() -> Warehouse:
    """Replace process-global kernel (tests)."""
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
    if x_user and x_user.strip():
        return x_user.strip()
    if as_query and as_query.strip():
        return as_query.strip()
    if body_user and body_user.strip():
        return body_user.strip()
    return default


app = FastAPI(title="pro-wms API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.exception_handler(KeyError)
async def _key_error(_request: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"missing: {exc.args[0]}", "error": "NotFound"})


class PutawayBody(BaseModel):
    to: str
    user: str | None = None


class AllocateBody(BaseModel):
    strategy: str = "fefo"
    user: str | None = None


class WaveBody(BaseModel):
    outbounds: list[str] = Field(default_factory=list)
    user: str | None = None


class RaceBody(BaseModel):
    sku: str = "SKU-MILK"
    warehouse: str = "WH-EAST"
    workers: int = 8
    user: str | None = None


class UserBody(BaseModel):
    user: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/snapshot")
def snapshot() -> Any:
    """Full kernel dump for admin UI / CLI `pro-wms snapshot`."""
    return _jsonable(get_warehouse().to_dict())


@app.post("/seed/demo")
def seed_demo() -> Any:
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
    strategy = payload.strategy if payload.strategy in {"fifo", "fefo"} else "fefo"
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=payload.user)
    return _jsonable(get_warehouse().allocate_outbound(outbound_id, strategy=strategy, user=user))


@app.post("/waves")
def wave_create(
    body: WaveBody,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
) -> Any:
    user = _resolve_user(default="supervisor", x_user=x_user, as_query=as_, body_user=body.user)
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
        default="supervisor",
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
    sku: str = Query(...),
    warehouse: str | None = Query(None),
) -> Any:
    wh = get_warehouse()
    rows = [
        r.__dict__
        for r in wh.ledger
        if r.sku == sku and (warehouse is None or r.warehouse == warehouse)
    ]
    return _jsonable({"on_hand": wh.qty_on_hand(sku, warehouse), "ledger": rows})
