# FIFO / FEFO 分配与乐观并发（him）

## 模块

- `cli/src/pro_wms_cli/allocation.py`：可插拔策略 + `plan_allocations`
- 内核 `allocate_outbound`：snapshot → plan →（可选 before_commit 注入竞态）→ version-checked commit

## 策略

| 策略 | 排序键（升序优先） |
| --- | --- |
| FIFO | `received_at`, `lot_id`, `location` |
| FEFO | `expiry`（空排最后）, `received_at`, `lot_id`, `location` |

支持行级 `min_shelf_days`：效期剩余不足的批次在 FEFO/FIFO 计划中直接跳过。

## 并发

1. 读快照候选（带 `version`）
2. 纯函数规划（不改库存）
3. 提交时 `expected_version` 不匹配 → `StockConflict`
4. Postgres 落地用 `UPDATE ... WHERE version = :seen AND qty >= :take`（见 `infra/init.sql`）
5. Redis 只做热点锁/缓存，不是库存真相

## 对齐

- CLI：`pro-wms race` / pytest `test_race_has_conflicts`、`test_fifo_and_fefo_diverge_*`
- API：`POST /outbounds/{id}/allocate?strategy=fefo|fifo` 返回 `meta.allocations[].version_seen`
- 管理台：拣货页展示策略与冲突提示（wayly）
