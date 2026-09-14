"""Domain errors."""


class WmsError(Exception):
    """Base."""


class IllegalTransition(WmsError):
    """Status machine rejected the move."""


class StockConflict(WmsError):
    """Optimistic version mismatch."""


class InsufficientStock(WmsError):
    """Not enough allocatable qty."""


class PermissionDenied(WmsError):
    """RBAC failed."""
