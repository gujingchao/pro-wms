"""Domain errors raised by the WMS kernel.

Every error here inherits `WmsError`, so callers can catch the whole family.
The HTTP layer maps these to status codes in one place (`api/app/main.py`);
the kernel itself knows nothing about HTTP.

Mapping (see api/README.md):
    IllegalTransition / StockConflict / InsufficientStock -> 409
    PermissionDenied                                      -> 403
    InvalidRequest                                        -> 400
    NotFound                                              -> 404
"""

from __future__ import annotations

__all__ = [
    "IllegalTransition",
    "InsufficientStock",
    "InvalidRequest",
    "NotFound",
    "PermissionDenied",
    "StockConflict",
    "WmsError",
]


class WmsError(Exception):
    """Base class for all domain errors."""


class IllegalTransition(WmsError):
    """The status machine rejected the requested move."""


class StockConflict(WmsError):
    """Optimistic version mismatch: another writer moved this row first."""


class InsufficientStock(WmsError):
    """Not enough eligible stock to satisfy the request."""


class PermissionDenied(WmsError):
    """RBAC check failed for the given identity."""


class InvalidRequest(WmsError):
    """Caller sent a malformed or empty request (maps to HTTP 400)."""


class NotFound(WmsError):
    """A referenced document, lot or master-data row does not exist (maps to HTTP 404).

    Raised only for lookups the caller supplied an id for; internal dictionary key
    errors stay `KeyError` on purpose so they surface as bugs instead of 404s.
    """
