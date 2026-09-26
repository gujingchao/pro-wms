"""Transactional execution boundary shared by memory and PostgreSQL backends."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from threading import RLock
from typing import Any, Protocol

from pro_wms_cli.errors import InvalidRequest, StockConflict
from pro_wms_cli.kernel import Warehouse

__all__ = ["MemoryRepository", "Repository", "jsonable", "validate_key"]


def jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.loads(json.dumps(value, default=str))


def validate_key(key: str | None) -> None:
    if key is not None and (not key.strip() or len(key) > 128):
        raise InvalidRequest("Idempotency-Key must contain 1..128 characters")


class Repository(Protocol):
    def execute(
        self, action: Callable[[Warehouse], Any], *, write: bool = False, key: str | None = None, fingerprint: str = ""
    ) -> Any: ...

    def ready(self) -> None: ...


class MemoryRepository:
    """Copy-on-write unit of work: failed operations never publish partial state."""

    def __init__(self) -> None:
        self.warehouse = Warehouse()
        self._lock = RLock()
        self._receipts: dict[str, tuple[str, Any]] = {}

    def ready(self) -> None:
        return None

    def execute(
        self, action: Callable[[Warehouse], Any], *, write: bool = False, key: str | None = None, fingerprint: str = ""
    ) -> Any:
        validate_key(key)
        with self._lock:
            if write and key is not None and key in self._receipts:
                old_fingerprint, response = self._receipts[key]
                if old_fingerprint != fingerprint:
                    raise StockConflict("Idempotency-Key was already used for another request")
                return deepcopy(response)
            working = Warehouse()
            working.load_dict(deepcopy(self.warehouse.to_dict()))
            result = jsonable(action(working))
            if write:
                self.warehouse = working
                if key is not None:
                    self._receipts[key] = (fingerprint, deepcopy(result))
            return result
