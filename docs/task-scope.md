# 任务论文范围

论文范围由任务发起方指定，模型只能选择范围内的子集。单篇任务使用 `anchor_source_id`；多篇任务使用 `allowed_paper_ids`（1–6 篇）。未指定范围的既有全库任务保持全库模式。

```json
POST /api/agent/tasks
{
  "task": "比较这两篇论文的实验设置",
  "allowed_paper_ids": ["openalex:W1", "openalex:W2"]
}
```

CLI 对应 `pa agent "比较实验设置" --paper openalex:W1 --paper openalex:W2 --trace`。ID 必须存在于当前文献库；简写仅接受唯一完整后缀，存在歧义时需提供完整 ID。空数组、未知 ID、超过六篇或与单篇锚定冲突的范围被拒绝。

范围任务隐藏发现、入库与方向推荐工具。问答、比较、读取方案、写代码和执行代码都在读取文件或创建工作区前检查论文 ID。省略问答或比较的 ID 时使用任务集合；显式 ID 不能扩大集合。BM25 和向量检索共享 `$in` 范围过滤，重排及生成前再次检查证据归属。任务提示、轮询状态和 JSONL 历史保存实际集合；历史比较保留字段证据。

这约束的是工具参数与检索数据范围，不是操作系统级代码沙箱。模型生成的 Python 仍是本机子进程，执行确认不证明其安全。结构范围检查也不证明答案得到原文的语义支持。

验证入口：`tests/test_task_scope.py`、`tests/test_agent_anchor.py`、`tests/test_api_agent.py`。测试使用临时账本、手写向量及脚本化模型，不调用线上模型。
