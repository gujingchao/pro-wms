"""Allow `python -m pro_wms_cli` alongside the installed `pro-wms` console script."""

from __future__ import annotations

from pro_wms_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
