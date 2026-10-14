# 无密钥证据诊断示例

三段文本和三个问题全部为原创合成夹具。Alpha 与 Beta 不是实际论文，DATA-ALPHA / DATA-BETA 不是实际数据集，数值只服务于演示。

从仓库根目录运行：

```bash
uv run pa eval-evidence --dataset examples/offline-evidence/cases.json --snapshot examples/offline-evidence/corpus.json --mode bm25 --k 2 --output data/evidence-eval/offline-demo.json
```

该命令读取文本快照并运行 BM25，验证语料摘要与标注片段，输出候选与失败阶段。它不下载模型，不调用嵌入/重排/对话服务，也不生成或人工复核答案。

每个 evidence_groups 内任意一个匹配片段可满足该组；跨论文问题使用两个组，要求两篇论文都有依据。即使候选召回齐全，仍不证明最终回答正确。
