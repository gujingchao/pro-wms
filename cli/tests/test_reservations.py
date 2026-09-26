from copy import deepcopy

import pytest

from pro_wms_cli.errors import IllegalTransition, InvalidRequest, StockConflict
from pro_wms_cli.kernel import Warehouse


def assert_balance(wh):
    for sku in wh.skus:
        for warehouse in wh.warehouses:
            assert sum(r.qty_delta for r in wh.ledger if r.sku == sku and r.warehouse == warehouse) == (
                wh.qty_on_hand(sku, warehouse)
            )
    assert all(0 <= row.reserved <= row.qty for row in wh.stock.values())


def test_reserve_then_ship_changes_physical_stock_only_on_shipment():
    wh = Warehouse()
    wh.seed_demo()
    assert_balance(wh)
    wh.allocate_outbound("OUT-2001")
    assert wh.qty_on_hand("SKU-MILK") == 10
    assert wh.qty_reserved("SKU-MILK") == 3
    assert wh.qty_available("SKU-MILK") == 7
    assert_balance(wh)
    wave = wh.wave_create(["OUT-2001"])
    wh.wave_pick(wave.id)
    assert wh.qty_on_hand("SKU-MILK") == 7
    assert wh.qty_reserved("SKU-MILK") == 0
    assert_balance(wh)
    before = deepcopy(wh.to_dict())
    with pytest.raises(IllegalTransition):
        wh.wave_pick(wave.id)
    assert wh.to_dict() == before


def test_cancel_releases_reservations_without_physical_movement():
    wh = Warehouse()
    wh.seed_demo()
    wh.allocate_outbound("OUT-2001")
    wh.outbound_cancel("OUT-2001")
    assert wh.qty_available("SKU-MILK") == wh.qty_on_hand("SKU-MILK") == 10
    assert wh.qty_reserved("SKU-MILK") == 0
    assert_balance(wh)
    with pytest.raises(IllegalTransition):
        wh.outbound_cancel("OUT-2001")


def test_missing_reservation_prevents_entire_shipment():
    wh = Warehouse()
    wh.seed_demo()
    wh.outbounds["OUT-2001"].lines[0]["qty"] = 7
    wh.allocate_outbound("OUT-2001")
    wave = wh.wave_create(["OUT-2001"])
    wh.stock[("WH-EAST", "EAST-A-01-02", "LOT-M2")].reserved = 0
    before = deepcopy(wh.to_dict())
    with pytest.raises(StockConflict):
        wh.wave_pick(wave.id)
    assert wh.to_dict() == before


def test_legacy_snapshot_is_rejected_without_guessing_inventory_semantics():
    wh = Warehouse()
    wh.seed_demo()
    snapshot = deepcopy(wh.to_dict())
    snapshot.pop("schema_version")
    with pytest.raises(InvalidRequest, match="legacy snapshot"):
        Warehouse().load_dict(snapshot)
