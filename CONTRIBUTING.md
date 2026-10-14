# 贡献与问题反馈

先查看 [README](README.md)、[架构说明](docs/architecture.md) 和 [验证边界](docs/validation.md)。功能建议请描述实际任务、当前限制及期望结果。

提交问题时提供：操作步骤或命令、Python/Node/系统版本、预期与实际行为、脱敏日志。不要附 API Key、`.env`、本地数据库、个人简历或无权公开的文献全文。

## 开发验证

```bash
uv sync --locked --dev
uv run pytest -q
uv run pa --help
```

前端改动还需执行 `cd web`、`npm ci`、`npm run build` 和 `npm run lint`，并同步 `web/dist`。测试使用假模型或本地夹具，常规 CI 不调用收费模型。

修改检索时保留阶段 trace；修改字段绑定时同时考虑错论文、过期引用、缺失事实和语义支持未知。模型回答、引用定位和人工语义复核分别记录，不能用关键词命中替代正确性。

真实模型实验需单独明确端点、数据、预算和结果范围。保存失败与降级信息，不把少量开发样例当独立基准。
