"""pro-wms CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from pro_wms_cli import __version__
from pro_wms_cli.errors import WmsError
from pro_wms_cli.store import api_call, kernel, save_kernel, using_http


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pro-wms", description="pro-wms state machine + race harness")
    parser.add_argument("--version", action="version", version=f"pro-wms {__version__}")
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
    try:
        print(_dispatch(args))
    except (WmsError, KeyError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _dump(value) -> str:
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _dispatch(args: argparse.Namespace) -> str:
    if using_http():
        return _dispatch_http(args)
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
    return _dump({"on_hand": wh.qty_on_hand(args.sku, args.warehouse), "ledger": rows})


def _dispatch_http(args: argparse.Namespace) -> str:
    mapping = {
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
        "ledger": ("GET", f"/ledger?sku={getattr(args, 'sku', '')}", None),
    }
    method, path, body = mapping[args.cmd]
    return _dump(api_call(method, path, body))


if __name__ == "__main__":
    raise SystemExit(main())
