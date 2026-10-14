# 安装与使用

## 安装

要求 Python 3.11+ 与 uv。在仓库根目录执行 `uv sync --locked`。GitHub 页面展示名为 Paper Research Agent，Python 分发名仍为 `paper-agent`，导入名为 `paper_agent`，命令为 `pa`。

仓库包含 `web/dist`，启动 Web 不要求 Node.js。修改前端时需 Node.js 20.19+ 或 22.12+，在 `web` 目录运行 `npm ci` 与 `npm run build`。

## 配置

复制 `.env.example` 为 `.env`，填写自己的 `LLM_API_KEY`。`config.yaml` 中三个端点可分别调整：

| 配置节 | 用途 | 默认模型 |
| --- | --- | --- |
| `llm` | 结构提取、精读报告、查询改写、问答与 Agent 决策 | `deepseek-ai/DeepSeek-V3.2` |
| `embedding` | 文档/查询向量 | `BAAI/bge-m3` |
| `reranker` | 候选重排序 | `BAAI/bge-reranker-v2-m3` |

每节的 `api_key_env` 指定自己的密钥变量。默认使用兼容端点；模型可用性、延迟和费用由实际服务商决定，仓库不附赠额度。Web 设置写入 `data/settings.json`，非空值覆盖对话模型配置；Embedding 和 Reranker 继续使用 YAML 与各自的环境变量。

`data_dir` 默认 `data`，保存账本、文献、解析文本、抽取、向量库、会话和任务轨迹。不要提交该目录或 `.env`。

## Web 流程

```bash
uv run pa serve --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。首次使用库为空：

1. 在“发现论文”检索主题、预览并点击“保存并下载全文”：先保存信息，再后台获取所选全文，逐篇显示成功、失败或无链接；下载不自动进入模型分析。也可选择“仅保存信息”，或在“文献库”导入自己的 PDF/DOCX。
2. 点论文标题进入独立阅读页，开始处理并观察五个阶段。是否能提问依据实际索引状态；“已建索引”记录已完成的步骤，不等于当前版本必然就绪。“全文文件”检查实际本地文件，来源链接不等于下载成功。
3. 在“针对本文提问”使用单篇范围；也可勾选 1–6 篇到“研究助手 → 问答与比较”问答，或明确选择全库。连续对话保留历史、默认全库，不自动检索入库。
4. 勾选 2–6 篇，点击“比较所选论文”，输入主题后查看字段依据和实验条件缺项/差异。`待核对` 是候选状态，`缺少证据` 不等于论文没有报告。
5. “执行任务”用于明确提交多步目标，左侧可新建或切换 Agent 历史；查看历史不会中止当前任务。连续对话可以将需求预填为任务，仍需点击“提交任务”。“任务记录”集中查看处理与 Agent 过程。普通处理记录只保留本次服务启动期间，Agent 历史从本地轨迹恢复；历史回读不等于自动恢复执行。
6. 勾选后可移动分组或删除论文。删除前列出实际范围，包括切换筛选前勾选的项目；取消不发送删除请求。删除成功项取消勾选，失败项保留勾选，可重试。删除论文与移出分组不同。

本次界面改造以电脑端为验收范围。默认浅色工作台，已保存的深色选择继续生效。阅读中的操作失败显示局部错误，保留已加载正文。

API 文档在 `/docs`。默认仅监听本机，不作为公网多用户服务部署。

## 常用 CLI

```bash
uv run pa --help
uv run pa search "graph neural network recommendation" --max 5
uv run pa add ./paper.pdf
uv run pa parse --all
uv run pa analyze --all
uv run pa index --all
uv run pa ask "论文使用了哪些数据集？" --paper <论文ID>
uv run pa agent "从库中选择两篇已处理论文，对比数据集与指标并说明依据；不要运行代码。"
uv run pa status
uv run pa serve
```

`analyze`、`index`、`ask` 和 `agent` 等涉及配置的模型服务；`doctor` 会进行网络自检。具体参数以 `uv run pa <命令> --help` 为准。

## 离线证据诊断

```bash
uv run pa eval-evidence --dataset examples/offline-evidence/cases.json --snapshot examples/offline-evidence/corpus.json --mode bm25 --k 2 --output data/evidence-eval/offline-demo.json
```

这是随仓库提供的原创合成夹具，不需要个人文献库，也不调用模型。报告中没有生成答案，语义复核状态会保持未复核。

诊断自己的已索引文献时，先用 `uv run pa evidence-snapshot` 保存快照，再运行 `eval-evidence`。默认离线 BM25；向量/重排/生成答案必须显式使用 `--live` 并具备可用服务。`review-evidence` 对保存产物复核，不重新调用模型。详见[证据级说明](证据级评测与学习说明.md)。

## MCP

MCP Host 配置示例（替换成安装目录中的实际可执行文件路径）：

```json
{
  "mcpServers": {
    "paper-research-agent": {
      "command": "/absolute/path/to/project/.venv/bin/pa",
      "args": ["mcp"]
    }
  }
}
```

Windows 对应 `.venv\\Scripts\\pa.exe`。启动命令为 `uv run pa mcp`；Web、CLI 和 MCP 使用同一文献库与工具执行器。

宿主需支持当前 SDK 的确认交互。拒绝/取消或响应不完整不会自动放行；旧宿主的 `PAPER_AGENT_CONFIRM` 运维开关见[历史实现说明](devlog/m27.md)。不要为省去确认而默认批准代码执行。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| API Key 或余额错误 | 核对端点、模型与 api_key_env；Web 设置只覆盖对话模型 |
| 有论文列表但不能回答正文 | 候选尚未下载/解析/索引，或当前只有摘要；先检查处理状态 |
| 在线论文源限流 | 看降级信息，可更换来源或导入有权使用的本地文件 |
| Windows 中文路径索引重开失败 | 从项目根目录启动；VectorStore 尝试以 ASCII 相对路径打开同一索引 |
| 修改前端后页面未变化 | 在 web 目录重新构建，再刷新页面 |
| 旧证据标签或人工复核失效 | 原文或分块/引用集可能已变化，按当前版本重新核对 |

复现扩展使用本机子进程和工作区白名单，尚无容器隔离。仅在自己理解且信任代码时确认执行。
