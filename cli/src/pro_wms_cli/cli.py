"""pro-wms CLI: state-machine driver and concurrency harness.

Output contract: the result of a successful command is a JSON document printed to
**stdout**. Diagnostics and errors go to **stderr** via `logging`, so piping stdout
into `jq` always yields valid JSON.

Exit codes: 0 success, 1 domain error or unknown document.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from pro_wms_cli import __version__
from pro_wms_cli.errors import WmsError
from pro_wms_cli.logging_setup import configure, get_logger
from pro_wms_cli.store import api_call, kernel, save_kernel, using_http

__all__ = ["main"]

_LOG = get_logger("cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pro-wms", description="pro-wms state machine + race harness")
    parser.add_argument("--version", action="version", version=f"pro-wms {__version__}")
    parser.add_argument("--idempotency-key", help="reuse this key when retrying the same HTTP write")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="load multi-warehouse demo")

    rec = sub.add_parser("inbound-receive")
    rec.add_argument("id")
    rec.add_argument("--as", dest="user", default="operator")

    put = sub.add_parser("inbound-putaway")
    put.add_argument("id")
    put.add_argument("--to", required=True)
    put.add_argument("--as", dest="user", default="operator")

    alloc = sub.add_parser("outbound-allocate")
    alloc.add_argument("id")
    alloc.add_argument("--strategy", choices=["fifo", "fefo"], default="fefo")
    alloc.add_argument("--as", dest="user", default="operator")

    cancel = sub.add_parser("outbound-cancel")
    cancel.add_argument("id")
    cancel.add_argument("--as", dest="user", default="operator")

    wave = sub.add_parser("wave-create")
    wave.add_argument("--outbounds", required=True, help="comma-separated outbound ids")
    wave.add_argument("--as", dest="user", default="supervisor")

    pick = sub.add_parser("wave-pick")
    pick.add_argument("id")
    pick.add_argument("--as", dest="user", default="operator")

    appr = sub.add_parser("stocktake-approve")
    appr.add_argument("id")
    appr.add_argument("--as", dest="user", default="supervisor")

    race = sub.add_parser("race", help="reproduce allocation version conflicts")
    race.add_argument("--sku", default="SKU-MILK")
    race.add_argument("--warehouse", default="WH-EAST")
    race.add_argument("--workers", type=int, default=8)

    sub.add_parser("snapshot", help="dump warehouses/stock/docs for UI")
    led = sub.add_parser("ledger")
    led.add_argument("--sku", required=True)
    led.add_argument("--warehouse", default=None)

    args = parser.parse_args(argv)
    configure()
    try:
        print(_dispatch(args))
    except (WmsError, RuntimeError) as exc:
        _LOG.error("%s: %s", type(exc).__name__, exc)
        return 1
    return 0


def _dump(value: Any) -> str:
    """Render a result as pretty JSON. `default=str` handles `date` values."""
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _dispatch(args: argparse.Namespace) -> str:
    """Run a command in-process and persist the snapshot afterwards.

    `snapshot` and `ledger` are read-only and skip the save.
    """
    if using_http():
        return _dispatch_http(args)
    if args.idempotency_key is not None:
        raise RuntimeError("--idempotency-key requires PRO_WMS_API (transactional service mode)")
    wh = kernel()
    if args.cmd == "seed":
        out = _dump(wh.seed_demo())
        save_kernel()
        return out
    if args.cmd == "inbound-receive":
        out = _dump(wh.inbound_receive(args.id, user=args.user))
        save_kernel()
        return out
    if args.cmd == "inbound-putaway":
        out = _dump(wh.inbound_putaway(args.id, args.to, user=args.user))
        save_kernel()
        return out
    if args.cmd == "outbound-allocate":
        out = _dump(wh.allocate_outbound(args.id, strategy=args.strategy, user=args.user))
        save_kernel()
        return out
    if args.cmd == "outbound-cancel":
        out = _dump(wh.outbound_cancel(args.id, user=args.user))
        save_kernel()
        return out
    if args.cmd == "wave-create":
        ids = [x.strip() for x in args.outbounds.split(",") if x.strip()]
        out = _dump(wh.wave_create(ids, user=args.user))
        save_kernel()
        return out
    if args.cmd == "wave-pick":
        out = _dump(wh.wave_pick(args.id, user=args.user))
        save_kernel()
        return out
    if args.cmd == "stocktake-approve":
        out = _dump(wh.stocktake_approve(args.id, user=args.user))
        save_kernel()
        return out
    if args.cmd == "snapshot":
        return _dump(wh.to_dict())
    if args.cmd == "race":
        if not wh.stock:
            wh.seed_demo()
        out = _dump(wh.race_allocate(args.sku, args.warehouse, workers=args.workers))
        save_kernel()
        return out
    rows = [
        r.__dict__
        for r in wh.ledger
        if r.sku == args.sku and (args.warehouse is None or r.warehouse == args.warehouse)
    ]
    return _dump({"on_hand": wh.qty_on_hand(args.sku, args.warehouse),
                  "reserved": wh.qty_reserved(args.sku, args.warehouse),
                  "available": wh.qty_available(args.sku, args.warehouse), "ledger": rows})


def _dispatch_http(args: argparse.Namespace) -> str:
    """Forward a command to the remote service.

    Identity is sent as `X-User` rather than only in the body, so commands without a
    body still reach the service with the caller's role intact.
    """
    mapping = {
        "outbound-cancel": ("POST", f"/outbounds/{getattr(args, 'id', '')}/cancel", None),
        "seed": ("POST", "/seed/demo", None),
        "snapshot": ("GET", "/snapshot", None),
        "inbound-receive": ("POST", f"/inbounds/{getattr(args, 'id', '')}/receive", None),
        "inbound-putaway": ("POST", f"/inbounds/{getattr(args, 'id', '')}/putaway", {"to": getattr(args, "to", None)}),
        "outbound-allocate": (
            "POST",
            f"/outbounds/{getattr(args, 'id', '')}/allocate",
            {"strategy": getattr(args, "strategy", "fefo")},
        ),
        "wave-create": ("POST", "/waves", {"outbounds": getattr(args, "outbounds", "").split(",")}),
        "wave-pick": ("POST", f"/waves/{getattr(args, 'id', '')}/pick", None),
        "stocktake-approve": ("POST", f"/stocktakes/{getattr(args, 'id', '')}/approve", None),
        "race": (
            "POST",
            "/race",
            {
                "sku": getattr(args, "sku", None),
                "warehouse": getattr(args, "warehouse", None),
                "workers": getattr(args, "workers", 8),
            },
        ),
        "ledger": ("GET", _ledger_path(args), None),
    }
    method, path, body = mapping[args.cmd]
    user = getattr(args, "user", None)
    if args.idempotency_key is not None:
        return _dump(api_call(method, path, body, user=user, idempotency_key=args.idempotency_key))
    return _dump(api_call(method, path, body, user=user))


def _ledger_path(args: argparse.Namespace) -> str:
    """Build `/ledger?sku=...&warehouse=...`, omitting an unset warehouse."""
    from urllib.parse import urlencode

    params = {"sku": getattr(args, "sku", "") or ""}
    warehouse = getattr(args, "warehouse", None)
    if warehouse:
        params["warehouse"] = warehouse
    return f"/ledger?{urlencode(params)}"


if __name__ == "__main__":
    raise SystemExit(main())
