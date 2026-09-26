"""In-process WMS kernel: the single source of truth for warehouse state.

Owns stock, lots, documents (inbound/outbound/wave/stocktake), the ledger and RBAC.
Both the CLI and the FastAPI service call into this class; neither may re-implement
business rules. Persistence is deliberately absent — `to_dict()`/`load_dict()`
serialize the whole kernel to a JSON snapshot.

Concurrency model: readers take a versioned snapshot, planners work on that snapshot
without mutating anything, and the commit re-checks each row's `version`. A mismatch
raises `StockConflict`, so lost updates are detected rather than silently applied.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from threading import Event, Lock, RLock
from typing import Any, TypedDict

from pro_wms_cli.allocation import (
    AllocationLine,
    InsufficientForPlan,
    LotView,
    StockView,
    StrategyName,
    get_strategy,
    plan_allocations,
)
from pro_wms_cli.errors import (
    IllegalTransition,
    InsufficientStock,
    InvalidRequest,
    NotFound,
    PermissionDenied,
    StockConflict,
)
from pro_wms_cli.logging_setup import get_logger

__all__ = [
    "MAX_RACE_WORKERS",
    "RACE_WINDOW_SECONDS",
    "Doc",
    "LedgerRow",
    "Lot",
    "RaceResult",
    "StockRow",
    "Strategy",
    "Warehouse",
]

#: Allocation strategy accepted by the kernel. Alias kept for brevity at call sites.
Strategy = StrategyName

#: Upper bound on `race` workers: the harness spawns one thread per worker, so an
#: unbounded value would let a single call exhaust the process.
MAX_RACE_WORKERS = 32

#: Seconds a race worker waits for its peers before committing on its own.
RACE_WINDOW_SECONDS = 1.0

_LOG = get_logger("kernel")


@dataclass
class Lot:
    """A received batch of one SKU.

    `expiry` is None for non-perishable goods; allocation treats those as never
    expiring and sorts them last under FEFO.
    """

    id: str
    sku: str
    batch_no: str
    expiry: date | None
    received_at: date


@dataclass
class StockRow:
    """Quantity of one lot at one location.

    `version` is the optimistic-concurrency token: it must match at commit time,
    and every mutation bumps it so stale writers are rejected.
    """

    warehouse: str
    location: str
    lot_id: str
    qty: int
    version: int = 1


@dataclass
class Doc:
    """A workflow document (inbound / outbound / wave / stocktake).

    `lines` is a list of plain dicts whose keys depend on the document type; `meta`
    carries type-specific extras such as chosen strategy or allocation breakdown.
    """

    id: str
    status: str
    warehouse: str
    lines: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LedgerRow:
    """Immutable audit entry.

    `qty_delta` is the signed stock movement; zero means "recorded for audit only"
    (e.g. putaway moves between locations, stocktake approval).
    """

    sku: str
    warehouse: str
    ref_type: str
    ref_id: str
    qty_delta: int
    note: str = ""


class RaceResult(TypedDict):
    """Outcome of `race_allocate`, split by how each competing worker ended.

    A healthy run has a non-empty `ok`, a non-empty `conflict` and an empty `other`;
    `other` lists workers that failed for an unexpected reason.
    """

    ok: list[str]
    conflict: list[str]
    other: list[dict[str, str]]
    on_hand: int


class Warehouse:
    """In-memory warehouse state machine.

    Thread-safety: every mutation runs under `self._lock` (an RLock), and stock
    commits additionally re-check row versions, so concurrent workers either win or
    fail loudly. Callers must not cache `Doc`/`StockRow` objects across calls —
    most methods return deep copies.
    """

    #: status -> set of actions allowed from that status.
    INBOUND: dict[str, set[str]] = {
        "draft": {"receive"},
        "receiving": {"putaway"},
        "putting": {"putaway"},
        "closed": set(),
    }
    OUTBOUND: dict[str, set[str]] = {
        "draft": {"allocate"},
        "allocated": {"wave"},
        "waved": {"pick"},
        "picking": {"pick", "ship"},
        "shipped": set(),
    }
    STOCKTAKE: dict[str, set[str]] = {
        "draft": {"submit"},
        "submitted": {"approve", "reject"},
        "approved": set(),
        "rejected": set(),
    }

    def __init__(self) -> None:
        self._lock = RLock()
        self.warehouses: dict[str, str] = {}
        self.locations: dict[str, str] = {}
        self.skus: dict[str, dict[str, Any]] = {}
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

    def _ensure_move(self, doc: Doc, table: dict[str, set[str]], action: str) -> None:
        """Raise unless `action` is legal from `doc.status`. Does not mutate."""
        if action not in table.get(doc.status, set()):
            raise IllegalTransition(f"{doc.id}: cannot {action} from {doc.status}")

    def _move(self, doc: Doc, table: dict[str, set[str]], action: str, dest: str) -> None:
        self._ensure_move(doc, table, action)
        doc.status = dest

    @staticmethod
    def _doc(table: dict[str, Doc], doc_id: str) -> Doc:
        """Look a document up, converting a missing id into `NotFound`."""
        try:
            return table[doc_id]
        except KeyError:
            raise NotFound(f"{doc_id} not found") from None

    def _lot(self, lot_id: str) -> Lot:
        """Look a lot up, converting a missing id into `NotFound`."""
        try:
            return self.lots[lot_id]
        except KeyError:
            raise NotFound(f"lot {lot_id} not found") from None

    def _stock_key(self, warehouse: str, location: str, lot_id: str) -> tuple[str, str, str]:
        return (warehouse, location, lot_id)

    @staticmethod
    def _positive_quantity(value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise InvalidRequest("quantity must be a positive integer")
        return value

    def _validate_location(self, warehouse: str, location: str) -> None:
        if warehouse not in self.warehouses:
            raise InvalidRequest(f"unknown warehouse {warehouse}")
        if location not in self.locations:
            raise IllegalTransition(f"unknown location {location}")
        if self.locations[location] != warehouse:
            raise InvalidRequest(f"location {location} does not belong to {warehouse}")

    def _receipt_lines(self, doc: Doc) -> list[tuple[Lot, str, int, str]]:
        """Validate all inbound lines before any stock or status mutation."""
        if not doc.lines:
            raise InvalidRequest("inbound requires at least one line")
        receipts: list[tuple[Lot, str, int, str]] = []
        for line in doc.lines:
            if not {"sku", "lot", "qty"} <= line.keys():
                raise InvalidRequest("inbound line requires sku, lot and qty")
            lot = self._lot(line["lot"])
            sku = line["sku"]
            if sku not in self.skus or lot.sku != sku:
                raise InvalidRequest(f"SKU {sku} does not match lot {lot.id}")
            qty = self._positive_quantity(line["qty"])
            src = line.get("from", "EAST-DOCK")
            self._validate_location(doc.warehouse, src)
            receipts.append((lot, src, qty, sku))
        return receipts

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

    def seed_demo(self) -> dict[str, list[str]]:
        """Reset the kernel to the demo dataset.

        Returns:
            A small summary: `{"warehouses": [...], "inbounds": [...], "outbounds": [...]}`.

        Side effects:
            Destructive — discards all stock, documents and ledger history.
            Dates are generated relative to `date.today()` so shelf-life maths stays
            meaningful however long after release this runs.
        """
        _LOG.info("seeding demo dataset (destructive reset)")
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
            # Relative to today so the demo (and its shelf-life maths) never goes stale.
            today = date.today()
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
        """Book goods onto the dock: `draft -> receiving`.

        Args:
            inbound_id: Existing inbound document id.
            user: Caller identity; must be `operator` or `supervisor`.

        Returns:
            A deep copy of the document in status `receiving`.

        Side effects:
            Adds stock at the line's `from` location (default `EAST-DOCK`) and appends
            a positive ledger entry per line.

        Raises:
            PermissionDenied: caller lacks the required role.
            InvalidRequest: empty lines, invalid quantities or mismatched SKU/location.
            IllegalTransition: document is not in `draft`.
            NotFound: the inbound id or one of its lot ids does not exist.
        """
        self._require(user, "operator", "supervisor")
        with self._lock:
            doc = self._doc(self.inbounds, inbound_id)
            # Validate every line before mutating: a missing lot must not leave the
            # document half-received with stock already booked onto some lines.
            self._ensure_move(doc, self.INBOUND, "receive")
            receipts = self._receipt_lines(doc)
            doc.status = "receiving"
            for lot, src, qty, sku in receipts:
                self._add_stock(doc.warehouse, src, lot.id, qty)
                self._write_ledger(sku, doc.warehouse, "inbound-receive", doc.id, qty)
            return deepcopy(doc)

    def inbound_putaway(self, inbound_id: str, to_location: str, user: str = "operator") -> Doc:
        """Move received goods from the dock to a storage bin: `receiving -> closed`.

        Args:
            to_location: Existing target location in the document's warehouse.

        Side effects:
            Consumes stock at the source and re-adds it at the target. Net on-hand is
            unchanged, so each ledger row carries `qty_delta=0` and records only the
            `src->dst` move.

        Raises:
            IllegalTransition: document not in `receiving`, or unknown location.
            InvalidRequest: invalid line data or a location in another warehouse.
            InsufficientStock: the source row does not hold enough stock to move.
            NotFound: the inbound id does not exist.
        """
        self._require(user, "operator", "supervisor")
        with self._lock:
            doc = self._doc(self.inbounds, inbound_id)
            # Validate the whole move before touching state: a rejected putaway must
            # never leave the document half-transitioned.
            self._ensure_move(doc, self.INBOUND, "putaway")
            self._validate_location(doc.warehouse, to_location)
            receipts = self._receipt_lines(doc)
            required: dict[tuple[str, str, str], int] = {}
            for lot, src, qty, _sku in receipts:
                key = self._stock_key(doc.warehouse, src, lot.id)
                required[key] = required.get(key, 0) + qty
            for key, qty in required.items():
                row = self.stock.get(key)
                have = 0 if row is None else row.qty
                if row is None or have < qty:
                    raise InsufficientStock(f"{key} has {have}, need {qty}")
            for lot, src, qty, sku in receipts:
                self._consume(doc.warehouse, src, lot.id, qty, expected_version=None)
                self._add_stock(doc.warehouse, to_location, lot.id, qty)
                self._write_ledger(sku, doc.warehouse, "inbound-putaway", doc.id, 0, f"{src}->{to_location}")
            doc.status = "closed"
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
        """Deduct `qty` and bump the row version. Caller must hold `self._lock`.

        Args:
            expected_version: Pass the version seen at snapshot time to enable the
                optimistic check; `None` skips it (used by putaway, which moves
                rather than competes for stock).

        Raises:
            StockConflict: `expected_version` given and the row moved on.
            InsufficientStock: remaining quantity is below `qty`.
            KeyError: no such stock row.
        """
        key = self._stock_key(warehouse, location, lot_id)
        row = self.stock[key]
        if expected_version is not None and row.version != expected_version:
            raise StockConflict(f"{key} version {row.version} != {expected_version}")
        if row.qty < qty:
            raise InsufficientStock(f"{key} has {row.qty}, need {qty}")
        row.qty -= qty
        row.version += 1

    def snapshot_candidates(self, warehouse: str, sku: str, strategy: Strategy) -> list[StockRow]:
        """Return deep-copied stock rows eligible for `sku`, best-ranked first.

        Each copy carries the `version` observed at snapshot time, which is what the
        later commit re-checks. Rows with non-positive quantity are excluded.
        """
        with self._lock:
            return deepcopy(self._candidates(warehouse, sku, strategy))

    def allocate_outbound(
        self,
        outbound_id: str,
        strategy: Strategy = "fefo",
        user: str = "operator",
        before_commit: Callable[[], None] | None = None,
        as_of: date | None = None,
    ) -> Doc:
        """Reserve stock for an outbound: `draft -> allocated`.

        Three phases, deliberately separated so the expensive part is pure:
            1. snapshot candidates (each row carries its `version`)
            2. plan greedily via `plan_allocations` — touches no stock
            3. commit the whole plan atomically, with every `version_seen` re-checked,
               so a concurrent writer wins only if nothing moved underneath it

        Planning is aggregated per SKU: several lines of the same SKU in one document
        are covered by a single snapshot and a single plan, so they cannot compete
        with each other for the same rows.

        Args:
            strategy: `fifo` (oldest receipt first) or `fefo` (soonest expiry first).
            before_commit: Optional hook invoked between planning and committing;
                the race harness uses it to widen the window on purpose.
            as_of: Reference date for expiry / shelf-life maths. Defaults to today —
                injectable so tests do not depend on the wall clock.

        Returns:
            A deep copy of the document in status `allocated`, with
            `meta["strategy"]` and `meta["allocations"]` populated (each allocation
            records the `version_seen` it was planned against).

        Side effects:
            Decrements stock per allocation line and appends a negative ledger entry.
            All or nothing: a rejected plan leaves stock and document status untouched.
            Not idempotent: a second call on the same document raises
            `IllegalTransition` because `allocated` permits no further allocation.

        Raises:
            StockConflict: another writer bumped a row's version since the snapshot.
            InsufficientStock: not enough eligible stock, or every candidate expired.
            IllegalTransition: document is not in `draft`.
            NotFound: the outbound id does not exist.
        """
        self._require(user, "operator", "supervisor")
        today = as_of or date.today()
        doc = self._doc(self.outbounds, outbound_id)
        if "allocate" not in self.OUTBOUND.get(doc.status, set()):
            raise IllegalTransition(f"{doc.id}: cannot allocate from {doc.status}")
        need_by_sku: dict[str, int] = {}
        shelf_by_sku: dict[str, int] = {}
        if not doc.lines or doc.warehouse not in self.warehouses:
            raise InvalidRequest("outbound requires a known warehouse and at least one line")
        for line in doc.lines:
            if not {"sku", "qty"} <= line.keys() or line["sku"] not in self.skus:
                raise InvalidRequest("outbound line requires a known sku and qty")
            sku = line["sku"]
            need_by_sku[sku] = need_by_sku.get(sku, 0) + self._positive_quantity(line["qty"])
            floor = line.get("min_shelf_days")
            if floor is not None:
                if type(floor) is not int or floor < 0:
                    raise InvalidRequest("min_shelf_days must be a non-negative integer")
                # Conflicting floors for one SKU: honour the strictest line.
                shelf_by_sku[sku] = max(floor, shelf_by_sku.get(sku, floor))
        lot_views = {lid: self._lot_view(lot) for lid, lot in self.lots.items()}
        plan_lines: list[AllocationLine] = []
        for sku, need in need_by_sku.items():
            snapped = self.snapshot_candidates(doc.warehouse, sku, strategy)
            try:
                plan_lines.extend(
                    plan_allocations(
                        need=need,
                        candidates=[self._stock_view(r) for r in snapped],
                        lots=lot_views,
                        strategy=strategy,
                        as_of=today,
                        min_remaining_shelf_days=shelf_by_sku.get(sku),
                    )
                )
            except InsufficientForPlan as exc:
                raise InsufficientStock(f"{sku} short {exc.short}") from exc
        if before_commit:
            before_commit()
        with self._lock:
            self._ensure_move(doc, self.OUTBOUND, "allocate")
            self._commit_allocations(doc, plan_lines, strategy)
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

    def _commit_allocations(self, doc: Doc, plan_lines: list[AllocationLine], strategy: Strategy) -> None:
        """Deduct every planned line or none at all. Caller must hold `self._lock`.

        Phase 1 replays the plan in memory — simulating cumulative deductions so two
        planned lines on the same row cannot double-spend — and raises before any
        mutation. Phase 2 therefore cannot fail, which keeps document status and stock
        consistent: either the whole allocation lands or nothing changes.
        """
        simulated: dict[tuple[str, str, str], tuple[int, int]] = {}
        for line in plan_lines:
            key = self._stock_key(line.warehouse, line.location, line.lot_id)
            row = self.stock.get(key)
            if row is None:
                raise StockConflict(f"{key} disappeared since the snapshot")
            qty, version = simulated.get(key, (row.qty, row.version))
            if version != line.version_seen:
                raise StockConflict(f"{key} version {version} != {line.version_seen}")
            if qty < line.qty:
                raise InsufficientStock(f"{key} has {qty}, need {line.qty}")
            simulated[key] = (qty - line.qty, version + 1)
        for key, (qty, version) in simulated.items():
            row = self.stock[key]
            row.qty = qty
            row.version = version
        for line in plan_lines:
            self._write_ledger(self._lot(line.lot_id).sku, doc.warehouse, "allocate", doc.id, -line.qty, strategy)

    def wave_create(self, outbound_ids: list[str], user: str = "supervisor") -> Doc:
        """Group allocated outbounds into a pick wave: `allocated -> waved`.

        Args:
            outbound_ids: One or more outbound ids, each already `allocated`.

        Returns:
            A deep copy of the new wave document (id like `WV-2001`).

        Side effects:
            Advances every listed outbound to `waved` and bumps the wave sequence.

        Raises:
            InvalidRequest: `outbound_ids` is empty, repeats an id, or spans several
                warehouses (previously empty input raised IndexError → 500).
            IllegalTransition: an outbound is not `allocated`.
            NotFound: an outbound id does not exist.
        """
        self._require(user, "supervisor")
        if not outbound_ids:
            raise InvalidRequest("wave requires at least one outbound")
        if len(set(outbound_ids)) != len(outbound_ids):
            raise InvalidRequest("wave contains duplicate outbound ids")
        with self._lock:
            # Validate every outbound before moving any: a partial move would leave
            # outbounds in `waved` with no wave document to pick them.
            docs: list[Doc] = []
            for oid in outbound_ids:
                doc = self._doc(self.outbounds, oid)
                self._ensure_move(doc, self.OUTBOUND, "wave")
                docs.append(doc)
            warehouses = {doc.warehouse for doc in docs}
            if len(warehouses) > 1:
                raise InvalidRequest(f"wave spans multiple warehouses: {sorted(warehouses)}")
            wave_id = f"WV-{2000 + self._wave_seq}"
            self._wave_seq += 1
            lines: list[dict] = []
            for doc in docs:
                doc.status = "waved"
                lines.extend([{"outbound": doc.id, **line} for line in doc.lines])
            wave = Doc(wave_id, "waved", docs[0].warehouse, lines, {"outbounds": list(outbound_ids)})
            self.waves[wave_id] = wave
            return deepcopy(wave)

    def wave_pick(self, wave_id: str, user: str = "operator") -> Doc:
        """Complete a wave: each outbound `waved -> picking -> shipped`.

        Returns:
            A deep copy of the wave in status `shipped`.

        Side effects:
            Sets the wave and all its outbounds to `shipped`. Stock was already
            deducted at allocation time, so this writes no ledger rows.

        Raises:
            IllegalTransition: the wave is not `waved`/`picking` (re-picking is
                refused), or one of its outbounds is not pickable.
            NotFound: the wave id, or one of its outbound ids, does not exist.
        """
        self._require(user, "operator", "supervisor")
        with self._lock:
            wave = self._doc(self.waves, wave_id)
            if wave.status not in {"waved", "picking"}:
                raise IllegalTransition(f"{wave.id}: cannot pick from {wave.status}")
            docs = [self._doc(self.outbounds, oid) for oid in wave.meta["outbounds"]]
            # Validate the whole wave before flipping any status, so a bad outbound
            # cannot leave the wave half-shipped.
            for doc in docs:
                self._ensure_move(doc, self.OUTBOUND, "pick")
            for doc in docs:
                self._move(doc, self.OUTBOUND, "pick", "picking")
                self._move(doc, self.OUTBOUND, "ship", "shipped")
            wave.status = "shipped"
            return deepcopy(wave)

    def stocktake_approve(self, stocktake_id: str, user: str = "supervisor") -> Doc:
        """Approve a submitted stocktake: `submitted -> approved`.

        Returns:
            A deep copy of the document in status `approved`.

        Side effects:
            Appends a zero-delta audit row per line. Counted quantities are recorded
            for audit only — approval does not adjust stock.

        Raises:
            PermissionDenied: caller is not `supervisor` or `admin`.
            NotFound: the stocktake id does not exist.
        """
        self._require(user, "supervisor")
        with self._lock:
            doc = self._doc(self.stocktakes, stocktake_id)
            self._move(doc, self.STOCKTAKE, "approve", "approved")
            for line in doc.lines:
                # approval confirms counted qty; ledger a zero-delta audit if match
                self._write_ledger(line["sku"], doc.warehouse, "stocktake-approve", doc.id, 0, "approved")
            return deepcopy(doc)

    def qty_on_hand(self, sku: str, warehouse: str | None = None) -> int:
        """Total physical quantity of `sku`, optionally scoped to one warehouse.

        Includes stock reserved by allocation, because allocation deducts on commit.
        """
        total = 0
        for row in self.stock.values():
            if warehouse and row.warehouse != warehouse:
                continue
            if self.lots[row.lot_id].sku == sku:
                total += row.qty
        return total

    def race_allocate(self, sku: str, warehouse: str, workers: int = 8, each_qty: int = 3) -> RaceResult:
        """Concurrency harness: fire `workers` allocations at the same stock.

        Args:
            workers: Number of competing threads, `1..MAX_RACE_WORKERS`.
            each_qty: Units every competing outbound asks for; must be positive.

        Returns:
            `{"ok": [ids], "conflict": [ids], "other": [{"id", "error"}], "on_hand": int}`
            — a healthy run has a non-empty `ok`, a non-empty `conflict`, and an
            empty `other`.

        Side effects:
            Creates `OUT-RACE-<n>` outbound documents and may deduct stock.
            Intended for tests/demos only.

        Raises:
            InvalidRequest: `workers` or `each_qty` is out of range.

        Note:
            The broad `except` below is deliberate: the harness must classify any
            unexpected failure as `other` instead of aborting the run.
            Workers rendezvous in `before_commit` so they commit against the same
            `version_seen` — the race window is synchronised, not timing-dependent.
        """
        if not 1 <= workers <= MAX_RACE_WORKERS:
            raise InvalidRequest(f"workers must be within 1..{MAX_RACE_WORKERS}, got {workers}")
        if each_qty < 1:
            raise InvalidRequest(f"each_qty must be positive, got {each_qty}")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        created: list[str] = []
        with self._lock:
            for i in range(workers):
                oid = f"OUT-RACE-{i}"
                self.outbounds[oid] = Doc(oid, "draft", warehouse, [{"sku": sku, "qty": each_qty}])
                created.append(oid)

        ok: list[str] = []
        conflict: list[str] = []
        other: list[dict[str, str]] = []
        arrived = 0
        gate = Event()
        gate_lock = Lock()

        def hold_window() -> None:
            """Wait for peers so every worker commits inside the same window.

            Unlike a fixed-size `Barrier` this cannot deadlock: a worker whose peer
            failed during planning still proceeds once the window expires.
            """
            nonlocal arrived
            with gate_lock:
                arrived += 1
                if arrived >= workers:
                    gate.set()
            gate.wait(timeout=RACE_WINDOW_SECONDS)

        def work(oid: str) -> tuple[str, str]:
            try:
                self.allocate_outbound(oid, strategy="fefo", before_commit=hold_window)
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
                    ok.append(oid)
                elif status == "conflict":
                    conflict.append(oid)
                else:
                    other.append({"id": oid, "error": status})
        return RaceResult(ok=ok, conflict=conflict, other=other, on_hand=self.qty_on_hand(sku, warehouse))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the whole kernel to a JSON-safe dict.

        Dates become ISO strings only after `json.dumps(..., default=str)`; this
        method itself returns native `date` objects inside `lots`.
        """
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

    def load_dict(self, data: dict[str, Any]) -> None:
        """Restore state produced by `to_dict()`.

        Args:
            data: Snapshot as produced by `to_dict()`, usually after a JSON round
                trip, so dates arrive as ISO strings.

        Side effects:
            Replaces all collections in place; not additive.
        """

        def dparse(value: Any) -> date | None:
            if value is None:
                return None
            if isinstance(value, date):
                return value
            return date.fromisoformat(value)

        def dparse_required(value: Any) -> date:
            parsed = dparse(value)
            if parsed is None:
                # `received_at` drives FIFO ordering; a null here is a corrupt
                # snapshot, which must fail loudly rather than poison the sort.
                raise ValueError("snapshot has a null date where one is required")
            return parsed

        self.warehouses = data.get("warehouses", {})
        self.locations = data.get("locations", {})
        self.skus = data.get("skus", {})
        self.lots = {
            k: Lot(
                v["id"],
                v["sku"],
                v["batch_no"],
                dparse(v.get("expiry")),
                dparse_required(v["received_at"]),
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
