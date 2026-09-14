"""In-process WMS kernel used by CLI tests until the FastAPI service is up."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from threading import RLock
from typing import Literal

from pro_wms_cli.allocation import (
    LotView,
    StockView,
    get_strategy,
    plan_allocations,
    InsufficientForPlan,
)
from pro_wms_cli.errors import (
    IllegalTransition,
    InsufficientStock,
    PermissionDenied,
    StockConflict,
)

Strategy = Literal["fifo", "fefo"]


@dataclass
class Lot:
    id: str
    sku: str
    batch_no: str
    expiry: date | None
    received_at: date


@dataclass
class StockRow:
    warehouse: str
    location: str
    lot_id: str
    qty: int
    version: int = 1


@dataclass
class Doc:
    id: str
    status: str
    warehouse: str
    lines: list[dict]
    meta: dict = field(default_factory=dict)


@dataclass
class LedgerRow:
    sku: str
    warehouse: str
    ref_type: str
    ref_id: str
    qty_delta: int
    note: str = ""


class Warehouse:
    """Thread-safe enough for race tests: snapshot then version-checked commit."""

    INBOUND = {"draft": {"receive"}, "receiving": {"putaway"}, "putting": {"putaway"}, "closed": set()}
    OUTBOUND = {
        "draft": {"allocate"},
        "allocated": {"wave"},
        "waved": {"pick"},
        "picking": {"pick", "ship"},
        "shipped": set(),
    }
    STOCKTAKE = {"draft": {"submit"}, "submitted": {"approve", "reject"}, "approved": set(), "rejected": set()}

    def __init__(self) -> None:
        self._lock = RLock()
        self.warehouses: dict[str, str] = {}
        self.locations: dict[str, str] = {}
        self.skus: dict[str, dict] = {}
        self.lots: dict[str, Lot] = {}
        self.stock: dict[tuple[str, str, str], StockRow] = {}
        self.inbounds: dict[str, Doc] = {}
        self.outbounds: dict[str, Doc] = {}
        self.waves: dict[str, Doc] = {}
        self.stocktakes: dict[str, Doc] = {}
        self.ledger: list[LedgerRow] = []
        self.users: dict[str, str] = {
            "operator": "operator",
            "supervisor": "supervisor",
            "admin": "admin",
        }
        self._wave_seq = 1

    def _role(self, user: str) -> str:
        if user not in self.users:
            raise PermissionDenied(f"unknown user {user}")
        return self.users[user]

    def _require(self, user: str, *roles: str) -> None:
        role = self._role(user)
        if role not in roles and role != "admin":
            raise PermissionDenied(f"{user} needs {roles}")

    def _move(self, doc: Doc, table: dict[str, set[str]], action: str, dest: str) -> None:
        if action not in table.get(doc.status, set()):
            raise IllegalTransition(f"{doc.id}: cannot {action} from {doc.status}")
        doc.status = dest

    def _stock_key(self, warehouse: str, location: str, lot_id: str) -> tuple[str, str, str]:
        return (warehouse, location, lot_id)

    def _add_stock(self, warehouse: str, location: str, lot_id: str, qty: int) -> None:
        key = self._stock_key(warehouse, location, lot_id)
        row = self.stock.get(key)
        if row is None:
            self.stock[key] = StockRow(warehouse, location, lot_id, qty, 1)
        else:
            row.qty += qty
            row.version += 1

    def _write_ledger(self, sku: str, warehouse: str, ref_type: str, ref_id: str, qty: int, note: str = "") -> None:
        self.ledger.append(LedgerRow(sku, warehouse, ref_type, ref_id, qty, note))

    def seed_demo(self) -> dict:
        with self._lock:
            self.warehouses = {"WH-EAST": "East DC", "WH-WEST": "West DC"}
            self.locations = {
                "EAST-DOCK": "WH-EAST",
                "EAST-A-01-01": "WH-EAST",
                "EAST-A-01-02": "WH-EAST",
                "WEST-A-01-01": "WH-WEST",
            }
            self.skus = {
                "SKU-MILK": {"name": "Fresh milk", "shelf_life_days": 14},
                "SKU-BOLT": {"name": "M8 bolt", "shelf_life_days": None},
            }
            today = date(2026, 9, 14)
            self.lots = {
                "LOT-M1": Lot("LOT-M1", "SKU-MILK", "B20260901", today + timedelta(days=5), today - timedelta(days=10)),
                "LOT-M2": Lot("LOT-M2", "SKU-MILK", "B20260910", today + timedelta(days=12), today - timedelta(days=2)),
                "LOT-B1": Lot("LOT-B1", "SKU-BOLT", "B-BOLT-1", None, today - timedelta(days=30)),
            }
            self.stock = {}
            self._add_stock("WH-EAST", "EAST-A-01-02", "LOT-M1", 4)
            self._add_stock("WH-EAST", "EAST-A-01-02", "LOT-M2", 6)
            self._add_stock("WH-WEST", "WEST-A-01-01", "LOT-B1", 100)
            self.inbounds = {
                "IN-1001": Doc(
                    "IN-1001",
                    "draft",
                    "WH-EAST",
                    [{"sku": "SKU-MILK", "qty": 8, "lot": "LOT-M2", "from": "EAST-DOCK"}],
                )
            }
            self.outbounds = {
                "OUT-2001": Doc("OUT-2001", "draft", "WH-EAST", [{"sku": "SKU-MILK", "qty": 3}])
            }
            self.waves = {}
            self.stocktakes = {
                "ST-3001": Doc(
                    "ST-3001",
                    "submitted",
                    "WH-EAST",
                    [{"sku": "SKU-MILK", "location": "EAST-A-01-02", "lot": "LOT-M1", "counted": 4}],
                    {"submitted_by": "operator"},
                )
            }
            self.ledger = []
            self._wave_seq = 1
        return {"warehouses": list(self.warehouses), "inbounds": ["IN-1001"], "outbounds": ["OUT-2001"]}

    def inbound_receive(self, inbound_id: str, user: str = "operator") -> Doc:
        self._require(user, "operator", "supervisor")
        with self._lock:
            doc = self.inbounds[inbound_id]
            self._move(doc, self.INBOUND, "receive", "receiving")
            for line in doc.lines:
                lot = self.lots[line["lot"]]
                self._add_stock(doc.warehouse, line.get("from", "EAST-DOCK"), lot.id, int(line["qty"]))
                self._write_ledger(line["sku"], doc.warehouse, "inbound-receive", doc.id, int(line["qty"]))
            return deepcopy(doc)

    def inbound_putaway(self, inbound_id: str, to_location: str, user: str = "operator") -> Doc:
        self._require(user, "operator", "supervisor")
        with self._lock:
            doc = self.inbounds[inbound_id]
            self._move(doc, self.INBOUND, "putaway", "closed")
            if to_location not in self.locations:
                raise IllegalTransition(f"unknown location {to_location}")
            for line in doc.lines:
                lot_id = line["lot"]
                src = line.get("from", "EAST-DOCK")
                qty = int(line["qty"])
                self._consume(doc.warehouse, src, lot_id, qty, expected_version=None)
                self._add_stock(doc.warehouse, to_location, lot_id, qty)
                self._write_ledger(line["sku"], doc.warehouse, "inbound-putaway", doc.id, 0, f"{src}->{to_location}")
            return deepcopy(doc)

    def _lot_view(self, lot: Lot) -> LotView:
        return LotView(lot.id, lot.sku, lot.batch_no, lot.expiry, lot.received_at)

    def _stock_view(self, row: StockRow) -> StockView:
        return StockView(row.warehouse, row.location, row.lot_id, row.qty, row.version)

    def _candidates(self, warehouse: str, sku: str, strategy: Strategy) -> list[StockRow]:
        rows = []
        for row in self.stock.values():
            if row.warehouse != warehouse or row.qty <= 0:
                continue
            lot = self.lots[row.lot_id]
            if lot.sku != sku:
                continue
            rows.append(row)
        strat = get_strategy(strategy)
        rows.sort(key=lambda r: strat.rank_key(self._stock_view(r), self._lot_view(self.lots[r.lot_id])))
        return rows

    def _consume(
        self,
        warehouse: str,
        location: str,
        lot_id: str,
        qty: int,
        expected_version: int | None,
    ) -> None:
        key = self._stock_key(warehouse, location, lot_id)
        row = self.stock[key]
        if expected_version is not None and row.version != expected_version:
            raise StockConflict(f"{key} version {row.version} != {expected_version}")
        if row.qty < qty:
            raise InsufficientStock(f"{key} has {row.qty}, need {qty}")
        row.qty -= qty
        row.version += 1

    def snapshot_candidates(self, warehouse: str, sku: str, strategy: Strategy) -> list[StockRow]:
        with self._lock:
            return deepcopy(self._candidates(warehouse, sku, strategy))

    def allocate_outbound(
        self,
        outbound_id: str,
        strategy: Strategy = "fefo",
        user: str = "operator",
        before_commit: Callable[[], None] | None = None,
    ) -> Doc:
        self._require(user, "operator", "supervisor")
        doc = self.outbounds[outbound_id]
        if "allocate" not in self.OUTBOUND.get(doc.status, set()):
            raise IllegalTransition(f"{doc.id}: cannot allocate from {doc.status}")
        plan_lines = []
        for line in doc.lines:
            need = int(line["qty"])
            snapped = self.snapshot_candidates(doc.warehouse, line["sku"], strategy)
            lot_views = {lid: self._lot_view(lot) for lid, lot in self.lots.items()}
            try:
                planned = plan_allocations(
                    need=need,
                    candidates=[self._stock_view(r) for r in snapped],
                    lots=lot_views,
                    strategy=strategy,
                    as_of=date(2026, 9, 14),
                    min_remaining_shelf_days=line.get("min_shelf_days"),
                )
            except InsufficientForPlan as exc:
                raise InsufficientStock(f"{line['sku']} short {exc.short}") from exc
            plan_lines.extend(planned)
        if before_commit:
            before_commit()
        with self._lock:
            if "allocate" not in self.OUTBOUND.get(doc.status, set()):
                raise IllegalTransition(f"{doc.id}: cannot allocate from {doc.status}")
            for line in plan_lines:
                self._consume(line.warehouse, line.location, line.lot_id, line.qty, expected_version=line.version_seen)
                sku = self.lots[line.lot_id].sku
                self._write_ledger(sku, doc.warehouse, "allocate", doc.id, -line.qty, strategy)
            doc.meta["strategy"] = strategy
            doc.meta["allocations"] = [
                {
                    "lot": line.lot_id,
                    "location": line.location,
                    "qty": line.qty,
                    "version_seen": line.version_seen,
                }
                for line in plan_lines
            ]
            doc.status = "allocated"
            return deepcopy(doc)

    def wave_create(self, outbound_ids: list[str], user: str = "supervisor") -> Doc:
        self._require(user, "supervisor")
        with self._lock:
            wave_id = f"WV-{2000 + self._wave_seq}"
            self._wave_seq += 1
            lines = []
            for oid in outbound_ids:
                doc = self.outbounds[oid]
                self._move(doc, self.OUTBOUND, "wave", "waved")
                lines.extend([{"outbound": oid, **line} for line in doc.lines])
            wave = Doc(wave_id, "waved", self.outbounds[outbound_ids[0]].warehouse, lines, {"outbounds": outbound_ids})
            self.waves[wave_id] = wave
            return deepcopy(wave)

    def wave_pick(self, wave_id: str, user: str = "operator") -> Doc:
        self._require(user, "operator", "supervisor")
        with self._lock:
            wave = self.waves[wave_id]
            if wave.status not in {"waved", "picking"}:
                raise IllegalTransition(f"{wave.id}: cannot pick from {wave.status}")
            wave.status = "picking"
            for oid in wave.meta["outbounds"]:
                doc = self.outbounds[oid]
                if doc.status == "waved":
                    doc.status = "picking"
                self._move(doc, self.OUTBOUND, "ship", "shipped")
            wave.status = "shipped"
            return deepcopy(wave)

    def stocktake_approve(self, stocktake_id: str, user: str = "supervisor") -> Doc:
        self._require(user, "supervisor")
        with self._lock:
            doc = self.stocktakes[stocktake_id]
            self._move(doc, self.STOCKTAKE, "approve", "approved")
            for line in doc.lines:
                # approval confirms counted qty; ledger a zero-delta audit if match
                self._write_ledger(line["sku"], doc.warehouse, "stocktake-approve", doc.id, 0, "approved")
            return deepcopy(doc)

    def qty_on_hand(self, sku: str, warehouse: str | None = None) -> int:
        total = 0
        for row in self.stock.values():
            if warehouse and row.warehouse != warehouse:
                continue
            if self.lots[row.lot_id].sku == sku:
                total += row.qty
        return total

    def race_allocate(self, sku: str, warehouse: str, workers: int = 8, each_qty: int = 3) -> dict:
        """Each worker tries to allocate `each_qty` of sku; versions make all but one fail."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        created: list[str] = []
        with self._lock:
            for i in range(workers):
                oid = f"OUT-RACE-{i}"
                self.outbounds[oid] = Doc(oid, "draft", warehouse, [{"sku": sku, "qty": each_qty}])
                created.append(oid)

        results = {"ok": [], "conflict": [], "other": []}

        def work(oid: str) -> tuple[str, str]:
            try:
                self.allocate_outbound(oid, strategy="fefo", before_commit=lambda: __import__("time").sleep(0.01))
                return oid, "ok"
            except StockConflict:
                return oid, "conflict"
            except Exception as exc:
                return oid, f"other:{exc}"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(work, oid) for oid in created]
            for fut in as_completed(futs):
                oid, status = fut.result()
                if status == "ok":
                    results["ok"].append(oid)
                elif status == "conflict":
                    results["conflict"].append(oid)
                else:
                    results["other"].append({"id": oid, "error": status})
        results["on_hand"] = self.qty_on_hand(sku, warehouse)
        return results


    def to_dict(self) -> dict:
        return {
            "warehouses": self.warehouses,
            "locations": self.locations,
            "skus": self.skus,
            "lots": {k: v.__dict__ for k, v in self.lots.items()},
            "stock": [
                {
                    "warehouse": r.warehouse,
                    "location": r.location,
                    "lot_id": r.lot_id,
                    "qty": r.qty,
                    "version": r.version,
                }
                for r in self.stock.values()
            ],
            "inbounds": {k: v.__dict__ for k, v in self.inbounds.items()},
            "outbounds": {k: v.__dict__ for k, v in self.outbounds.items()},
            "waves": {k: v.__dict__ for k, v in self.waves.items()},
            "stocktakes": {k: v.__dict__ for k, v in self.stocktakes.items()},
            "ledger": [r.__dict__ for r in self.ledger],
            "users": self.users,
            "wave_seq": self._wave_seq,
        }

    def load_dict(self, data: dict) -> None:
        from datetime import date as date_cls

        def dparse(value):
            if value is None:
                return None
            if isinstance(value, date_cls):
                return value
            return date_cls.fromisoformat(value)

        self.warehouses = data.get("warehouses", {})
        self.locations = data.get("locations", {})
        self.skus = data.get("skus", {})
        self.lots = {
            k: Lot(
                v["id"],
                v["sku"],
                v["batch_no"],
                dparse(v.get("expiry")),
                dparse(v["received_at"]),
            )
            for k, v in data.get("lots", {}).items()
        }
        self.stock = {}
        for row in data.get("stock", []):
            key = (row["warehouse"], row["location"], row["lot_id"])
            self.stock[key] = StockRow(
                row["warehouse"],
                row["location"],
                row["lot_id"],
                row["qty"],
                row["version"],
            )
        self.inbounds = {k: Doc(**v) for k, v in data.get("inbounds", {}).items()}
        self.outbounds = {k: Doc(**v) for k, v in data.get("outbounds", {}).items()}
        self.waves = {k: Doc(**v) for k, v in data.get("waves", {}).items()}
        self.stocktakes = {k: Doc(**v) for k, v in data.get("stocktakes", {}).items()}
        self.ledger = [LedgerRow(**r) for r in data.get("ledger", [])]
        self.users = data.get("users", self.users)
        self._wave_seq = data.get("wave_seq", 1)
