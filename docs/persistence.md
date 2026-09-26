# 库存模型与持久化（B 批）

## 数量口径

- `qty` / `on_hand` 是实物库存，包括已预占但未发运的货物。
- `reserved` 是出库预占量；`available = qty - reserved`。
- 分配只增加预占；取消 draft/allocated 单据释放预占；已入波次的单据暂不支持取消。
- 现有 `/waves/{id}/pick` 仍合并拣货与发运，但现在真正扣减实物、释放预占并写发运流水。
- 上架只能移动可用库存，不能移动已预占的数量。
- 演示初始化记录期初流水，按 SKU、仓库核对 `sum(ledger.qty_delta) == on_hand`。
- 数量仍为正整数；重量、金额与 Decimal 不属于本批。盘点批准仍只留审计，不调账。

## 运行方式

CLI 本地演示保持零运行依赖。API 默认是进程内模式；它支持失败回滚，但数据不跨进程共享。
持久使用应设置 `PRO_WMS_REPOSITORY=postgres`，数据库不可用时不会回退为内存。

```powershell
python -m pip install -e ./cli -e './api[dev]'
$env:PRO_WMS_REPOSITORY = 'postgres'
$env:DATABASE_URL = 'postgresql://wms:your-password@127.0.0.1:5432/pro_wms'
python -m app.postgres
uvicorn app.main:app --port 8080
```

`python -m app.postgres` 在事务内应用 `api/app/migrations/*.sql` 的已知版本，重复执行不重置数据。
当前使用独立 `pro_wms` schema；不会导入、覆盖或删除旧 `infra/init.sql` 在 public schema 的草案表。
数据库账号需要建 schema/表权限；本版本 API 容器启动时执行迁移。

Compose：复制 `.env.example` 为 `.env` 并设置密码，然后执行：

```sh
docker compose --profile full up --build
```

`/health` 检查进程存活；`/health/ready` 检查存储连接及迁移版本。默认仅映射本机回环端口。
Redis 不参与本版本；已有 Redis 数据卷不会被这份配置删除。

## 演示与身份边界

API 的 `/seed/demo`、`/race` 默认禁用。明确设置 `PRO_WMS_ENABLE_DEMO=1` 才能演示。
PostgreSQL 下 seed 只允许空库初始化，重复重置被拒绝；race 在 PostgreSQL 下始终禁用。
内存模式仍可重置演示数据。

真实登录认证尚未实现，`X-User` 仍是演示身份，不能将本服务直接开放给不可信客户端。
本批完成持久化，不代表已具备生产访问控制。

## 事务与幂等

每个 API 写操作使用一个事务：读取状态、执行业务、写库存/单据/流水、写幂等响应，一起提交。
PostgreSQL 库存更新保留行版本条件，数据库约束禁止负库存、超额预占和跨仓库位。
流水只追加。任何写入或响应序列化失败都不会提交业务变更。
内存实现使用副本执行，成功后一次发布，具备对应的回滚行为。

所有 POST 可传 `Idempotency-Key`（1–128 字符）。客户端遇到超时，应使用原 key 和相同参数重试。
相同 key、操作、有效身份和参数返回首次成功的 JSON；同 key 不同请求返回 409。
失败请求不占用 key；PostgreSQL 的成功记录跨 API 重启保留。key 不是认证凭据。
当前不自动清理幂等记录，避免旧请求在过期后重复执行；后续需定义保留策略。

```sh
# 设置 PRO_WMS_API 后，同一命令重试保留这个 key。
pro-wms --idempotency-key receive-IN-1001 inbound-receive IN-1001
pro-wms outbound-cancel OUT-2001
```

## 明确的性能限制

本批使用全库一条 revision 行串行化写事务，并加载当前完整领域状态；正常提交只写变化的记录。
这是小规模部署的正确性基线，不是高吞吐或高可用承诺。流水增长会增加读取及内存成本。
后续按真实负载将事务缩小到仓库/单据/库存行，并将列表与流水改为分页 SQL 查询。
不应直接取消全局写锁：必须同时解决单据并发、共享序列、幂等记录和一致性读取。

## 兼容与迁移

本版版本号为 0.2.0，JSON 快照增加 `schema_version=2`。
旧版 qty 是分配后剩余量，新版 qty 是实物量；自动照搬会少算已预占库存，因此旧快照明确拒绝加载。
已有数据应保留备份，依据未发运分配、历史流水和实际盘点核对后专项迁移。本批没有自动迁移旧数据。
`on_hand` 字段名称保留但语义更正，客户端应分别展示 `on_hand/reserved/available`。

## 验证

设置 `PRO_WMS_TEST_DSN` 为一个允许创建测试数据库的 PostgreSQL 连接，再执行：

```sh
pytest api/tests/test_transactions.py -q
```

每个 PostgreSQL 测试创建并清理随机命名的独立数据库，不重置现有库。
覆盖跨进程争抢、并发相同幂等请求、两个 API 实例共享数据、重新连接恢复、数据库流水写入故障回滚。
未配置 DSN 时数据库用例明确跳过；CI 提供 PostgreSQL 16 服务运行这些用例。
