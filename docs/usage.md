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

1. 在线检索论文后勾选入库，或上传自己的 PDF/DOCX。
2. 打开详情并“一键处理”，观察下载、解析、提取、报告与索引的阶段状态。
3. 在“针对本文提问”或助手对话中问数据集、方法、实验设置，展开引用核对原文。
4. 在“字段依据”查看研究目标、数据集和指标。`待核对` 是候选状态，`缺少证据` 不等于论文没有报告。
5. 切到 Agent 任务比较已有论文，查看工具步骤与历史；批量入库和代码运行按规则请求确认。

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
