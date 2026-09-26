"""FastAPI wrapper around transactional `Warehouse` execution.

This layer is deliberately thin: it resolves the caller's identity, forwards to the
kernel, and serializes the result. Business rules live in
`pro_wms_cli.kernel` — do not re-implement them here.

Identity: `X-User` header (preferred), `?as=` query, or a `user` field in the body,
in that order. When none is supplied the request runs as `operator` (least
privilege), so privileged actions must state their role explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request
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
from app.repository import MemoryRepository, Repository


def reset_warehouse() -> Warehouse:
    """Reset only the default in-memory app, for tests and local demos."""
    if not isinstance(app.state.repository, MemoryRepository):
        raise RuntimeError("reset_warehouse cannot reset PostgreSQL")
    app.state.repository = MemoryRepository()
    return app.state.repository.warehouse


def _run(
    request: Request,
    operation: str,
    args: dict[str, Any],
    action: Callable[[Warehouse], Any],
    *,
    key: str | None = None,
    write: bool = True,
) -> Any:
    repository: Repository = request.app.state.repository
    fingerprint = hashlib.sha256(
        json.dumps([operation, args], sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return repository.execute(action, write=write, key=key, fingerprint=fingerprint)


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


router = APIRouter()


ERROR_STATUS: dict[type[Exception], int] = {
    InvalidRequest: 400,
    IllegalTransition: 409,
    PermissionDenied: 403,
    StockConflict: 409,
    InsufficientStock: 409,
    NotFound: 404,
}


async def _domain_error(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=ERROR_STATUS[type(exc)], content={"detail": str(exc), "error": type(exc).__name__})


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


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/snapshot")
def snapshot(request: Request) -> Any:
    """Full kernel dump for the admin UI and `pro-wms snapshot`."""
    return _run(request, "snapshot", {}, lambda wh: wh.to_dict(), write=False)


@router.post("/seed/demo")
def seed_demo(request: Request, key: str | None = Header(None, alias="Idempotency-Key")) -> Any:
    """Explicit demo mode only; PostgreSQL allows initialization of an empty database."""
    if not request.app.state.demo_enabled:
        raise PermissionDenied("demo endpoints are disabled")

    def seed(wh: Warehouse) -> Any:
        if request.app.state.persistent and (wh.warehouses or wh.skus or wh.stock or wh.ledger):
            raise InvalidRequest("PostgreSQL demo seed requires an empty database")
        return wh.seed_demo()

    return _run(request, "seed", {}, seed, key=key)


@router.post("/inbounds/{inbound_id}/receive")
def inbound_receive(
    request: Request,
    inbound_id: str,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    body: UserBody | None = None,
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    user = _resolve_user(
        default="operator",
        x_user=x_user,
        as_query=as_,
        body_user=body.user if body else None,
    )
    return _run(
        request,
        "receive",
        {"id": inbound_id, "user": user},
        lambda wh: wh.inbound_receive(inbound_id, user=user),
        key=key,
    )


@router.post("/inbounds/{inbound_id}/putaway")
def inbound_putaway(
    request: Request,
    inbound_id: str,
    body: PutawayBody,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=body.user)
    return _run(
        request,
        "putaway",
        {"id": inbound_id, "to": body.to, "user": user},
        lambda wh: wh.inbound_putaway(inbound_id, body.to, user=user),
        key=key,
    )


@router.post("/outbounds/{outbound_id}/allocate")
def outbound_allocate(
    request: Request,
    outbound_id: str,
    body: AllocateBody | None = None,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    payload = body or AllocateBody()
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=payload.user)
    return _run(
        request,
        "allocate",
        {"id": outbound_id, "strategy": payload.strategy, "user": user},
        lambda wh: wh.allocate_outbound(outbound_id, strategy=payload.strategy, user=user),
        key=key,
    )


@router.post("/waves")
def wave_create(
    request: Request,
    body: WaveBody,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    user = _resolve_user(default="operator", x_user=x_user, as_query=as_, body_user=body.user)
    ids = [x.strip() for x in body.outbounds if x and x.strip()]
    return _run(request, "wave", {"ids": ids, "user": user}, lambda wh: wh.wave_create(ids, user=user), key=key)


@router.post("/waves/{wave_id}/pick")
def wave_pick(
    request: Request,
    wave_id: str,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    body: UserBody | None = None,
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    user = _resolve_user(
        default="operator",
        x_user=x_user,
        as_query=as_,
        body_user=body.user if body else None,
    )
    return _run(
        request, "pick-ship", {"id": wave_id, "user": user}, lambda wh: wh.wave_pick(wave_id, user=user), key=key
    )


@router.post("/stocktakes/{stocktake_id}/approve")
def stocktake_approve(
    request: Request,
    stocktake_id: str,
    as_: str | None = Query(None, alias="as"),
    x_user: str | None = Header(None, alias="X-User"),
    body: UserBody | None = None,
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    user = _resolve_user(
        default="operator",
        x_user=x_user,
        as_query=as_,
        body_user=body.user if body else None,
    )
    return _run(
        request,
        "approve",
        {"id": stocktake_id, "user": user},
        lambda wh: wh.stocktake_approve(stocktake_id, user=user),
        key=key,
    )


@router.post("/race")
def race(
    request: Request, body: RaceBody | None = None, key: str | None = Header(None, alias="Idempotency-Key")
) -> Any:
    if not request.app.state.demo_enabled or request.app.state.persistent:
        raise PermissionDenied("race is only available in explicit in-memory demo mode")
    payload = body or RaceBody()

    def run(wh: Warehouse) -> Any:
        if not wh.stock:
            wh.seed_demo()
        return wh.race_allocate(payload.sku, payload.warehouse, workers=payload.workers)

    return _run(request, "race", payload.model_dump(), run, key=key)


@router.get("/ledger")
def ledger(
    request: Request,
    sku: str = Query(..., description="SKU to report on"),
    warehouse: str | None = Query(None, description="Optional warehouse filter"),
) -> Any:
    """Return `{"on_hand": int, "ledger": [...]}` for a SKU."""

    def read(wh: Warehouse) -> Any:
        rows = [r.__dict__ for r in wh.ledger if r.sku == sku and (warehouse is None or r.warehouse == warehouse)]
        return {
            "on_hand": wh.qty_on_hand(sku, warehouse),
            "reserved": wh.qty_reserved(sku, warehouse),
            "available": wh.qty_available(sku, warehouse),
            "ledger": rows,
        }

    return _run(request, "ledger", {}, read, write=False)


@router.post("/outbounds/{outbound_id}/cancel")
def outbound_cancel(
    request: Request,
    outbound_id: str,
    x_user: str | None = Header(None, alias="X-User"),
    key: str | None = Header(None, alias="Idempotency-Key"),
) -> Any:
    user = _resolve_user(default="operator", x_user=x_user)
    return _run(
        request,
        "cancel",
        {"id": outbound_id, "user": user},
        lambda wh: wh.outbound_cancel(outbound_id, user=user),
        key=key,
    )


@router.get("/health/ready")
def readiness(request: Request) -> Any:
    try:
        request.app.state.repository.ready()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


def create_app(repository: Repository | None = None, *, demo_enabled: bool | None = None) -> FastAPI:
    from app.postgres import PostgresRepository

    if repository is None:
        backend = os.environ.get("PRO_WMS_REPOSITORY", "memory")
        if backend == "postgres":
            repository = PostgresRepository(os.environ["DATABASE_URL"])
        elif backend == "memory":
            repository = MemoryRepository()
        else:
            raise RuntimeError("PRO_WMS_REPOSITORY must be memory or postgres")
    instance = FastAPI(title="pro-wms API", version=__version__)
    instance.state.repository = repository
    instance.state.persistent = isinstance(repository, PostgresRepository)
    instance.state.demo_enabled = os.environ.get("PRO_WMS_ENABLE_DEMO") == "1" if demo_enabled is None else demo_enabled
    instance.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-User", "Idempotency-Key"],
    )
    for error in ERROR_STATUS:
        instance.add_exception_handler(error, _domain_error)
    instance.include_router(router)
    return instance


app = create_app()
