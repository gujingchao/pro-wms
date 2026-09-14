# Demo seed

`pro-wms seed` 装入：

- 仓：`WH-EAST` / `WH-WEST`
- 库位：仓-区-货架-位（如 `EAST-A-01-01`）
- SKU：`SKU-MILK`（效期）/ `SKU-BOLT`
- 多批次不同效期，方便 FEFO
- 入库单 `IN-1001`（draft，含批次效期）
- 出库单 `OUT-2001`（draft，数量贴近库存以便打竞态）
- 盘点单 `ST-3001`（submitted，待主管审批）
- 用户：`operator` / `supervisor` / `admin`
