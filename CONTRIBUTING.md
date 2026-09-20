# 参与贡献

感谢关注 pro-wms！无论是报告缺陷、补充文档还是提交代码，都欢迎。

## 提交 Issue

报告缺陷时请尽量包含：

- 复现步骤（CLI 命令或 API 请求）
- 期望行为与实际行为
- Python 版本与操作系统

## 开发环境

```bash
git clone https://github.com/gujingchao/pro-wms.git
cd pro-wms/cli
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# API 侧（改 api/ 才需要）
cd ../api
pip install -e ../cli && pip install -e ".[dev]"
```

## 提交前自检

```bash
cd cli && ruff check . && pytest
cd ../api && ruff check . && pytest -q
```

两项全部通过再提交。CI 会对 cli 与 api 分别跑 lint 与测试。

## 代码规范

完整规范见 [`docs/style.md`](docs/style.md)，要点：

- **注释语言**：代码注释与 docstring 用英文；面向人的文档（README、docs/）用中文。只求一致，不混用。
- **注释风格**：精简 Google 风格——一行摘要，必要时追加 `Args:` / `Returns:` / `Raises:` / `Side effects:`。不逐行复述代码。
- **错误**：领域错误继承 `WmsError`；HTTP 状态码映射只在 `api/app/main.py` 一处维护。
- **日志**：用 `logging`（`pro_wms_cli.logging_setup`），日志走 stderr；CLI 的 JSON 结果走 stdout，这是对外契约。
- **导出**：模块显式声明 `__all__`。
- **格式**：行宽 120，ruff 规则 `E,F,I,UP`。

## 测试约定

- 内核测试注意状态机顺序：outbound 必须 `draft → allocate → wave → pick/ship`，
  直接对 draft 建波会得到 `IllegalTransition`。
- 涉及效期/日期的测试用 `date.today()` 或注入 `as_of`，不要硬编码日历日期。
- 改动并发相关逻辑后，跑 `pro-wms race --workers 8` 确认冲突仍被正确挡住。

## 提交信息

使用简洁的祈使句，例如：

```
fix(kernel): reject empty wave instead of raising IndexError
docs: rewrite README as a project introduction
```

## License

提交即表示你同意以 [MIT](LICENSE) 许可证发布贡献内容。
