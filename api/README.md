# pro-wms API

FastAPI service wrapping the in-process `pro_wms_cli.kernel.Warehouse` (same behaviors as CLI).

Owned by **selyla**. Port **8080**.

## Install

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../cli
pip install -e ".[dev]"
```

Or with requirements:

```bash
pip install -e ../cli
pip install -r requirements.txt
pip install -e .
```

## Run

```bash
cd api
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Point the CLI at it:

```bash
export PRO_WMS_API=http://127.0.0.1:8080
pro-wms seed
pro-wms inbound-receive IN-1001
```

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | `{status: ok}` |
| POST | `/seed/demo` | multi-warehouse demo |
| POST | `/inbounds/{id}/receive` | optional `X-User` / `?as=` / body `user` (default operator) |
| POST | `/inbounds/{id}/putaway` | body `{to}` |
| POST | `/outbounds/{id}/allocate` | body `{strategy: fifo\|fefo}` |
| POST | `/waves` | body `{outbounds: [ids]}` (default user supervisor) |
| POST | `/waves/{id}/pick` | |
| POST | `/stocktakes/{id}/approve` | default user supervisor |
| POST | `/race` | body `{sku, warehouse, workers}` |
| GET | `/ledger?sku=&warehouse=` | on_hand + ledger rows |

Errors: `IllegalTransition`/`StockConflict`/`InsufficientStock` → 409; `PermissionDenied` → 403; missing doc → 404.

CORS: `*`.

## Tests

```bash
cd api && pytest -q
```

In-memory kernel only (no Docker).
