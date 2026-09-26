"""Run the same transaction contract against memory and a real PostgreSQL server."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pro_wms_cli.errors import InsufficientStock, StockConflict
from pro_wms_cli.kernel import Doc
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.main import create_app
from app.postgres import PostgresRepository, migrate
from app.repository import MemoryRepository


@pytest.fixture(params=["memory", "postgres"])
def repository(request):
    if request.param == "memory":
        yield MemoryRepository()
        return
    admin_dsn = os.environ.get("PRO_WMS_TEST_DSN")
    if not admin_dsn:
        pytest.skip("set PRO_WMS_TEST_DSN to run real PostgreSQL integration tests")
    name = "pro_wms_test_" + uuid4().hex
    # Each test owns a fresh database; existing schemas/data are never reset.
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = make_conninfo(admin_dsn, dbname=name)
    try:
        migrate(dsn)
        migrate(dsn)  # Versioned initialization is safe to repeat.
        yield PostgresRepository(dsn)
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def seed(repository):
    repository.execute(lambda wh: wh.seed_demo(), write=True)


def snapshot(repository):
    return repository.execute(lambda wh: wh.to_dict())


def test_restart_and_two_api_instances_share_receipts_and_inventory(repository):
    seed(repository)
    second = PostgresRepository(repository.dsn) if isinstance(repository, PostgresRepository) else repository
    with TestClient(create_app(repository)) as a, TestClient(create_app(second)) as b:
        headers = {"Idempotency-Key": "receive-1", "X-User": "operator"}
        first = a.post("/inbounds/IN-1001/receive", headers=headers)
        assert first.status_code == 200
        assert b.post("/inbounds/IN-1001/receive", headers=headers).json() == first.json()
        assert b.get("/ledger", params={"sku": "SKU-MILK"}).json()["on_hand"] == 18
        assert a.post("/inbounds/IN-1001/putaway", json={"to": "EAST-A-01-01"}, headers=headers).status_code == 409
        assert b.post("/outbounds/OUT-2001/allocate", headers={"Idempotency-Key": "allocate-1"}).status_code == 200
        balance = a.get("/ledger", params={"sku": "SKU-MILK"}).json()
        assert (balance["on_hand"], balance["reserved"], balance["available"]) == (18, 3, 15)
        assert a.post("/outbounds/OUT-2001/cancel", headers={"Idempotency-Key": "cancel-1"}).status_code == 200
        assert b.get("/ledger", params={"sku": "SKU-MILK"}).json()["reserved"] == 0


def test_concurrent_identical_requests_have_one_effect(repository):
    seed(repository)

    def receive(_):
        return repository.execute(
            lambda wh: wh.inbound_receive("IN-1001"), write=True, key="same", fingerprint="receive"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(receive, range(8)))
    assert all(result == results[0] for result in results)
    assert repository.execute(lambda wh: wh.qty_on_hand("SKU-MILK")) == 18


def test_failed_command_rolls_back_and_does_not_consume_key(repository):
    seed(repository)
    before = snapshot(repository)

    def fail(wh):
        wh.inbound_receive("IN-1001")
        raise RuntimeError("failure after stock, document and ledger changes")

    with pytest.raises(RuntimeError):
        repository.execute(fail, write=True, key="retry", fingerprint="receive")
    assert snapshot(repository) == before
    repository.execute(lambda wh: wh.inbound_receive("IN-1001"), write=True, key="retry", fingerprint="receive")
    assert repository.execute(lambda wh: wh.qty_on_hand("SKU-MILK")) == 18


def test_database_failure_during_ledger_write_rolls_back_stock_document_and_key(repository):
    if not isinstance(repository, PostgresRepository):
        pytest.skip("SQL fault injection requires PostgreSQL")
    seed(repository)
    before = snapshot(repository)
    with psycopg.connect(repository.dsn) as conn:
        conn.execute(
            "CREATE FUNCTION pro_wms.reject_ledger() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'injected ledger failure'; END $$"
        )
        conn.execute(
            "CREATE TRIGGER reject_ledger BEFORE INSERT ON pro_wms.ledger "
            "FOR EACH ROW EXECUTE FUNCTION pro_wms.reject_ledger()"
        )
    with pytest.raises(psycopg.errors.RaiseException):
        repository.execute(lambda wh: wh.inbound_receive("IN-1001"), write=True, key="retry", fingerprint="receive")
    assert snapshot(repository) == before
    with psycopg.connect(repository.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM pro_wms.requests").fetchone()[0] == 0
        conn.execute("DROP TRIGGER reject_ledger ON pro_wms.ledger")
    repository.execute(lambda wh: wh.inbound_receive("IN-1001"), write=True, key="retry", fingerprint="receive")


def _allocate_in_process(dsn, outbound):
    repository = PostgresRepository(dsn)
    try:
        repository.execute(lambda wh: wh.allocate_outbound(outbound), write=True)
        return "ok"
    except (InsufficientStock, StockConflict):
        return "conflict"


def test_separate_processes_cannot_overreserve(repository):
    if not isinstance(repository, PostgresRepository):
        pytest.skip("cross-process storage requires PostgreSQL")

    def prepare(wh):
        wh.seed_demo()
        for i in range(4):
            wh.outbounds[str(i)] = Doc(str(i), "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 4}])

    repository.execute(prepare, write=True)
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_allocate_in_process, [repository.dsn] * 4, map(str, range(4))))
    assert results.count("ok") == 2
    assert repository.execute(lambda wh: (wh.qty_on_hand("SKU-MILK"), wh.qty_reserved("SKU-MILK"))) == [10, 8]


def test_persistent_wave_shipping_and_balances(repository):
    seed(repository)
    repository.execute(lambda wh: wh.allocate_outbound("OUT-2001"), write=True)
    wave = repository.execute(lambda wh: wh.wave_create(["OUT-2001"]), write=True)
    repository.execute(lambda wh: wh.wave_pick(wave["id"]), write=True, key="ship", fingerprint="ship")
    repository.execute(lambda wh: wh.wave_pick(wave["id"]), write=True, key="ship", fingerprint="ship")
    state = snapshot(repository)
    assert sum(r["qty"] for r in state["stock"]) == sum(r["qty_delta"] for r in state["ledger"])
    assert sum(r["reserved"] for r in state["stock"]) == 0
    assert repository.execute(lambda wh: wh.qty_on_hand("SKU-MILK")) == 7


def test_demo_is_explicit_and_database_failure_does_not_fall_back_to_memory():
    with TestClient(create_app(MemoryRepository(), demo_enabled=False)) as client:
        assert client.post("/seed/demo").status_code == 403
        assert client.post("/race").status_code == 403
    with TestClient(create_app(PostgresRepository("host=127.0.0.1 port=1 connect_timeout=1"))) as client:
        assert client.get("/health/ready").status_code == 503


def test_idempotency_key_validation(client):
    assert client.post("/seed/demo", headers={"Idempotency-Key": " "}).status_code == 400
    assert client.post("/seed/demo", headers={"Idempotency-Key": "x" * 129}).status_code == 400


def test_postgres_seed_cannot_reset_existing_data(repository):
    if not isinstance(repository, PostgresRepository):
        pytest.skip("memory demo intentionally supports reset")
    with TestClient(create_app(repository, demo_enabled=True)) as client:
        assert client.post("/seed/demo").status_code == 200
        assert client.post("/inbounds/IN-1001/receive").status_code == 200
        before = client.get("/snapshot").json()
        assert client.post("/seed/demo").status_code == 400
        assert client.post("/race").status_code == 403
        assert client.get("/snapshot").json() == before


def _receive_in_process(dsn):
    repository = PostgresRepository(dsn)
    result = repository.execute(
        lambda wh: wh.inbound_receive("IN-1001"), write=True, key="survives-process", fingerprint="receive"
    )
    return result, repository.execute(lambda wh: wh.qty_on_hand("SKU-MILK"))


def test_idempotent_result_survives_process_restart(repository):
    if not isinstance(repository, PostgresRepository):
        pytest.skip("memory is process-local by design")
    seed(repository)
    with ProcessPoolExecutor(max_workers=1) as first_process:
        first = first_process.submit(_receive_in_process, repository.dsn).result(timeout=30)
    with ProcessPoolExecutor(max_workers=1) as restarted_process:
        second = restarted_process.submit(_receive_in_process, repository.dsn).result(timeout=30)
    assert first == second
    assert second[1] == 18
