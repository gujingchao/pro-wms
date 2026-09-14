from __future__ import annotations

import json

import pytest

from pro_wms_cli.cli import main
from pro_wms_cli.errors import IllegalTransition, PermissionDenied, StockConflict
from pro_wms_cli.kernel import Warehouse
from pro_wms_cli.store import kernel


def test_closed_loop_via_cli(capsys):
    k = kernel()
    k.seed_demo()
    assert main(["inbound-receive", "IN-1001"]) == 0
    assert main(["inbound-putaway", "IN-1001", "--to", "EAST-A-01-01"]) == 0
    assert main(["outbound-allocate", "OUT-2001", "--strategy", "fefo"]) == 0
    assert main(["wave-create", "--outbounds", "OUT-2001"]) == 0
    assert main(["wave-pick", "WV-2001"]) == 0
    assert main(["stocktake-approve", "ST-3001", "--as", "supervisor"]) == 0
    capsys.readouterr()
    assert main(["ledger", "--sku", "SKU-MILK", "--warehouse", "WH-EAST"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "on_hand" in payload
    assert payload["ledger"]


def test_fefo_prefers_earlier_expiry():
    wh = Warehouse()
    wh.seed_demo()
    doc = wh.allocate_outbound("OUT-2001", strategy="fefo")
    lots = [a["lot"] for a in doc.meta["allocations"]]
    assert lots[0] == "LOT-M1"


def test_fifo_prefers_earlier_received():
    wh = Warehouse()
    wh.seed_demo()
    doc = wh.allocate_outbound("OUT-2001", strategy="fifo")
    lots = [a["lot"] for a in doc.meta["allocations"]]
    assert lots[0] == "LOT-M1"


def test_illegal_transition():
    wh = Warehouse()
    wh.seed_demo()
    with pytest.raises(IllegalTransition):
        wh.inbound_putaway("IN-1001", "EAST-A-01-01")


def test_rbac_stocktake():
    wh = Warehouse()
    wh.seed_demo()
    with pytest.raises(PermissionDenied):
        wh.stocktake_approve("ST-3001", user="operator")
    wh.stocktake_approve("ST-3001", user="supervisor")


def test_race_has_conflicts():
    wh = Warehouse()
    wh.seed_demo()
    result = wh.race_allocate("SKU-MILK", "WH-EAST", workers=8, each_qty=3)
    assert result["ok"]
    assert result["conflict"]
    assert not result["other"]
    # remaining stock never goes negative
    assert result["on_hand"] >= 0


def test_direct_version_conflict():
    wh = Warehouse()
    wh.seed_demo()
    rows = wh.snapshot_candidates("WH-EAST", "SKU-MILK", "fefo")
    row = rows[0]
    wh._consume(row.warehouse, row.location, row.lot_id, 1, expected_version=row.version)
    with pytest.raises(StockConflict):
        wh._consume(row.warehouse, row.location, row.lot_id, 1, expected_version=row.version)


def test_fifo_and_fefo_diverge_when_expiry_and_receipt_disagree():
    """Later-received lot that expires sooner: FEFO takes it, FIFO takes older receipt."""
    from datetime import date, timedelta

    from pro_wms_cli.kernel import Lot

    wh = Warehouse()
    wh.seed_demo()
    today = date(2026, 9, 14)
    # Fresh receipt but near expiry — FEFO wants this; FIFO prefers LOT-M1 (older receipt).
    wh.lots["LOT-M3"] = Lot(
        "LOT-M3",
        "SKU-MILK",
        "B20260913",
        today + timedelta(days=2),
        today - timedelta(days=1),
    )
    wh._add_stock("WH-EAST", "EAST-A-01-01", "LOT-M3", 5)
    wh.outbounds["OUT-FEFO"] = wh.outbounds["OUT-2001"].__class__(
        "OUT-FEFO", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 2}]
    )
    wh.outbounds["OUT-FIFO"] = wh.outbounds["OUT-2001"].__class__(
        "OUT-FIFO", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 2}]
    )
    fefo = wh.allocate_outbound("OUT-FEFO", strategy="fefo")
    fifo = wh.allocate_outbound("OUT-FIFO", strategy="fifo")
    assert fefo.meta["allocations"][0]["lot"] == "LOT-M3"
    assert fifo.meta["allocations"][0]["lot"] == "LOT-M1"


def test_fefo_respects_min_shelf_days():
    from datetime import date, timedelta

    from pro_wms_cli.kernel import Doc, Lot

    wh = Warehouse()
    wh.seed_demo()
    today = date(2026, 9, 14)
    # Only near-expiry milk on hand for this outbound's threshold.
    wh.outbounds["OUT-SHELF"] = Doc(
        "OUT-SHELF",
        "draft",
        "WH-EAST",
        [{"sku": "SKU-MILK", "qty": 2, "min_shelf_days": 7}],
    )
    # LOT-M1 expires in 5 days → skipped; LOT-M2 in 12 days → taken.
    doc = wh.allocate_outbound("OUT-SHELF", strategy="fefo")
    assert doc.meta["allocations"][0]["lot"] == "LOT-M2"


def test_pluggable_strategy_registry():
    from pro_wms_cli.allocation import get_strategy

    assert get_strategy("fifo").name == "fifo"
    assert get_strategy("fefo").name == "fefo"
