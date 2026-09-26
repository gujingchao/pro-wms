"""PostgreSQL unit of work; rules stay in Warehouse, persistence writes only deltas.

The first release serializes writers on a single revision row. This is a deliberate
correctness-first boundary for a small deployment, not a high-throughput design.
Readers use a repeatable-read snapshot without blocking writers.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
from pro_wms_cli.errors import StockConflict
from pro_wms_cli.kernel import Warehouse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.repository import jsonable, validate_key

__all__ = ["PostgresRepository", "migrate"]

SCHEMA_VERSION = 1


def migrate(dsn: str) -> None:
    """Apply versioned SQL transactionally; never touch the old public-schema draft."""
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(716234001)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS pro_wms")
        conn.execute("CREATE TABLE IF NOT EXISTS pro_wms.migrations (version INTEGER PRIMARY KEY)")
        versions = {row[0] for row in conn.execute("SELECT version FROM pro_wms.migrations")}
        if versions - {SCHEMA_VERSION}:
            raise RuntimeError("unsupported database schema version")
        if SCHEMA_VERSION not in versions:
            sql = Path(__file__).with_name("migrations").joinpath("001_inventory.sql").read_text(encoding="utf-8")
            conn.execute(sql)
            conn.execute("INSERT INTO pro_wms.migrations VALUES (%s)", (SCHEMA_VERSION,))


class PostgresRepository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ready(self) -> None:
        with psycopg.connect(self.dsn, connect_timeout=5) as conn:
            row = conn.execute("SELECT version FROM pro_wms.migrations").fetchall()
            if row != [(SCHEMA_VERSION,)]:
                raise RuntimeError("run python -m app.postgres before serving requests")

    def execute(
        self, action: Callable[[Warehouse], Any], *, write: bool = False, key: str | None = None, fingerprint: str = ""
    ) -> Any:
        validate_key(key)
        with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row) as conn:
            if not write:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL lock_timeout = '10s'")
            conn.execute("SET LOCAL statement_timeout = '30s'")
            revision = conn.execute(
                "SELECT * FROM pro_wms.revision WHERE id = 1" + (" FOR UPDATE" if write else "")
            ).fetchone()
            if revision is None:
                raise RuntimeError("database has not been initialized")
            if write and key is not None:
                cached = conn.execute("SELECT * FROM pro_wms.requests WHERE key = %s", (key,)).fetchone()
                if cached:
                    if cached["fingerprint"] != fingerprint:
                        raise StockConflict("Idempotency-Key was already used for another request")
                    return cached["response"]
            wh = self._load(conn, revision)
            before = jsonable(wh.to_dict())
            result = jsonable(action(wh))
            if write:
                self._save(conn, before, jsonable(wh.to_dict()))
                conn.execute("UPDATE pro_wms.revision SET version = version + 1 WHERE id = 1")
                if key is not None:
                    conn.execute(
                        "INSERT INTO pro_wms.requests (key, fingerprint, response) VALUES (%s,%s,%s)",
                        (key, fingerprint, Jsonb(result)),
                    )
            return result

    def _load(self, conn: Any, revision: dict[str, Any]) -> Warehouse:
        data: dict[str, Any] = {"schema_version": 2, "users": revision["users"], "wave_seq": revision["wave_seq"]}
        data["warehouses"] = {r["id"]: r["name"] for r in conn.execute("SELECT * FROM pro_wms.warehouses")}
        data["locations"] = {r["id"]: r["warehouse"] for r in conn.execute("SELECT * FROM pro_wms.locations")}
        data["skus"] = {r["id"]: r["payload"] for r in conn.execute("SELECT * FROM pro_wms.skus")}
        data["lots"] = {r["id"]: r for r in conn.execute("SELECT * FROM pro_wms.lots")}
        data["stock"] = list(conn.execute("SELECT * FROM pro_wms.stock ORDER BY warehouse, location, lot_id"))
        data["ledger"] = [
            {k: v for k, v in r.items() if k != "id"} for r in conn.execute("SELECT * FROM pro_wms.ledger ORDER BY id")
        ]
        for kind in ("inbounds", "outbounds", "waves", "stocktakes"):
            data[kind] = {
                r["id"]: {k: v for k, v in r.items() if k != "kind"}
                for r in conn.execute("SELECT * FROM pro_wms.documents WHERE kind = %s", (kind,))
            }
        wh = Warehouse()
        wh.load_dict(data)
        return wh

    def _save(self, conn: Any, before: dict[str, Any], after: dict[str, Any]) -> None:
        # No current command deletes records. Refuse unsupported deletions rather than
        # acknowledging a command whose state would not survive the next load.
        for collection in ("warehouses", "skus", "locations", "lots", "inbounds", "outbounds", "waves", "stocktakes"):
            if before[collection].keys() - after[collection].keys():
                raise RuntimeError(f"deletion is not supported for {collection}")
        old_keys = {(r["warehouse"], r["location"], r["lot_id"]) for r in before["stock"]}
        new_keys = {(r["warehouse"], r["location"], r["lot_id"]) for r in after["stock"]}
        if old_keys - new_keys:
            raise RuntimeError("stock deletion is not supported; retain zero-quantity rows")
        # Master records are upserted only when changed; no truncate/reload on normal writes.
        for id_, name in after["warehouses"].items():
            if before["warehouses"].get(id_) != name:
                conn.execute(
                    "INSERT INTO pro_wms.warehouses VALUES (%s,%s) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name",
                    (id_, name),
                )
        for id_, payload in after["skus"].items():
            if before["skus"].get(id_) != payload:
                conn.execute(
                    "INSERT INTO pro_wms.skus VALUES (%s,%s) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload",
                    (id_, Jsonb(payload)),
                )
        for id_, warehouse in after["locations"].items():
            if before["locations"].get(id_) != warehouse:
                conn.execute(
                    "INSERT INTO pro_wms.locations VALUES (%s,%s) "
                    "ON CONFLICT (id) DO UPDATE SET warehouse=EXCLUDED.warehouse",
                    (id_, warehouse),
                )
        for id_, lot in after["lots"].items():
            if before["lots"].get(id_) != lot:
                conn.execute(
                    "INSERT INTO pro_wms.lots VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET "
                    "sku=EXCLUDED.sku,batch_no=EXCLUDED.batch_no,expiry=EXCLUDED.expiry,"
                    "received_at=EXCLUDED.received_at",
                    (id_, lot["sku"], lot["batch_no"], lot["expiry"], lot["received_at"]),
                )
        old_stock = {(r["warehouse"], r["location"], r["lot_id"]): r for r in before["stock"]}
        for row in after["stock"]:
            key = (row["warehouse"], row["location"], row["lot_id"])
            old = old_stock.get(key)
            if old == row:
                continue
            if old is None:
                conn.execute(
                    "INSERT INTO pro_wms.stock VALUES (%s,%s,%s,%s,%s,%s)",
                    (*key, row["qty"], row["reserved"], row["version"]),
                )
            else:
                changed = conn.execute(
                    "UPDATE pro_wms.stock SET qty=%s,reserved=%s,version=%s "
                    "WHERE warehouse=%s AND location=%s AND lot_id=%s AND version=%s",
                    (row["qty"], row["reserved"], row["version"], *key, old["version"]),
                )
                if changed.rowcount != 1:
                    raise StockConflict(f"stock changed for {key}")
        for kind in ("inbounds", "outbounds", "waves", "stocktakes"):
            for id_, doc in after[kind].items():
                if before[kind].get(id_) != doc:
                    conn.execute(
                        "INSERT INTO pro_wms.documents VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (kind,id) DO UPDATE SET warehouse=EXCLUDED.warehouse,"
                        "status=EXCLUDED.status,lines=EXCLUDED.lines,meta=EXCLUDED.meta",
                        (kind, id_, doc["warehouse"], doc["status"], Jsonb(doc["lines"]), Jsonb(doc["meta"])),
                    )
        if after["ledger"][: len(before["ledger"])] != before["ledger"]:
            raise RuntimeError("ledger history is append-only; demo reset is forbidden in PostgreSQL mode")
        for i, row in enumerate(after["ledger"][len(before["ledger"]) :], start=len(before["ledger"]) + 1):
            conn.execute(
                "INSERT INTO pro_wms.ledger VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (i, row["sku"], row["warehouse"], row["ref_type"], row["ref_id"], row["qty_delta"], row["note"]),
            )
        conn.execute(
            "UPDATE pro_wms.revision SET wave_seq=%s, users=%s WHERE id=1", (after["wave_seq"], Jsonb(after["users"]))
        )


if __name__ == "__main__":
    migrate(os.environ["DATABASE_URL"])
