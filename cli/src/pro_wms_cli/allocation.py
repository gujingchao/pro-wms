"""Pluggable stock allocation strategies (FIFO / FEFO).

This module is pure: it ranks candidates and plans allocations without touching any
store. The caller (`kernel.Warehouse`) owns mutation and must commit each planned
line with the `version_seen` recorded here — that version is the optimistic-lock
token, not a cache key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol

__all__ = [
    "STRATEGIES",
    "AllocationLine",
    "AllocationStrategy",
    "FefoStrategy",
    "FifoStrategy",
    "InsufficientForPlan",
    "LotView",
    "StockView",
    "StrategyName",
    "get_strategy",
    "plan_allocations",
]

StrategyName = Literal["fifo", "fefo"]


@dataclass(frozen=True)
class LotView:
    """Read-only projection of a lot used for ranking."""

    id: str
    sku: str
    batch_no: str
    expiry: date | None
    received_at: date


@dataclass(frozen=True)
class StockView:
    """Read-only projection of a stock row used for ranking and version checks."""

    warehouse: str
    location: str
    lot_id: str
    qty: int
    version: int


@dataclass(frozen=True)
class AllocationLine:
    """One planned pick, plus the version it was planned against."""

    warehouse: str
    location: str
    lot_id: str
    qty: int
    version_seen: int


class AllocationStrategy(Protocol):
    name: StrategyName

    def rank_key(self, stock: StockView, lot: LotView) -> tuple[Any, ...]:
        """Ascending sort key: earlier = preferred."""
        ...


class FifoStrategy:
    """Oldest receipt first."""

    name: StrategyName = "fifo"

    def rank_key(self, stock: StockView, lot: LotView) -> tuple[Any, ...]:
        return (lot.received_at, lot.id, stock.location)


class FefoStrategy:
    """Soonest expiry first; undated lots sort last."""

    name: StrategyName = "fefo"

    def rank_key(self, stock: StockView, lot: LotView) -> tuple[Any, ...]:
        # Missing expiry sorts last so undated industrial SKUs don't steal FEFO priority.
        return (lot.expiry or date.max, lot.received_at, lot.id, stock.location)


STRATEGIES: dict[StrategyName, AllocationStrategy] = {
    "fifo": FifoStrategy(),
    "fefo": FefoStrategy(),
}


def get_strategy(name: StrategyName) -> AllocationStrategy:
    """Look a strategy up by name.

    Raises:
        ValueError: unknown strategy name.
    """
    try:
        return STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown allocation strategy: {name}") from exc


class InsufficientForPlan(Exception):
    """Raised when eligible stock cannot cover `need`.

    Attributes:
        short: How many units are still missing after planning.
    """

    def __init__(self, short: int) -> None:
        self.short = short
        super().__init__(f"short {short}")


def plan_allocations(
    *,
    need: int,
    candidates: list[StockView],
    lots: dict[str, LotView],
    strategy: StrategyName,
    as_of: date | None = None,
    min_remaining_shelf_days: int | None = None,
) -> list[AllocationLine]:
    """Plan how to cover `need` units from `candidates`. Pure — mutates nothing.

    Args:
        need: Requested quantity; zero or negative returns an empty plan.
        candidates: Stock rows to consider. Must already be scoped to the target
            warehouse and SKU by the caller.
        lots: Lot lookup keyed by `lot_id`, used for expiry and receipt dates.
        strategy: Ranking strategy name.
        as_of: Reference date for expiry maths. Defaults to today.
        min_remaining_shelf_days: Optional floor — lots with less remaining shelf
            life are skipped, even if not yet expired.

    Returns:
        Allocation lines in ranked order, possibly splitting across several lots.
        Each line carries the `version` observed on its source row.

    Raises:
        ValueError: unknown strategy.
        InsufficientForPlan: eligible stock cannot cover `need` — note that lots
            already past `expiry` are never eligible, regardless of
            `min_remaining_shelf_days`.
    """
    if need <= 0:
        return []
    strat = get_strategy(strategy)
    today = as_of or date.today()
    eligible: list[StockView] = []
    for row in candidates:
        if row.qty <= 0:
            continue
        lot = lots[row.lot_id]
        # Never ship stock that has already passed its expiry date.
        if lot.expiry is not None and lot.expiry < today:
            continue
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


# Reference SQL for a future Postgres-backed commit — mirrors _consume's semantics:
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
