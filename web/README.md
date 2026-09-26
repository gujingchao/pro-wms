# pro-wms web

Vue 3 + Vite + TypeScript 管理台，对接 FastAPI（默认 `http://127.0.0.1:8080`）。

## 功能模块

| 路由 | 说明 |
| --- | --- |
| `/` | 仪表盘 / 一键 `POST /seed/demo`、`GET /snapshot` 内核快照 |
| `/inbound` | `IN-1001` receive → putaway |
| `/outbound` | `OUT-2001` allocate（FIFO/FEFO） |
| `/waves` | 创建波次、pick |
| `/stocktake` | `ST-3001` approve（supervisor） |
| `/ledger` | 按 sku/warehouse 查流水与 on_hand |
| `/race` | 并发竞态演示 |

壳层：侧栏导航、API Base URL、角色（`X-User`: operator / supervisor / admin）、
仓库切换（WH-EAST / WH-WEST）、连接健康检查。
每次变更后右侧 JSON 面板 + 403/409 banner。

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

## Snapshot

`GET /snapshot` 返回内核全量快照（`Warehouse.to_dict()` 的 JSON 化输出）。
`src/api/client.ts` 的 `getSnapshot()` 在端点不可用（404）时优雅回退到本地 demo 状态。

## 契约

对齐 `../contracts/openapi.yaml` 与 `../api/README.md`。
前端不承载业务规则，改动 `api/` / `cli/` 行为时请同步更新契约。

## 0.2 库存口径

库存流水页面分别显示实物、预占、可用数量；分配不减少实物，发运才扣减。
出库页可取消尚未入波次的单据。API 断线或报错时清空快照并提示，不再回退演示库存。
API 演示入口需要服务端显式启用，数据库模式只允许空库初始化。
运行客户端回归检查：`node --test tests/client.test.mjs`。
