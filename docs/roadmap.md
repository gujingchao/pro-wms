# pro-wms 演进路线

> 下文保留最初规划，不是当前完成清单。B 批采用事务执行边界与串行写入的 PostgreSQL 基线，
> 尚未完成下述通用存储原语抽象、分页、高可用和框架化。当前行为以 [persistence.md](persistence.md) 为准。

目标：**一套高可用、高可扩展的开源 WMS 框架** —— 使用者能零成本跑起来试，能低成本改成自己的业务。

本文是路线规划，不是已完成事项清单。里程碑按依赖顺序排列，每个里程碑都可独立发布、独立验收。

## 现状定位

| 已有资产 | 缺失地基 |
| --- | --- |
| 快照 → 纯函数规划 → 版本号提交（天然适配乐观锁） | 无存储抽象，状态在进程内存里 |
| 可插拔分配策略（`AllocationStrategy` + 注册表） | 服务层有进程级单例，无法多副本 |
| 零依赖 30 秒跑通 + 确定性并发复现 | 写接口不幂等、无分页、无 API 版本 |
| 契约测试 / 类型检查 / CI 三件套 | 无 Dockerfile、无迁移体系、无集成测试 |
| 清晰的领域异常与状态机 | 扩展点覆盖不均（只有分配策略是可插拔的） |

## 贯穿全程的两条硬约束

1. **零依赖演示体验不能退化。** 内存实现必须始终是默认可用路径，`python -m pro_wms_cli seed` 一键跑通不依赖任何外部服务。
2. **业务规则只能有一份。** 新增存储后端只实现"原语"（带版本校验的原子扣减、移库、追加流水），绝不实现规划、排序或策略判断。否则内存版与 Postgres 版的行为会分叉，契约测试也拦不住。

## 里程碑

### M1 · 安全网与存储地基（约 3 人日）

| 交付物 | 说明 |
| --- | --- |
| `cli/tests/test_invariants.py` | 随机操作序列（seed 可复现）跑 500 轮，每轮末校验三条不变量 |
| 状态与规则分离 | 纯数据 `WarehouseState` 与业务规则 `Warehouse` 解耦 |
| `Repository` 原语接口 | 见下方接口草案；`InMemoryRepository` 为默认实现 |
| 锁归位 | `RLock` 从内核移出，成为 `InMemoryRepository` 的实现细节 |

**不变量**（后续每个里程碑都要保持）：

1. `Σ(ledger.qty_delta) == Σ(stock.qty)`
2. 任意操作序列后 `stock.qty >= 0`
3. 已过效期的批次不出现在任何 `allocations` 中

**接口草案**（`cli/src/pro_wms_cli/ports.py`）：

```python
class StockPort(Protocol):
    """存储层需要实现的库存原语。业务规则不在这一层。"""

    def commit_deductions(self, lines: Sequence[AllocationLine]) -> None:
        """全有或全无地扣减；任一行版本不符或数量不足 → StockConflict，且不改动任何行。"""

    def apply_moves(self, moves: Sequence[StockMove]) -> None:
        """上架/移库的原子移动。"""

    def append_ledger(self, rows: Sequence[LedgerRow]) -> None: ...

    def next_seq(self, name: str) -> int: ...

    def save_document(self, doc: Doc) -> None: ...

    def list_stock(self, filters: StockFilter) -> Page[StockRow]: ...
```

**验收**：不变量测试通过；`Warehouse` 内不再出现 `self._lock`；全部既有测试不变绿；CLI 零依赖跑通不变。

### M2 · 可部署与高可用（约 6 人日）

| 交付物 | 说明 |
| --- | --- |
| `PostgresRepository` | 实现 M1 的原语，用 `UPDATE ... WHERE version = :seen AND qty >= :take` 保证原子性 |
| 迁移体系 | `infra/init.sql` 草案转为可版本化迁移（`alembic/`） |
| 服务无状态化 | 去掉 `_WAREHOUSE` 进程单例，按请求/事务取仓储 |
| 幂等键 | POST 支持 `Idempotency-Key`，重放返回同一响应，不重复扣减 |
| 分页与列表端点 | `GET /stock`、`GET /documents` 带游标分页；替代 `/snapshot` 全量 dump |
| 健康检查分离 | `/health/live`（进程存活）与 `/health/ready`（仓储可达） |
| `api/Dockerfile` | 多阶段镜像；修复 `docker-compose.yml` 中 `--profile full` 引用不存在的 Dockerfile |
| 集成测试 | 对真实 Postgres 跑一遍核心流程 |

**验收（可直接演示的高可用证据）**：`docker compose --profile full up` 一条命令起库与 API；启动**两个** API 实例，实例 A 收货 / 分配，实例 B 立刻读到同一结果；重放同一个 `Idempotency-Key` 不会二次扣减。

### M3 · 框架化：把扩展点铺开（约 7 人日）

| 交付物 | 说明 |
| --- | --- |
| 声明式单据类型 | `DocumentType` = 字段 schema + 状态机 + 动作 → 状态 + 动作 → 角色。新增调拨单等类型只需注册 |
| `Doc.lines` 有 schema | 取代当前无校验的 `list[dict[str, Any]]` |
| SPI 注册表 | 把分配策略的成功模式复制到 `PutawayStrategy` / `WavePlanner` / `StocktakeAdjustmentPolicy` |
| `AuthProvider` | 身份解析外移，内核不再持有用户表；先带一个 demo 实现 |
| `EventPublisher` | `doc.status_changed` / `stock.allocated` / `stock.adjusted`，为 webhook、ERP 对接提供挂载点 |
| 库位模型 | `Location` 补 zone / 类型 / 容量 / 混放规则（对齐 `infra/init.sql` 已有列） |
| 数量精度 | `int` → `Decimal`，对齐 `NUMERIC(18, 3)` |

**验收**：新增一种单据类型**不改内核源码**，只注册；注册一个自定义上架策略并接入 `inbound_putaway`；订阅一次领域事件并输出到日志；非法 line 字段被 schema 拒绝。

### M4 · 领域补全与开发者体验（约 6 人日）

| 交付物 | 说明 |
| --- | --- |
| 单据创建 + 批次自动建立 | `inbound-create` / `outbound-create`；收货时按 `skus[sku].shelf_life_days` 推算效期自动建批次 |
| 盘点真实调账 | create / submit / reject / approve；差异按 `实盘 - (可用量 + 在途分配量)` 计算并写调整分录 |
| 出库撤销 | `outbound-cancel` 释放已分配库存并写反向流水 |
| 部分分配（opt-in） | `allow_partial=True` 时分配可用部分，欠量记入 `meta["shortage"]` |
| 破坏性端点默认关闭 | `/race`、`/seed/demo` 需显式开启 |
| 文档 | `docs/adr/`（分配即扣减、过期硬规则、乐观锁选型、存储原语边界）+ `docs/extending.md` + 接入教程 |

**验收**：从零把一个自定义 SKU、库位、策略接进系统，全程只依赖文档、不改内核。

## 排期与依赖

```
M1 安全网与存储地基 ──┬─→ M2 可部署与高可用 ──→ M3 框架化 ──→ M4 领域补全
                      └─（M3 的声明式单据类型依赖 M1 的状态分离）
```

M1 是唯一的前置依赖。M4 中的领域功能彼此独立，可按需调整先后。

## 明确不在本路线内

| 不做 | 原因 |
| --- | --- |
| 完整鉴权实现（密码、会话、令牌签发） | 只提供 `AuthProvider` 接口与 demo 实现；完整实现留给使用者按自己的身份体系接入 |
| 多租户 / 多组织 | 需要贯穿数据模型的租户维度，属于独立课题 |
| 消息队列 / 事件总线选型 | 先提供 `EventPublisher` 接口与进程内实现 |
| Kubernetes / Helm 编排 | 先保证 compose 一条命令可用 |
| 具体性能指标承诺 | 需要真实的负载模型与基线数据 |

## 待确认的决策点

| # | 决策 | 建议 |
| --- | --- | --- |
| 1 | API 版本化方式 | 迁到 `/v1` 并同步改前端（干净）；或根路径与 `/v1` 并存（兼容但两套契约） |
| 2 | 集成测试基础设施 | 建议 compose 驱动，贴近真实部署形态；Testcontainers 需要 CI 开 Docker |
| 3 | 存储驱动与迁移工具 | 建议 psycopg3 + Alembic（`docker-compose.yml` 的 `DATABASE_URL` 已写 `postgresql+psycopg`） |
| 4 | 内存实现是否保持默认 | 建议保持默认（`PRO_WMS_REPOSITORY=memory`），保护零依赖体验与现有测试 |
| 5 | 数量在 JSON 中的表示 | 建议用字符串以避免浮点精度损失，但会改变 CLI 输出契约 |
| 6 | CLI 输出兼容性底线 | 明确 `qty` 由数字改字符串是否可接受 |
