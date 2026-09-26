from copy import deepcopy

import pytest

from pro_wms_cli.errors import IllegalTransition, InsufficientStock, InvalidRequest
from pro_wms_cli.kernel import Doc, Warehouse


def warehouse():
    wh = Warehouse()
    wh.seed_demo()
    return wh


@pytest.mark.parametrize("qty", [-5, 0, True, 1.5, "3", None])
@pytest.mark.parametrize("kind", ["inbound", "outbound"])
def test_invalid_quantity_leaves_all_state_unchanged(qty, kind):
    wh = warehouse()
    if kind == "inbound":
        wh.inbounds["IN-1001"].lines.append(
            {"sku": "SKU-MILK", "lot": "LOT-M2", "qty": qty, "from": "EAST-DOCK"}
        )
    else:
        wh.outbounds["OUT-2001"].lines.append({"sku": "SKU-MILK", "qty": qty})
    before = deepcopy(wh.to_dict())
    with pytest.raises(InvalidRequest):
        if kind == "inbound":
            wh.inbound_receive("IN-1001")
        else:
            wh.allocate_outbound("OUT-2001")
    assert wh.to_dict() == before


@pytest.mark.parametrize("change", [
    {"sku": "SKU-BOLT"}, {"sku": "UNKNOWN"},
    {"from": "WEST-A-01-01"}, {"from": "UNKNOWN"},
])
def test_receive_rejects_inconsistent_line_before_mutating(change):
    wh = warehouse()
    line = dict(wh.inbounds["IN-1001"].lines[0])
    line.update(change)
    wh.inbounds["IN-1001"].lines.append(line)
    before = deepcopy(wh.to_dict())
    with pytest.raises((InvalidRequest, IllegalTransition)):
        wh.inbound_receive("IN-1001")
    assert wh.to_dict() == before


def test_putaway_rejects_other_warehouse():
    wh = warehouse()
    wh.inbound_receive("IN-1001")
    before = deepcopy(wh.to_dict())
    with pytest.raises(InvalidRequest):
        wh.inbound_putaway("IN-1001", "WEST-A-01-01")
    assert wh.to_dict() == before


@pytest.mark.parametrize("shortage", [False, True])
def test_putaway_aggregates_duplicate_source_lines(shortage):
    wh = warehouse()
    wh.inbounds["DUP"] = Doc("DUP", "draft", "WH-EAST", [
        {"sku": "SKU-MILK", "lot": "LOT-M2", "qty": 4, "from": "EAST-DOCK"},
        {"sku": "SKU-MILK", "lot": "LOT-M2", "qty": 4, "from": "EAST-DOCK"},
    ])
    wh.inbound_receive("DUP")
    if shortage:
        # Another valid outbound can consume dock stock before putaway.
        wh.outbounds["OTHER"] = Doc("OTHER", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 13}])
        wh.allocate_outbound("OTHER")
        before = deepcopy(wh.to_dict())
        with pytest.raises(InsufficientStock):
            wh.inbound_putaway("DUP", "EAST-A-01-01")
        assert wh.to_dict() == before
    else:
        total = wh.qty_on_hand("SKU-MILK")
        wh.inbound_putaway("DUP", "EAST-A-01-01")
        assert wh.inbounds["DUP"].status == "closed"
        assert wh.stock[("WH-EAST", "EAST-DOCK", "LOT-M2")].qty == 0
        assert wh.stock[("WH-EAST", "EAST-A-01-01", "LOT-M2")].qty == 8
        assert wh.qty_on_hand("SKU-MILK") == total


@pytest.mark.parametrize("kind", ["inbound", "outbound"])
@pytest.mark.parametrize("lines", [[], [{}]])
def test_empty_or_missing_required_lines_are_domain_errors(kind, lines):
    wh = warehouse()
    doc = wh.inbounds["IN-1001"] if kind == "inbound" else wh.outbounds["OUT-2001"]
    doc.lines = lines
    before = deepcopy(wh.to_dict())
    with pytest.raises(InvalidRequest):
        if kind == "inbound":
            wh.inbound_receive(doc.id)
        else:
            wh.allocate_outbound(doc.id)
    assert wh.to_dict() == before


@pytest.mark.parametrize("floor", [-1, True, 1.5, "7"])
def test_invalid_shelf_floor_changes_nothing(floor):
    wh = warehouse()
    wh.outbounds["OUT-2001"].lines[0]["min_shelf_days"] = floor
    before = deepcopy(wh.to_dict())
    with pytest.raises(InvalidRequest):
        wh.allocate_outbound("OUT-2001")
    assert wh.to_dict() == before
