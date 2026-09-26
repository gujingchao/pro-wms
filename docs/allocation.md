# FIFO / FEFO 分配与乐观并发

## 模块

- `cli/src/pro_wms_cli/allocation.py`：可插拔策略 + `plan_allocations`（纯函数）
- 内核 `allocate_outbound`：快照 → 规划 →（可选 `before_commit` 注入竞态）→ 带版本校验的提交

## 策略

| 策略 | 排序键（升序优先） |
| --- | --- |
| FIFO | `received_at`, `lot_id`, `location` |
| FEFO | `expiry`（空排最后）, `received_at`, `lot_id`, `location` |

支持行级 `min_shelf_days`：剩余效期不足的批次在 FEFO / FIFO 计划中直接跳过。
同一出库单内多个相同 SKU 的行会**先按 SKU 聚合需求量再规划一次**，避免两行互相争抢同一批库存行；
若多行给出不同的 `min_shelf_days`，取其中最严格的值。

**硬规则**：效期已过的批次永不参与分配，即使该行未设 `min_shelf_days`。
参考日期为 `as_of`（默认 `date.today()`），测试可注入以保持确定性。

## 并发

1. 读快照候选（每行带 `version`）
2. 纯函数规划（不改库存）
3. 提交前在内存中按顺序预演整份计划（逐行校验 `version` 与可用量），全部通过才落库
4. 预演不通过 → `StockConflict` / `InsufficientStock`，**库存与单据状态均不变**（全有或全无）
5. 分配增加 reserved，实物 qty 不变；可用量为 qty - reserved
6. PostgreSQL 事务串行化提交并校验行版本；取消释放预占，发运扣减实物（见 `persistence.md`）

失败语义：冲突是**预期结果**而非缺陷——调用方应重试（重新快照 → 重新规划）。
内核同一单据重复分配仍报 `IllegalTransition`；API 同一 `Idempotency-Key` 重试返回原成功结果。

## 对齐

- CLI：`pro-wms race` / pytest `test_race_has_conflicts`、`test_fifo_and_fefo_diverge_*`
- API：`POST /outbounds/{id}/allocate（JSON body 的 strategy 为 fefo/fifo）` 返回 `meta.allocations[].version_seen`
- 管理台：拣货页展示策略与冲突提示
