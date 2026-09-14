"""Pluggable stock allocation strategies (FIFO / FEFO).

him owns this module: ranking rules, partial picks, and the optimistic
version contract that _consume must honor. Redis is not the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

StrategyName = Literal["fifo", "fefo"]


@dataclass(frozen=True)
class LotView:
    id: str
    sku: str
    batch_no: str
    expiry: date | None
    received_at: date


@dataclass(frozen=True)
class StockView:
    warehouse: str
    location: str
    lot_id: str
    qty: int
    version: int


@dataclass(frozen=True)
class AllocationLine:
    warehouse: str
    location: str
    lot_id: str
    qty: int
    version_seen: int


class AllocationStrategy(Protocol):
    name: StrategyName

    def rank_key(self, stock: StockView, lot: LotView) -> tuple:
        """Ascending sort key: earlier = preferred."""
        ...


class FifoStrategy:
    name: StrategyName = "fifo"

    def rank_key(self, stock: StockView, lot: LotView) -> tuple:
        return (lot.received_at, lot.id, stock.location)


class FefoStrategy:
    name: StrategyName = "fefo"

    def rank_key(self, stock: StockView, lot: LotView) -> tuple:
        # Missing expiry sorts last so undated industrial SKUs don't steal FEFO priority.
        return (lot.expiry or date.max, lot.received_at, lot.id, stock.location)


STRATEGIES: dict[StrategyName, AllocationStrategy] = {
    "fifo": FifoStrategy(),
    "fefo": FefoStrategy(),
}


def get_strategy(name: StrategyName) -> AllocationStrategy:
    try:
        return STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown allocation strategy: {name}") from exc


def plan_allocations(
    *,
    need: int,
    candidates: list[StockView],
    lots: dict[str, LotView],
    strategy: StrategyName,
    as_of: date | None = None,
    min_remaining_shelf_days: int | None = None,
) -> list[AllocationLine]:
    """Greedy partial allocate. Does not mutate stock; caller commits with version checks."""
    if need <= 0:
        return []
    strat = get_strategy(strategy)
    today = as_of or date.today()
    eligible: list[StockView] = []
    for row in candidates:
        if row.qty <= 0:
            continue
        lot = lots[row.lot_id]
        if min_remaining_shelf_days is not None and lot.expiry is not None:
            if (lot.expiry - today).days < min_remaining_shelf_days:
                continue
        eligible.append(row)
    eligible.sort(key=lambda r: strat.rank_key(r, lots[r.lot_id]))
    plan: list[AllocationLine] = []
    remaining = need
    for row in eligible:
        if remaining <= 0:
            break
        take = min(row.qty, remaining)
        plan.append(
            AllocationLine(
                warehouse=row.warehouse,
                location=row.location,
                lot_id=row.lot_id,
                qty=take,
                version_seen=row.version,
            )
        )
        remaining -= take
    if remaining > 0:
        raise InsufficientForPlan(remaining)
    return plan


class InsufficientForPlan(Exception):
    def __init__(self, short: int) -> None:
        self.short = short
        super().__init__(f"short {short}")


# Suggested Postgres commit (selyla migrations):
#
#   UPDATE stock
#      SET qty = qty - :take, version = version + 1
#    WHERE warehouse_id = :wh AND location_id = :loc AND lot_id = :lot
#      AND version = :version_seen AND qty >= :take
#   RETURNING version;
#
# 0 rows updated => StockConflict (or InsufficientStock if qty raced down).
# Optional hard lock for hot SKUs in the same txn:
#   SELECT id FROM stock WHERE ... FOR UPDATE;
