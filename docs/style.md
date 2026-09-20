# 编码与文档规范

本文先记录**现存差异**，再给出**统一方案**。规范只约束新增与修订代码，不改业务逻辑与对外接口。

## 一、现存差异（盘点结果）

| 维度 | 分歧现状 | 影响 |
| --- | --- | --- |
| 注释语言 | 代码 docstring 为英文；`README.md`、`seed/README.md` 为中文；`docs/allocation.md` 中英混排 | 阅读时来回切换语言 |
| 模块归属 | `README.md`、`docs/allocation.md`、`openapi.yaml`、`infra/init.sql`、`allocation.py` 中散落 `selyla` / `wayly` / `herry` / `him` 等无指代人名 | 新读者无法理解，疑似生成残留 |
| 日志 | 全项目**没有** `logging`：CLI 用 `print()` 输出到 stdout、错误走 stderr；API 靠 FastAPI 异常处理器返回 JSON | 无级别、无结构，无法接入采集 |
| 错误处理 | 内核抛领域异常；CLI 捕获 `(WmsError, KeyError, RuntimeError)` 后 `print` 并返回 1；API 逐类型注册 handler；`store.api_call` 把 `HTTPError` 压成 `RuntimeError`，丢失错误类型 | 同一错误在不同层表现不一致 |
| 导出 | `pro_wms_cli/__init__.py`、`api/app/__init__.py` 只暴露 `__version__`；各模块均无 `__all__`，`from x import *` 行为不确定 | 公开边界不清 |
| 类型标注 | 多数函数有标注，但 `to_dict()`、`race_allocate()` 返回裸 `dict` | 静态检查失效 |
| 类型别名 | `allocation.StrategyName` 与 `kernel.Strategy` 是同一个 `Literal["fifo","fefo"]` 的两份定义 | 两处需同步改，易漂移 |
| 静态检查 | 两套 `pyproject.toml` 都有 ruff 配置，但 CI **只对 cli 跑 lint**，api 的 dev 依赖里也没有 ruff | api 侧格式无人把关 |

## 二、统一方案（决定）

### 1. 语言
- **面向人的文档**（`README.md`、`docs/`、`*/README.md`）用**中文**。
- **代码注释与 docstring** 用**英文**，与现有代码主体一致，且标识符均为英文。
- 移除 `selyla` / `wayly` / `herry` / `him` 等无指代人名，改为描述职责的措辞。

### 2. 注释与 docstring
采用**精简 Google 风格**：一行摘要（祈使句，句末加句号）；仅当信息不明显时才追加 `Args:` / `Returns:` / `Raises:` / `Side effects:` / `Note:`。

- 禁止逐行复述代码。
- 必须写清：**入参含义与单位、返回值结构、对共享状态的副作用、并发与幂等相关的注意事项**。
- 模块 docstring 用一句话说明"本模块负责什么、不负责什么"。

### 3. 命名
- 模块/函数/变量：`snake_case`；类：`PascalCase`；常量：`UPPER_CASE`；
  内部实现（非公开）：单下划线前缀。
- 布尔语义变量用 `is_` / `has_` / `should_` 前缀。
- 类型别名统一在**定义处**声明一次，其他模块导入复用，不再重复定义。

### 4. 日志
统一使用标准库 `logging`，经 `pro_wms_cli.logging_setup.configure()` 一次性配置：

- **级别约定**：`DEBUG` 排查细节；`INFO` 关键状态流转（播种、状态迁移）；`WARNING` 可恢复的异常（版本冲突、库存不足）；`ERROR` 操作失败。
- **输出方向**：日志一律走 `stderr`。**CLI 的 JSON 结果仍打印到 stdout**，这是 CLI 的对外契约，不受日志改造影响。
- 禁止在库代码里直接 `print()`。

### 5. 错误处理
- 领域错误一律继承 `pro_wms_cli.errors.WmsError`，禁止抛裸 `Exception` 或 `RuntimeError` 表达业务语义。
- 内核只抛领域异常；**HTTP 状态码映射集中在 API 层的异常处理器**，一处维护。
- 调用方捕获时按类型处理，不用宽泛 `except Exception`；确实需要兜底的地方（并发压测）必须在注释中写明原因。

### 6. 导出
- 每个模块显式声明 `__all__`，只列出对外契约的一部分。
- 包 `__init__.py` 仅导出版本号与稳定的顶层符号，不承担业务逻辑。

### 7. 格式与静态检查
- 行宽 120、`ruff` 规则集 `E,F,I,UP`，两个子项目一致。
- CI 对 **cli 与 api 都跑 lint**；api 的 dev 依赖补齐 ruff。

### 8. 类型标注
- 公开函数全部标注；容器类型写完整（`dict[str, Any]` 而非 `dict`）。
