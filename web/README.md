# pro-wms web（wayly）

Vue 3 + Vite + TypeScript 管理台，对接 FastAPI（默认 `http://127.0.0.1:8080`）。

## 功能模块

| 路由 | 说明 |
| --- | --- |
| `/` | 仪表盘 / 一键 `POST /seed/demo`、可选 `getSnapshot()` |
| `/inbound` | `IN-1001` receive → putaway |
| `/outbound` | `OUT-2001` allocate（FIFO/FEFO） |
| `/waves` | 创建波次、pick |
| `/stocktake` | `ST-3001` approve（supervisor） |
| `/ledger` | 按 sku/warehouse 查流水与 on_hand |
| `/race` | 并发竞态演示 |

壳层：侧栏导航、API Base URL、角色（`X-User`: operator / supervisor）、仓库切换（WH-EAST / WH-WEST）、连接健康检查。每次变更后右侧 JSON 面板 + 403/409 banner。

## 开发

先启动 API：

```bash
cd ../api
source .venv/bin/activate   # 或按 api/README 安装
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

再启动前端：

```bash
cd web
npm install
npm run dev
```

生产构建：

```bash
npm run build
npm run preview
```

## Snapshot TODO

内核 `Warehouse.to_dict()` 已存在，但 API **尚无** `GET /snapshot`。客户端 `src/api/client.ts` 的 `getSnapshot()` 会尝试该端点，遇 404 时优雅回退到本地 demo 状态，并留有 TODO 注释。

## 契约

对齐 `../contracts/openapi.yaml` 与 `../api/README.md`。勿改动 `api/` / `cli/` 行为。
