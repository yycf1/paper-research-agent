# 问答状态与引用结构校验

问答保留 `(answer, hits)` 的 Python 调用方式；trace、`/api/ask` 和 Agent `ask_library` 返回以下状态。

| answer_status | 含义 |
| --- | --- |
| empty_index | 整个索引为空；Web 仍以 HTTP 400 提示建库 |
| scope_unindexed | 全库有内容，但所选论文没有索引块 |
| no_hits | 本次查询没有召回；不能据此断言原文没答案 |
| low_evidence | 已召回片段的已知重排分数均低于阈值 |
| empty_response | 模型重试后仍为空 |
| citation_invalid | 引用结构修复后仍不合格，返回明确失败提示 |
| refused | 模型以指定的证据不足句式开头；不等于拒答正确性已评估 |
| generated | 正常生成且引用结构校验通过；不等于事实已证实 |

服务不可用在 Web 中为 HTTP 503，在 Agent 中为 degraded 工具结果。BM25/重排回退保留 `retrieval_notes`。`/api/ask` 支持 `paper_id` 或 `paper_ids` 子集，二者互斥，ID 校验后才开始检索。

在线生成检查 `[1]`、连续引用、逗号列表与数字区间；编号必须落在当前上下文范围，非拒答格式的答案必须有编号。失败后最多额外调用一次模型，使用相同证据修复；仍失败时不返回错误草稿，也不自行替换编号。trace 保留修复前后检查以供诊断。

`semantic_support=not_automatically_judged` 始终保留。数字有效只证明指向现有片段，不能证明片段支持该结论；拒答句式识别也是结构规则，仍需原文核对。测试使用脚本化模型，不代表线上模型效果评测。
