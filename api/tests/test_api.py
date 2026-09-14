from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_closed_loop(client):
    r = client.post("/seed/demo")
    assert r.status_code == 200
    seed = r.json()
    assert "IN-1001" in seed["inbounds"]
    assert "OUT-2001" in seed["outbounds"]

    r = client.post("/inbounds/IN-1001/receive")
    assert r.status_code == 200
    assert r.json()["status"] == "receiving"

    r = client.post("/inbounds/IN-1001/putaway", json={"to": "EAST-A-01-01"})
    assert r.status_code == 200
    assert r.json()["status"] == "closed"

    r = client.post("/outbounds/OUT-2001/allocate", json={"strategy": "fefo"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "allocated"
    assert body["meta"]["strategy"] == "fefo"
    assert body["meta"]["allocations"][0]["lot"] == "LOT-M1"

    r = client.post("/waves", json={"outbounds": ["OUT-2001"]})
    assert r.status_code == 200
    wave = r.json()
    assert wave["id"] == "WV-2001"
    assert wave["status"] == "waved"

    r = client.post(f"/waves/{wave['id']}/pick")
    assert r.status_code == 200
    assert r.json()["status"] == "shipped"

    r = client.post("/stocktakes/ST-3001/approve", headers={"X-User": "supervisor"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    r = client.get("/ledger", params={"sku": "SKU-MILK", "warehouse": "WH-EAST"})
    assert r.status_code == 200
    led = r.json()
    assert "on_hand" in led
    assert led["ledger"]


def test_race_returns_conflicts(client):
    assert client.post("/seed/demo").status_code == 200
    r = client.post("/race", json={"sku": "SKU-MILK", "warehouse": "WH-EAST", "workers": 8})
    assert r.status_code == 200
    result = r.json()
    assert result["ok"]
    assert result["conflict"]
    assert not result["other"]
    assert result["on_hand"] >= 0


def test_illegal_transition_409(client):
    assert client.post("/seed/demo").status_code == 200
    r = client.post("/inbounds/IN-1001/putaway", json={"to": "EAST-A-01-01"})
    assert r.status_code == 409
    assert r.json()["error"] == "IllegalTransition"


def test_snapshot(client):
    client.post("/seed/demo")
    res = client.get("/snapshot")
    assert res.status_code == 200
    body = res.json()
    assert "WH-EAST" in body.get("warehouses", {}) or "WH-EAST" in (body.get("warehouses") or [])
    assert "IN-1001" in body.get("inbounds", {})
