# pro-wms API

FastAPI 服务，包装进程内的 `pro_wms_cli.kernel.Warehouse`（行为与 CLI 完全一致）。

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
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

让 CLI 指向它：

```bash
export PRO_WMS_API=http://127.0.0.1:8080
pro-wms seed
pro-wms inbound-receive IN-1001
```

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
| POST | `/seed/demo` | 装入演示数据（**会清空现有状态**） |
| POST | `/inbounds/{id}/receive` | 可选 `X-User` / `?as=` / body `user`（默认 operator） |
| POST | `/inbounds/{id}/putaway` | body `{to}`，默认 operator |
| POST | `/outbounds/{id}/allocate` | body `{strategy: fifo\|fefo}`，默认 operator |
| POST | `/waves` | body `{outbounds: [ids]}`，**需 supervisor** |
| POST | `/waves/{id}/pick` | 默认 operator |
| POST | `/stocktakes/{id}/approve` | **需 supervisor** |
| POST | `/race` | body `{sku, warehouse, workers}` |
| GET | `/ledger?sku=&warehouse=` | `on_hand` + 流水行 |

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
cd api && ruff check . && pytest -q
```

仅用内存内核，不依赖 Docker。
