# paper-agent · 文献阅读复现 Agent

把「论文检索 → 下载 → 解析 → 实验知识提取 → RAG 问答」做成一条本地 CLI 流水线；二期在此之上叠加论文复现能力。规划详见 [PLAN.md](PLAN.md)。

## 当前进度

| 里程碑 | 状态 |
|---|---|
| M0 初始化与环境验证 | ✅ 完成（硅基流动实测连通，BGE-M3 + DeepSeek-V3，`pa doctor` 全绿） |
| M1 检索下载 + 文献库账本 | ✅ 完成（OpenAlex/EuropePMC 实测通过，arXiv 待 proxy） |
| M2 解析 + 本地导入 + 知识提取 | ✅ 完成（PDF/DOCX 解析、experiment.json + report.md 端到端实测通过） |
| M3 RAG 问答 + 评测集 | ✅ 完成（索引 22 篇 142 块，`pa eval` 检索命中率 100%；答案生成实测待 .env 补 `ZHIPU_API_KEY`） |
| M4 编排整合 | ✅ 完成（`pa run` 全链路 + 断点续跑实测通过；analyze→问答两环待 `ZHIPU_API_KEY` 后端到端验收） |
| M5 检索智能化与工具契约 | ✅ 完成（中文查询优化、意图理解一体化、域外拒绝/方向引导、相关性预筛，实测全通过） |
| M6 健壮性收尾与 Web 化地基 | ✅ 完成（状态机盲区根治、GitHub Actions CI、service 层下沉 + CLI 拆包、qa 空回复兜底） |
| M7 Web 服务层 | ✅ 完成（FastAPI REST API、后台任务 + 轮询、`pa serve`，全部端点真实实测） |
| M8 Web 界面 | ✅ 完成（React + TypeScript 文献库/助手双页、一键处理、问答带出处、深链，FastAPI 同端口托管） |
| M9 会话记忆与真实使用修复 | ✅ 完成（对话会话持久化 + 多轮语境、侧栏会话管理、SQLite 线程修复、LLM 拥塞缓解：快路径 + 去重复优化 + 前端超时） |
| M10 库管理自主权 | ✅ 完成（检索预览挑选入库、数据源可选、分组导航、论文删除、本地上传） |
| M11 一致性与静默失败修复 | ✅ 完成（空报告防护 + 存量修复、CLI 检索预览式 + pa prune、qa 出处贯通、跨源重复候选去重、Markdown 渲染与差异化产物展示、未分组入口/分页/防抖） |
| M12 复现规划器（二期第一步） | ✅ 完成（experiment.json + 复现资产 → repro_plan.md：`pa plan` / API / Web「复现方案」页签） |
| M13 分组自主权与高级检索 | ✅ 完成（主动建组/重命名/删除、多选批量归类、详情页改组；标题/摘要限定、作者过滤、引用数/最新排序、最低被引、仅全文、年份区间） |
| M14 Function-calling Agent Loop | ✅ 完成（`pa agent`：LLM 自主调用工具的多步循环——检索预览→挑选入库→库内问答，四态错误回填自纠错、步数熔断软着陆，151 个测试全通过） |
| M15 Agent 护栏硬化与评测体系 | ✅ 完成（循环检测/token 预算/入库上限+HITL 确认/限流自动重试/轨迹存档；检索排序指标 hit@k+MRR+nDCG，agent 任务评测：完成率/工具合规/越界=0/成本） |
| M16 Agent 上 Web | ✅ 完成（助手页「对话/Agent 任务」双模式：REST API 后台任务 + 步骤轨迹实时轮询 + 浏览器内 HITL 确认门 + Markdown 结果与用量展示） |
| M17 设置中心 + 文献库 UI 重构 | ✅ 完成（顶栏齿轮弹窗配置模型厂商/Base URL/API Key，覆盖层 `data/settings.json` 保存即生效无需重启、Key 全程脱敏、一键测试连接；文献库信息架构重排为检索区/过滤区/分组行三区，高级筛选网格化，全局视觉打磨零回归） |
| M18 一键处理透明化 | ✅ 完成（处理链拆为 5 个阶段实时可见：下载/解析/提取实验结构/生成报告/索引，job.result 原地更新流式推进；完成卡/失败卡 + 产物级复用（报告失败重试不重复提取）+ 修复 download_failed 无法重试的存量 bug + DOI 落地页自动跟随 citation_pdf_url 回退；详情页新增「针对本文提问」RAG 问答入口，价值链闭环） |
| M19 项目定位 + 复现执行器 MVP | ✅ 完成（agent 工具面 5→9：新增跨论文对比 compare_papers 与复现执行三件套 read_repro_plan/write_code/run_code——隔离工作区、文件名白名单、独立进程 5~180s 超时、执行前 HITL 确认；评测集扩到 12 任务实测：完成率/合规率/答案命中 100%、越界 0；repro-01 真机 11 步自纠错：模型按复现方案写 numpy 原型 → 读 stderr 迭代修复 5 轮 → 跑通训练 + 自主设计消融实验验证方法关键模块） |
| M20 双主题 + 去拥挤 | ✅ 完成（CSS 全量 token 化重写：颜色收敛为语义变量，`data-theme` 一键换肤；深色/浅色双主题进设置页（迷你色板卡片 + 即时预览），localStorage 防首帧闪烁 + settings.json 持久化跨设备跟随；全站间距放宽、状态徽章/表头换行修复） |
| M21 跨页状态保持 + 整列滚动 | ✅ 完成（文献库/助手双页常驻挂载，切页不卸载组件：Agent 任务时间线与轮询、库页筛选与滚动位置跨页保留，顶栏「助手」执行中小红点呼吸提醒；库页滚动容器上移——检索/过滤/分组区随内容滚走不再常驻占屏，表头 sticky 相对列容器钉住续读，修 border-collapse 下 sticky 表头边框渲染问题） |
| M22 MCP server | ✅ 完成（`pa mcp` 以 stdio 传输把 9 个工具暴露为 MCP 协议服务——Claude Desktop / Cursor 等 host 开箱接入；schema 直接消费 TOOL_REGISTRY 零二次维护，ToolExecutor 与四态语义/硬护栏原样复用，HITL 确认插座映射为 PAPER_AGENT_CONFIRM 环境变量 + stderr 审计；SDK 内存流协议测试 5 用例 + 真实子进程 stdio 冒烟） |
| M23 任务中心：历史 + SSE | ✅ 完成（Agent 任务跨会话可回看：轨迹文件名编入任务 id 作为重启后回读的关联键，列表端点合并 live 注册表与磁盘轨迹、缺 final 记录如实标中断，详情端点从轨迹重建完整时间线；`GET .../events` SSE 推送替代 1.2s 轮询——同进程读共享状态对象增量推送 snapshot/step/status/end 三段式，前端断流自动回退轮询兜底；库页失败行原始报错收敛为「源站拒绝访问（403）」式短语、全文留悬停提示） |
| M24 复现工作区可视化 | ✅ 完成（run_code 真实执行追加写 `runs.jsonl` 运行档案——拒绝的执行不留痕；`GET /papers/{id}/repro` + `/repro/file` 两个只读端点，文件名白名单与执行器同一套；详情页「复现」页签 = 方案 + 工作区文件点击看代码 + 运行记录 exit 码徽章与可展开输出，旗舰功能复现证据链首次全程浏览器可见；顺手修 M16 遗留的确认门「入库」硬编码文案，代码执行确认现正确显示「请求运行代码」） |
| M25 真实数据集复现 | ✅ 完成（CommonsenseQA 官方数据落工作区为复现资产（9,741 训练 / 1,221 验证）；五轮 Agent 协作把合成原型升级为真实数据版 run_real.py——期间记录了 source_id 未锚定导致的任务漂移与清理、数值梯度 2,470 万次前向的死因定位、stdout 块缓冲的超时假象、解析梯度 300 倍加速；最终在真实数据上测得全量验证准确率 22.03%（loss 正常下降、稳定高于随机 20%），差距来源（无 ConceptNet/Wikipedia 知识源、冻结编码器、MLP-only 最小口径）在复现方案资产评估中已写明；17 条运行轨迹含全部失败记录，浏览器「复现」页签逐条可查） |
| M26 任务锚定 + 工具作用域 | ✅ 完成（M25 漂移事故的系统性修复，prompt 建议升级为代码强制：任务 API `anchor_source_id` 校验论文存在，循环层注入锚定约束，执行器**移除并拒绝**检索/入库/方向推荐类工具（最小权限工具集，specs_for 隐藏 + execute 拦截双层防御）；详情页「🤖 交给 Agent 续做（锚定本论文）」一键发起——预填任务 + 锚定提示条，锚随提交走不污染后续任务；CLI `--anchor` 同步；run_code 子进程改 `-u` 无缓冲，超时也能拿到部分输出（修 M25 缓冲假象）；真机 9 步零漂移） |
| M27 MCP elicitation 确认门 | ✅ 完成（HITL 确认从环境变量升级为宿主真弹窗：MCP 2026-07-28 input_required 流——confirm 首触返回 `InputRequiredResult` 内嵌 elicitation 表单，宿主弹窗后客户端带 `input_responses + request_state` 重试原请求，服务端按人的决定放行/拒绝；request_state 归属校验防错配，decline/cancel/响应缺失失败关闭，表单 approve=false 否决 accept；`PAPER_AGENT_CONFIRM` 降级为运维开关与老宿主逃生口；协议层控制流信号用 BaseException 穿透 run_tool 四态分类器；SDK 内存流协议测试 5→10 + 真实 stdio 子进程冒烟（协商 2026-07-28 全回路跑通）） |

## 快速开始

```powershell
# 1. 安装依赖（uv 管理）
python -m uv sync

# 2. 配置密钥：复制 .env.example 为 .env，填入 API Key
#    硅基流动 https://cloud.siliconflow.cn ｜ 智谱 https://open.bigmodel.cn

# 3. 环境自检（M0 验收命令：平台连通 + 图执行）
python -m uv run pa doctor
```

Windows 下若 `pa` 不在 PATH，用 `python -m uv run pa ...` 等价调用。

## 常用命令（M1 起）

```powershell
python -m uv run pa search "基于深度学习的医学影像分割" --max 10   # 检索（默认预览不写库；--pick 1,3-5 选收入库 / --ingest 全收）
python -m uv run pa search "llm medical" --in title --sort citations --min-citations 50 --year-from 2023 --oa   # 高级检索：标题限定+引用排序+最低被引+年份+仅全文
python -m uv run pa tag <id片段...> --set "综述收集"   # 主动归类（--clear 移出分组）
python -m uv run pa download --all          # 下载所有待处理论文的 OA PDF
python -m uv run pa add "路径\论文.pdf"      # 导入本地 PDF/DOCX（中文文献入口）
python -m uv run pa parse --all             # 解析为分节 Markdown
python -m uv run pa analyze --all           # LLM 提取 experiment.json + 精读报告（自动补修历史空报告）
python -m uv run pa index --all             # 分块 + 向量化 + Chroma 索引（增量）
python -m uv run pa run "图神经网络 推荐"    # 一键流水线：检索→选文→下载→解析→提取→索引（LLM 相关性预筛）
python -m uv run pa plan <id>               # 复现规划：experiment.json → repro_plan.md（复现链第一步）
python -m uv run pa ask "论文里用了哪些数据集？"   # 问答（带出处；无参进入多轮 REPL）
python -m uv run pa agent "找 2 篇 2024 年的 GNN 推荐综述入库"   # Agent 模式：LLM 自主调用工具完成多步任务
python -m uv run pa chat                    # 自然语言助手（检索/问答/状态/方向引导/域外拒绝）
python -m uv run pa eval                    # RAG 回归评测（--with-llm 加测答案质量）
python -m uv run pa status                  # 文献库状态总览
python -m uv run pa prune --status discovered [--yes]   # 按状态/分组批量清理（全链删除）
python -m uv run pa doctor                  # 环境自检（平台 + 数据源）
python -m uv run pa serve                   # Web 界面 + REST API（http://127.0.0.1:8000）
python -m uv run pa mcp                     # MCP stdio server（Claude Desktop / Cursor 接入）
```

## 为什么不是又一个「论文问答 ChatBot」

把一篇 PDF 贴给任意对话 AI 也能总结和问答——所以本项目的价值不在「读一篇」，而在五件对话 AI 做不到的事：

1. **它有记忆**：本地文献库是持续累积的资产（SQLite 账本 + 向量索引 + 知识产物），
   跨会话、跨周可用；对话 AI 关掉窗口就忘。
2. **它有手**：接真实学术数据源（OpenAlex/Europe PMC/arXiv）检索、跨源去重、下载真 PDF
   （含 DOI 落地页自动跟随 citation_pdf_url）、解析、索引——不编造「不存在的参考文献」。
3. **它能长时自主干活**：function-calling 循环让模型自主编排多步任务，护栏（步数熔断 /
   token 预算 / 循环检测 / 入库上限 / 代码执行前 HITL 确认）全部代码强制，无人值守可复盘（trace 存档）。
4. **它可被度量**：检索排序指标（hit@k / MRR / nDCG）+ 12 任务 agent 评测集
   （完成率 / 工具合规 / 越界操作 / 成本）+「评测 → 轨迹复盘 → 修护栏 → 复测」闭环。
5. **它能动手验证论文**（M19 复现执行器 MVP，M24 起工作区可视化，M25 上真实数据）：read_repro_plan → write_code → run_code——
   agent 在隔离工作区写最小可运行原型、独立进程执行、读 stderr 自纠错重跑，
   已在真实 CommonsenseQA 数据（9,741/1,221 题）上跑通方案的图推理管线并测得真实
   准确率（最小原型口径 22.0%，与论文 79.3% 的差距来源——无知识源依赖与预训练
   编码器——在复现方案的资产评估中如实写明），详情页「复现」页签可查工作区代码
   与每次运行的 exit 码和输出（含全部失败记录），
   「读 → 答 → 比 → 跑」里对话 AI 完全无法闭环的一环。

Agent 任务模式下这 9 个工具由模型自主编排：search_papers / add_papers /
ask_library / library_status / explore_directions / compare_papers /
read_repro_plan / write_code / run_code。

## Agent 模式（function-calling 循环）

`pa chat` 是「路由模式」：LLM 做一次意图分类，之后的分发路径由代码写死。
`pa agent` 是真正的 **agent loop**：模型看到工具注册表（search_papers /
add_papers / ask_library / library_status / explore_directions / compare_papers /
read_repro_plan / write_code / run_code）后自主决定
调什么、调几轮——先检索预览、看结果再挑选入库、跨论文收集对比证据、按复现方案
写原型并沙箱执行，多步编排由模型完成，每步工具调用实时打印可观测。

```powershell
python -m uv run pa agent "看看库里有什么，再找 3 篇 2024 年后的 GNN 推荐综述入库"
python -m uv run pa agent "库里关于医学影像的论文都用了什么数据集？" --max-steps 12
```

工程护栏（全部代码强制，config.yaml `agent` 节可调）：

- **步数熔断**：`--max-steps`（默认 8）触顶后禁止工具、强制总结（软着陆）；
- **token 预算**：每轮调用前检查累计用量（默认 120k），超限软着陆；
- **循环检测**：同 (工具名, 参数) 签名第 2 次执行时回填警告、第 3 次直接拒绝执行；
- **入库护栏**：单次超 `add_cap`（20）直接拒绝要求分批；达 `confirm_threshold`（5）
  触发人工确认（y/N）——`run_code` 代码执行无条件复用同一确认钩子；
- **复现沙箱**：代码只落 `data/repro/<id>/workspace` 隔离工作区，文件名白名单
  （纯文件名 + 仅 .py，路径穿越直接拒绝），独立子进程执行、5~180s 超时、输出截断；
- **限流自动重试**：数据源 429 归类 `retryable_error`，框架按 `retry_after`
  自动重试一次；其余源故障（degraded）交模型换路；
- **轨迹存档**：`--trace` 把每步工具调用与结果写 JSONL（`data/traces/`），可复盘。

## MCP 接入（任意 Agent Host 可用）

`pa mcp` 以 stdio 传输启动 MCP server，把 9 个工具暴露给任何 MCP host。
工具 schema 直接消费 `TOOL_REGISTRY`（单一事实来源），执行复用 `ToolExecutor`
（四态结果语义与全部硬护栏原样生效）——协议层只做适配，不放宽护栏。

Claude Desktop（`claude_desktop_config.json`）或 Cursor（`mcp.json`）：

```json
{
  "mcpServers": {
    "paper-agent": {
      "command": "E:\\path\\to\\.venv\\Scripts\\pa.exe",
      "args": ["mcp"]
    }
  }
}
```

- **HITL 确认（M27）**：支持 input_required 的宿主（MCP 2026-07-28）里，
  `add_papers`（≥5 篇）与 `run_code` 触发确认门时宿主会弹**真实确认窗**——
  人的决定随重试请求回到服务端，拒绝则工具返回 declined 语义；`decline`/
  `cancel`/响应缺失一律失败关闭。`PAPER_AGENT_CONFIRM=approve|reject`
  保留为运维开关与老宿主逃生口（显式设置时优先于弹窗），每次触发写
  stderr 审计日志；`add_cap` 等硬护栏不受影响。接入后在 host 里即可
  「检索并入库 3 篇 GNN 综述」、「库里医学影像论文都用了什么数据集」——
  同一个文献库，CLI / Web / MCP 三入口共享。

## 评测体系

```powershell
python -m uv run pa eval --report     # 检索：hit@k 曲线 + MRR + nDCG@8（26 条问答对）
python -m uv run pa eval-agent        # Agent：12 任务 × 完成率/工具合规/越界操作/成本
```

检索排序指标实测（25 条带期望出处的 case）：hit@1 52% ｜ hit@3 76% ｜ hit@5 88% ｜
hit@8 100% ｜ MRR 0.665 ｜ nDCG@8 0.727——hit@8 满分掩盖了「相关块近半数排不到
第一」的排序劣化，这正是只看命中率看不见的问题。

Agent 任务评测（`eval/agent_tasks.json`，12 任务）覆盖四类能力面：单工具意图命中、
多步编排（检索→挑选入库 / 跨论文对比 / 复现执行）、边界行为（域外拒绝不调工具、
模糊指令澄清、空结果诚实汇报）；指标为任务完成率、工具合规率（越界操作必须为 0）、
答案关键词命中率、平均步数与 tokens。三轮实测（M15 两轮 + M19 扩容一轮）：

- M15 首轮（10 任务）：完成率 90%，平均 2.5 步 / 6161 tokens——唯一失败源于
  数据源集体限流时模型盲目换词重搜 8 次（评测轨迹复盘定位）；
- M15 修复后复测：**完成率 100%，平均 1.9 步 / 5240 tokens**——同样的源故障下
  模型 2 步即识别「0 结果是故障不是没论文」并诚实汇报；
- M19 扩容复测（12 任务，含 compare-01 / repro-01）：**完成率 / 合规率 /
  答案命中 100%，越界 0，平均 2.5 步 / 17538 tokens**（tokens 上升来自 9 工具
  注册表 + 复现任务本身的重活，如实记录）。repro-01 轨迹：read_repro_plan →
  write_code/run_code 交替 5 轮自纠错 → 最终 numpy 原型训练收敛（合成数据
  100%）并自主设计消融实验（去掉 GCN 模块后跌到随机水平）验证方法关键机制；
- 保留 1 处工具合规 ✗ 的历史记录为判定器口径问题（源全挂时不入库才是正确
  行为，无条件 must_call 判不了条件合规），如实记录而非掩饰。

这就是本项目的评测工作流：**跑评测 → 轨迹复盘定位失败模式 → 改护栏 → 复测验证**。

## Web 界面

`pa serve` 后浏览器打开 <http://127.0.0.1:8000>：

- **文献库**：检索入库（支持中文+年份，可勾选数据源；**高级筛选**：标题/摘要限定、作者过滤、
  相关度/引用数/最新排序、最低被引、仅可下载全文、年份区间；检索结果先预览、勾选、命名分组后再入库）、
  上传本地 PDF/DOCX、分组导航（**主动新建/重命名/删除分组**、列表多选批量归类、详情页下拉改组）、
  状态过滤、「加载更多」分页、
  论文详情（精读报告 / 实验结构含复现资产与置信度 / 复现方案 / 全文）、
  「一键处理」**五阶段实时时间线**（下载全文→解析→提取实验结构→生成精读报告→向量索引，
  每步状态/耗时/失败原因即时可见；失败自动保留已完成产物，重试跳过已完成阶段；
  完成后可在详情页**针对本文提问**——基于全文知识块的 RAG 问答，带页码出处）、单篇删除（连产物全链清理）；
- **助手**：自然语言对话（检索 / 问答 / 状态 / 方向引导），问答带可点击出处（含历史会话）；
  对话自动保存为会话（侧栏可新建 / 切换 / 删除），多轮追问带上下文；
  **Agent 任务模式**：描述目标（如「找 2 篇 2024 年的 GNN 推荐综述入库」），
  LLM 自主调用工具完成多步任务——每步工具调用实时显示（工具名/参数/四态状态徽章/耗时），
  批量入库在浏览器内弹确认框（同意/拒绝），结果以 Markdown 渲染并附步数与 token 用量；
- **设置**（顶栏 ⚙）：**外观主题**（深色/浅色，点选即时生效，保存后跨设备跟随）；
  配置模型平台——厂商预设（智谱/DeepSeek/Moonshot/通义/OpenAI）或自定义
  OpenAI 兼容端点，填 Base URL / API Key / 模型名，可一键「测试连接」；Key 只保存在本机
  `data/settings.json`（gitignore），界面回显仅尾 4 位脱敏；未配 Key 时齿轮亮黄点提醒；
- 支持深链：`?paper=<id>` 直开论文详情，`?tab=chat&s=<会话id>` 直达某次对话。

前端开发模式：`cd web && npm install && npm run dev`（Vite 端口 5173，/api 自动代理到 8000）；
改前端代码后 `npm run build` 重新生成 `web/dist`，`pa serve` 即托管最新产物。
API 文档（Swagger）在 <http://127.0.0.1:8000/docs>。

- 论文 id 可用片段（如 `W3217045679`、`PMC13451302`），多源结果自动去重；
- `pa chat` 支持带条件的自然语言检索（「找 2023 年以后的 XX 综述，前 20 篇」，自动抽取年份/数量并翻译成英文检索词）、方向推荐（「不知道看什么论文」）；与科研无关的请求会被礼貌拒绝；
- `pa run` 的无人值守选文带 LLM 相关性预筛与选文理由（显示在流水线 notes 中）；
- `pa run` 基于 library.db 状态断点续跑：中断后重跑同一主题自动跳过已完成阶段；
- `pa run` 无人值守参数在 `config.yaml` 的 `pipeline` 节（top_n / year_from / download_budget）；
- 无 OA 全文的论文标记「仅摘要」，其摘要也会以单块入知识库；
- `pa ask --paper <id片段>` 可限定单篇问答；范围外问题会明确回答「未提及」；
- `pa analyze`/`pa ask`/`pa run` 的知识提取需要 LLM Key；
- arXiv 在本机被网络屏蔽，检索自动降级；如需 arXiv，开启代理工具并在 `config.yaml` 的 `proxy` 填 `http://127.0.0.1:端口`。

## 配置说明

配置分三层（优先级从低到高）：`config.yaml` 代码默认 → `.env` 本地密钥 →
`data/settings.json` Web 设置页写入的运行时覆盖（只覆盖 llm 端点三项，保存即生效，无需重启）。

- `config.yaml`：平台 base_url / model、无人值守参数（top_n、年份过滤、下载预算）、
  agent 护栏（max_steps、token_budget、add_cap、confirm_threshold）。切换硅基流动 ↔ 智谱只改此文件（或在 Web 设置页覆盖）。
- `.env`：存放 API Key（变量名由各端点的 `api_key_env` 指定）。当前 `llm` 用智谱 `GLM-4.7`（`ZHIPU_API_KEY`），`embedding` 用硅基流动 BGE-M3（`LLM_API_KEY`）；免费档可换回 `GLM-4.7-Flash`。
