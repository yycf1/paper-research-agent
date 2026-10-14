# 证据级诊断报告

运行：2026-10-13T10:26:09.803543+00:00；语料块：1093。
完成 11 题，检索评分 10 题，答案人工复核 0 题。
模式：bm25；生成答案：否。
语料版本：`663d9f44e0c2695131c41f0f88a5123ef3668a9c8bc6220e6b9f9cc505c4c508`。

本报告区分检索、引用编号与人工事实复核；未复核不等于正确或错误。
标签只覆盖已核对片段；未命中标注证据不等于没有命中其他支持片段。种子集由助手核对解析文本，不是独立盲测。

| 问题 | 标签状态 | 标注证据覆盖 | 首个证据排名 | 诊断 | 答案复核 |
| --- | --- | --- | --- | --- | --- |
| Glaucoma framework 使用了哪些眼底图像数据集？ | 已核对解析文本 | 0.0 | None | 候选中缺少标注证据 | 未生成 |
| 该 glaucoma framework 对图像做了哪些预处理？ | 已核对解析文本 | 0.0 | None | 候选中缺少标注证据 | 未生成 |
| DrugPred 的 drug-drug interaction 建模使用哪个数据集？ | 已核对解析文本 | 1.0 | 8 | 答案尚未生成或复核 | 未生成 |
| DrugPred 在第三阶段采用了哪个文本模型？ | 已核对解析文本 | 0.0 | None | 候选中缺少标注证据 | 未生成 |
| Swin Unet3D 摘要讨论的是哪种维度和模态的医学影像？ | 已核对解析文本 | 0.0 | None | 重排/Top-k 后缺少标注证据 | 未生成 |
| Swin Unet3D 摘要指出 CNN 在学习哪类依赖信息时有局限？ | 已核对解析文本 | 0.0 | None | 重排/Top-k 后缺少标注证据 | 未生成 |
| 哪篇 GIST segmentation 文献报告了 DC、mean IoU 和 accuracy？分别是什么数值？ | 已核对解析文本 | 1.0 | 1 | 答案尚未生成或复核 | 未生成 |
| DrugPred 的 DOI 是什么？ | 已核对解析文本 | 1.0 | 1 | 答案尚未生成或复核 | 未生成 |
| 它训练和测试用了哪些数据集？ | 已核对解析文本 | 0.0 | None | 重排/Top-k 后缺少标注证据 | 未生成 |
| Glaucoma framework 和 DrugPred 分别用了哪些数据集？请分别列出，不比较效果优劣。 | 已核对解析文本 | 0.0 | None | 重排/Top-k 后缺少标注证据 | 未生成 |
| 该 glaucoma framework 的训练 GPU 型号和总耗电量分别是多少？ | 待标注 | 未评分 | — | 缺少有效标签，不评分 | 未生成 |

## 使用限制

- Evidence labels are a checked subset, not exhaustive relevance judgments.
- Missing labeled evidence does not establish that all supporting passages were missed.
- Citation numbering checks do not establish semantic support.
- Unreviewed answers have no factual accuracy score.
- Token usage covers reported chat calls, not embedding or reranking charges.

逐阶段候选、原文与版本摘要见同名 JSON。BM25 离线运行不证明向量检索、重排序或模型回答效果。
