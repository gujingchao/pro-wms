# pro-wms

开源仓库管理系统（一期就按复杂业务做，不是精简版）。

计划仓库：`https://github.com/gujingchao/pro-wms`

| 目录 | 说明 |
| --- | --- |
| `api/` | FastAPI 领域服务（selyla）：多仓、批次效期、入出库状态机、波次、盘点审批、库存并发 |
| `web/` | Vue 3 管理台（wayly） |
| `cli/` | 状态机 CLI + 并发分配集成测试（herry） |
| `infra/` | Docker Compose：Postgres + Redis |
| `seed/` | 多仓 / 批次效期 / 波次 / 待审批盘点种子 |
| `contracts/` | HTTP 契约，给前后端和 CLI 对齐 |

## 一期闭环

多仓收货入位（批次效期）→ 波次拣货出库 → 库存/流水正确 → 盘点审批 → 并发冲突可复现且被乐观版本号挡住。

分配：可插拔 **FIFO / FEFO**（him）；库存扣减乐观版本号 + 行锁，Redis 作锁/缓存而不是唯一真相。

## CLI（内核可独立跑通验收）

```bash
cd cli && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pro-wms seed
pro-wms inbound-receive IN-1001
pro-wms inbound-putaway IN-1001 --to EAST-A-01-01
pro-wms outbound-allocate OUT-2001 --strategy fefo
pro-wms wave-create --outbounds OUT-2001
pro-wms wave-pick WV-2001
pro-wms stocktake-approve ST-3001 --as supervisor
pro-wms race --sku SKU-MILK --warehouse WH-EAST --workers 8
pro-wms ledger --sku SKU-MILK
```

`PRO_WMS_API=http://127.0.0.1:8080` 时 CLI 改打 HTTP，不再走进程内内核。

## API（FastAPI，包装同一内核）

```bash
cd api && python3 -m venv .venv && source .venv/bin/activate
pip install -e ../cli
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then:

```bash
export PRO_WMS_API=http://127.0.0.1:8080
pro-wms seed   # hits POST /seed/demo
```

Endpoints: `/health`, `/seed/demo`, `/inbounds/{id}/receive|putaway`, `/outbounds/{id}/allocate`, `/waves`, `/waves/{id}/pick`, `/stocktakes/{id}/approve`, `/race`, `/ledger`.

See `api/README.md` and `contracts/openapi.yaml`.

## Web（Vue 3 管理台）

```bash
cd web
npm install
npm run dev      # http://localhost:5173 ，默认 API http://127.0.0.1:8080
npm run build
```

模块：仪表盘/种子、入库、出库分配、波次、盘点审批、库存流水、并发竞态。壳层含 API Base URL、角色（X-User）、仓库切换与健康检查。详见 `web/README.md`。

> 说明：`GET /snapshot` 尚未上线；前端 `getSnapshot()` 对 404 回退到本地 demo 状态。

## Compose

```bash
docker compose up -d postgres redis
```

API 镜像等 `api/` Dockerfile 就绪后再开 `full` profile。

## 开发

```bash
cd cli && ruff check . && pytest
cd ../api && pytest -q
```

CI：lint + 状态机 + 并发竞态 + API TestClient。

## License

MIT
