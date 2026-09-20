# 演示数据

`pro-wms seed`（对应 `POST /seed/demo`）装入一套开箱可跑的演示数据：

| 类别 | 内容 |
| --- | --- |
| 仓库 | `WH-EAST` / `WH-WEST` |
| 库位 | 仓-区-货架-位（如 `EAST-A-01-01`） |
| SKU | `SKU-MILK`（有 shelf_life_days）/ `SKU-BOLT`（无效期） |
| 批次 | `LOT-M1` / `LOT-M2` 效期不同，用于演示 FEFO；`LOT-B1` 无效期 |
| 单据 | 入库单 `IN-1001`（draft）、出库单 `OUT-2001`（draft）、盘点单 `ST-3001`（submitted，待审批） |
| 用户 | `operator` / `supervisor` / `admin` |

**注意**：

- 批次的效期与收货日期**相对 `date.today()` 生成**，所以任何时间运行演示，FEFO 与
  效期校验的行为都是一致的，不会因数据老化而失真。
- `seed` 是**破坏性操作**：会清空当前全部库存、单据与流水。
- 出库单数量贴近库存总量，方便用 `pro-wms race` 复现并发冲突。
