# pro-wms API

FastAPI 服务，通过事务仓储执行 `pro_wms_cli.kernel.Warehouse`（行为与 CLI 完全一致）。

端口 **8080**。这一层刻意做薄：只负责解析身份、转发内核、序列化结果，
**业务规则一律在内核**，不要在此重写。

## 安装

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ../cli          # cli 未发布到 PyPI，必须先本地安装
pip install -e ".[dev]"
```

## 运行

```bash
cd api
PRO_WMS_ENABLE_DEMO=1 uvicorn app.main:app --host 127.0.0.1 --port 8080
```

让 CLI 指向它：

```bash
export PRO_WMS_API=http://127.0.0.1:8080
pro-wms seed
pro-wms inbound-receive IN-1001
```

PostgreSQL 启动、幂等 key、数量兼容说明见 [持久化文档](../docs/persistence.md)。
新增 `GET /health/ready` 检查存储，`POST /outbounds/{id}/cancel` 取消尚未入波次的出库单。
所有写接口支持 `Idempotency-Key`；同 key 不同操作、有效身份或参数返回 409。

## 身份

按 `X-User` 请求头 → `?as=` 查询参数 → 请求体 `user` 字段的顺序解析。

**未提供身份时按最小权限 `operator` 执行**，因此 `/waves`、
`/stocktakes/{id}/approve` 这类需要 `supervisor` 的接口必须显式声明身份，
否则返回 403。

## 接口

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/health` | `{status: ok}` |
| GET | `/snapshot` | 内核全量快照（`to_dict()` 输出） |
| POST | `/seed/demo` | 显式演示模式；内存重置，PostgreSQL 仅空库初始化 |
| POST | `/inbounds/{id}/receive` | 可选 `X-User` / `?as=` / body `user`（默认 operator） |
| POST | `/inbounds/{id}/putaway` | body `{to}`，默认 operator |
| POST | `/outbounds/{id}/allocate` | body `{strategy: fifo\|fefo}`，默认 operator |
| POST | `/waves` | body `{outbounds: [ids]}`，**需 supervisor** |
| POST | `/waves/{id}/pick` | 默认 operator |
| POST | `/stocktakes/{id}/approve` | **需 supervisor** |
| POST | `/race` | body `{sku, warehouse, workers}` |
| GET | `/ledger?sku=&warehouse=` | `on_hand` / `reserved` / `available` + 流水行 |

## 错误

| 领域异常 | 状态码 |
| --- | --- |
| `InvalidRequest` | 400 |
| `PermissionDenied` | 403 |
| `NotFound`（文档 / 批次 id 不存在） | 404 |
| `IllegalTransition` / `StockConflict` / `InsufficientStock` | 409 |
| 未预期异常（含内核内部 `KeyError`） | 500 |

响应体统一为 `{"detail": "...", "error": "<异常类名>"}`。
映射集中在 `app/main.py` 的异常处理器，新增领域异常时只改那一处。

**注意**：`KeyError` 不再被映射为 404。内核只对"调用方传入的 id 查不到"抛 `NotFound`；
内部字典键错误一律 500，避免真实缺陷被伪装成"资源不存在"。

## CORS

默认只放行本地开发来源（5173 / 8080）。用 `PRO_WMS_ALLOWED_ORIGINS`
（逗号分隔）覆盖；不使用通配符，因为它无法与带凭据的请求共存。

## 测试

```bash
cd api && ruff check . && mypy && pytest -q
```

默认使用内存；设置 `PRO_WMS_TEST_DSN` 后运行真实 PostgreSQL 用例，测试账号需具备创建测试库权限。

`tests/test_contract.py` 会双向比对 `../contracts/openapi.yaml` 与 `app.openapi()`：
路径与方法必须两边一致，OpenAPI 版本号与 `app.__version__` 必须相同，文档里的
`strategy` 枚举必须等于内核的 `STRATEGIES`。接口增删却忘了改契约会直接失败。
