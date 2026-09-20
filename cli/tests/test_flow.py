from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from pro_wms_cli.cli import main
from pro_wms_cli.errors import (
    IllegalTransition,
    InsufficientStock,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    StockConflict,
)
from pro_wms_cli.kernel import Doc, Lot, Warehouse
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
    today = date.today()
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
    from pro_wms_cli.kernel import Doc

    wh = Warehouse()
    wh.seed_demo()
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


def test_expired_lots_are_never_allocated():
    """A lot past its expiry must not be shipped even without min_shelf_days."""
    wh = Warehouse()
    wh.seed_demo()
    wh.lots["LOT-M1"].expiry = date.today() - timedelta(days=1)
    doc = wh.allocate_outbound("OUT-2001", strategy="fefo")
    allocated = [a["lot"] for a in doc.meta["allocations"]]
    assert "LOT-M1" not in allocated
    assert allocated == ["LOT-M2"]


def test_fefo_uses_real_today_not_a_frozen_date():
    """Shelf-life maths must follow the clock, not a hardcoded calendar date."""
    wh = Warehouse()
    wh.seed_demo()
    today = date.today()
    # LOT-M1 is seeded at today+5; asking for a 7-day floor must always exclude it.
    wh.outbounds["OUT-SHELF"] = Doc(
        "OUT-SHELF",
        "draft",
        "WH-EAST",
        [{"sku": "SKU-MILK", "qty": 2, "min_shelf_days": 7}],
    )
    doc = wh.allocate_outbound("OUT-SHELF", strategy="fefo", as_of=today)
    assert doc.meta["allocations"][0]["lot"] == "LOT-M2"


def test_wave_requires_at_least_one_outbound():
    from pro_wms_cli.errors import InvalidRequest

    wh = Warehouse()
    wh.seed_demo()
    with pytest.raises(InvalidRequest):
        wh.wave_create([])


def test_cli_http_mode_sends_identity(monkeypatch):
    """`--as` must reach the service, otherwise RBAC is silently bypassed."""
    import pro_wms_cli.cli as cli_mod
    import pro_wms_cli.store as store_mod

    monkeypatch.setenv("PRO_WMS_API", "http://127.0.0.1:9999")
    store_mod.using_http.cache_clear()
    sent: list[dict] = []

    def fake_api_call(method, path, body=None, user=None):
        sent.append({"method": method, "path": path, "user": user})
        return {"stub": True}

    monkeypatch.setattr(cli_mod, "api_call", fake_api_call)

    assert cli_mod.main(["stocktake-approve", "ST-3001", "--as", "operator"]) == 0
    assert sent[-1]["user"] == "operator"


def test_cli_http_mode_forwards_ledger_warehouse(monkeypatch):
    import pro_wms_cli.cli as cli_mod
    import pro_wms_cli.store as store_mod

    monkeypatch.setenv("PRO_WMS_API", "http://127.0.0.1:9999")
    store_mod.using_http.cache_clear()
    sent: list[dict] = []

    def fake_api_call(method, path, body=None, user=None):
        sent.append({"path": path})
        return {"on_hand": 0, "ledger": []}

    monkeypatch.setattr(cli_mod, "api_call", fake_api_call)

    cli_mod.main(["ledger", "--sku", "SKU-MILK", "--warehouse", "WH-EAST"])
    assert "warehouse=WH-EAST" in sent[-1]["path"]


def test_putaway_with_unknown_location_keeps_document_open():
    """A rejected putaway must not leave the document half-transitioned."""
    wh = Warehouse()
    wh.seed_demo()
    wh.inbound_receive("IN-1001")
    with pytest.raises(IllegalTransition):
        wh.inbound_putaway("IN-1001", "NO-SUCH-LOC")
    assert wh.inbounds["IN-1001"].status == "receiving"


def test_putaway_is_all_or_nothing():
    """One failing line must not move the stock of the lines before it."""
    wh = Warehouse()
    wh.seed_demo()
    today = date.today()
    wh.lots["LOT-M3"] = Lot("LOT-M3", "SKU-MILK", "B-M3", today + timedelta(days=9), today)
    wh.inbounds["IN-MULTI"] = Doc(
        "IN-MULTI",
        "draft",
        "WH-EAST",
        [
            {"sku": "SKU-MILK", "qty": 2, "lot": "LOT-M3", "from": "EAST-DOCK"},
            {"sku": "SKU-MILK", "qty": 5, "lot": "LOT-M2", "from": "EAST-DOCK"},
        ],
    )
    wh.inbound_receive("IN-MULTI")
    # Sabotage the second line so putaway cannot cover it.
    wh._consume("WH-EAST", "EAST-DOCK", "LOT-M2", 4, expected_version=None)
    with pytest.raises(InsufficientStock):
        wh.inbound_putaway("IN-MULTI", "EAST-A-01-01")
    assert wh.inbounds["IN-MULTI"].status == "receiving"
    assert wh.stock[("WH-EAST", "EAST-DOCK", "LOT-M3")].qty == 2
    assert ("WH-EAST", "EAST-A-01-01", "LOT-M3") not in wh.stock


def test_missing_ids_raise_not_found():
    """A missing id is a domain `NotFound`, not a bare KeyError."""
    wh = Warehouse()
    wh.seed_demo()
    with pytest.raises(NotFound):
        wh.inbound_receive("IN-NOPE")
    with pytest.raises(NotFound):
        wh.allocate_outbound("OUT-NOPE")
    with pytest.raises(NotFound):
        wh.wave_create(["OUT-NOPE"])
    with pytest.raises(NotFound):
        wh.wave_pick("WV-NOPE")
    with pytest.raises(NotFound):
        wh.stocktake_approve("ST-NOPE", user="supervisor")


def test_receive_with_unknown_lot_books_nothing():
    wh = Warehouse()
    wh.seed_demo()
    wh.inbounds["IN-BAD"] = Doc("IN-BAD", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 1, "lot": "LOT-NOPE"}])
    with pytest.raises(NotFound):
        wh.inbound_receive("IN-BAD")
    assert wh.inbounds["IN-BAD"].status == "draft"
    assert wh.ledger == []


def test_same_sku_twice_in_one_outbound_plans_once():
    """Two lines of one SKU share a single plan instead of conflicting with each other."""
    wh = Warehouse()
    wh.seed_demo()
    wh.outbounds["OUT-DUP"] = Doc(
        "OUT-DUP",
        "draft",
        "WH-EAST",
        [{"sku": "SKU-MILK", "qty": 2}, {"sku": "SKU-MILK", "qty": 3}],
    )
    doc = wh.allocate_outbound("OUT-DUP", strategy="fefo")
    assert sum(a["qty"] for a in doc.meta["allocations"]) == 5
    assert wh.qty_on_hand("SKU-MILK", "WH-EAST") == 5


def test_allocation_conflict_changes_nothing():
    """A version conflict must not leave stock deducted or the document advanced."""
    wh = Warehouse()
    wh.seed_demo()
    before = wh.qty_on_hand("SKU-MILK", "WH-EAST")
    row = wh.snapshot_candidates("WH-EAST", "SKU-MILK", "fefo")[0]

    def poke() -> None:
        wh._consume(row.warehouse, row.location, row.lot_id, 1, expected_version=row.version)

    with pytest.raises(StockConflict):
        wh.allocate_outbound("OUT-2001", strategy="fefo", before_commit=poke)
    assert wh.outbounds["OUT-2001"].status == "draft"
    assert wh.qty_on_hand("SKU-MILK", "WH-EAST") == before - 1  # only the poke landed
    assert not [row for row in wh.ledger if row.ref_type == "allocate"]


def test_wave_with_unallocated_sibling_moves_nothing():
    """A bad member must not leave its siblings waved without a wave document."""
    wh = Warehouse()
    wh.seed_demo()
    wh.outbounds["OUT-2002"] = Doc("OUT-2002", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 1}])
    wh.allocate_outbound("OUT-2001")
    with pytest.raises(IllegalTransition):
        wh.wave_create(["OUT-2001", "OUT-2002"])
    assert wh.outbounds["OUT-2001"].status == "allocated"
    assert wh.waves == {}


def test_wave_rejects_duplicate_ids():
    wh = Warehouse()
    wh.seed_demo()
    wh.outbounds["OUT-2002"] = Doc("OUT-2002", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 1}])
    wh.allocate_outbound("OUT-2001")
    wh.allocate_outbound("OUT-2002")
    with pytest.raises(InvalidRequest):
        wh.wave_create(["OUT-2001", "OUT-2001"])
    assert wh.outbounds["OUT-2001"].status == "allocated"


def test_wave_rejects_cross_warehouse_batch():
    wh = Warehouse()
    wh.seed_demo()
    wh.outbounds["OUT-WEST"] = Doc("OUT-WEST", "draft", "WH-WEST", [{"sku": "SKU-BOLT", "qty": 1}])
    wh.allocate_outbound("OUT-2001")
    wh.allocate_outbound("OUT-WEST")
    with pytest.raises(InvalidRequest):
        wh.wave_create(["OUT-2001", "OUT-WEST"])
    assert wh.outbounds["OUT-2001"].status == "allocated"
    assert wh.outbounds["OUT-WEST"].status == "allocated"
    assert wh.waves == {}


def test_race_rejects_out_of_range_args():
    wh = Warehouse()
    wh.seed_demo()
    with pytest.raises(InvalidRequest):
        wh.race_allocate("SKU-MILK", "WH-EAST", workers=1000)
    with pytest.raises(InvalidRequest):
        wh.race_allocate("SKU-MILK", "WH-EAST", workers=8, each_qty=0)


def test_save_kernel_writes_atomically(tmp_path, monkeypatch):
    """The snapshot must land via os.replace, leaving no temp file behind."""
    import pro_wms_cli.store as store_mod

    target = tmp_path / "state.json"
    monkeypatch.setenv("PRO_WMS_STATE", str(target))
    monkeypatch.delenv("PRO_WMS_EPHEMERAL", raising=False)

    store_mod.save_kernel()

    assert target.exists()
    assert not (tmp_path / "state.json.tmp").exists()
    assert "warehouses" in json.loads(target.read_text(encoding="utf-8"))


def test_snapshot_round_trip():
    """`to_dict` -> JSON -> `load_dict` must restore an equivalent kernel."""
    wh = Warehouse()
    wh.seed_demo()
    wh.inbound_receive("IN-1001")
    wh.allocate_outbound("OUT-2001", strategy="fefo")

    restored = Warehouse()
    restored.load_dict(json.loads(json.dumps(wh.to_dict(), default=str)))

    assert restored.to_dict() == wh.to_dict()
    assert restored.qty_on_hand("SKU-MILK", "WH-EAST") == wh.qty_on_hand("SKU-MILK", "WH-EAST")


def test_load_dict_rejects_null_received_at():
    """A corrupt snapshot must fail loudly instead of poisoning FIFO ordering."""
    wh = Warehouse()
    wh.seed_demo()
    payload = json.loads(json.dumps(wh.to_dict(), default=str))
    payload["lots"]["LOT-M1"]["received_at"] = None
    with pytest.raises(ValueError, match="null date"):
        wh.load_dict(payload)
