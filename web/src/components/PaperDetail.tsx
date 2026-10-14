import React, { useEffect, useState } from "react";
import { api, pollJob, type AskReply, type ExperimentSchema, type Job, type PaperDetail, type ReproView } from "../api";
import Markdown from "../md";
import Evidence from "./Evidence";
import { PaperFacts } from "./FieldEvidence";
import { statusLabel } from "../labels";

type Tab = "report" | "experiment" | "facts" | "plan" | "fulltext" | "abstract";

/** 一键处理的 5 个阶段（与后端 process.py 的 STAGE_LABELS 对应，顺序即执行序）。 */
const CANON_STAGES = [
  { k: "download", label: "下载全文" },
  { k: "parse", label: "解析文本" },
  { k: "experiment", label: "提取实验结构" },
  { k: "report", label: "生成精读报告" },
  { k: "index", label: "向量索引" },
];

type StageRec = { stage: string; label: string; status: "running" | "ok" | "skipped" | "failed"; detail: string; duration_ms: number };

export default function PaperDetail({
  id,
  onClose,
  onChanged,
  onStartAgent,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
  onStartAgent: (task: string, anchor: string) => void;
}) {
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [tab, setTab] = useState<Tab>("report");
  const [err, setErr] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [planning, setPlanning] = useState(false);
  const [groups, setGroups] = useState<{ name: string; count: number }[]>([]);
  const [askQ, setAskQ] = useState("");
  const [asking, setAsking] = useState(false);
  const [askOut, setAskOut] = useState<AskReply | null>(null);
  const [repro, setRepro] = useState<ReproView | null>(null);
  const [revision, setRevision] = useState(0);

  const load = () =>
    api.paper(id).then(value => { setPaper(value); setRevision(n => n + 1); }).catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
    api.tags().then((t) => setGroups(t.tags)).catch(() => {});
    // 复现产物（工作区文件/运行记录）与论文主体独立加载，缺省不阻塞详情
    api.paperRepro(id).then(setRepro).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const changeTag = async (tag: string) => {
    const target = tag === "__none__" ? null : tag;
    setErr("");
    try {
      if (target === "__new__") {
        const typed = window.prompt("新分组名称：");
        if (!typed || !typed.trim()) return;
        await api.createTag(typed.trim());
        await api.setPaperTag(id, typed.trim());
      } else {
        await api.setPaperTag(id, target);
      }
      await load();
      api.tags().then((t) => setGroups(t.tags)).catch(() => {});
      onChanged();
    } catch (e) {
      setErr(String(e));
    }
  };

  const makePlan = async () => {
    setErr("");
    setPlanning(true);
    try {
      await api.plan(id);
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setPlanning(false);
    }
  };

  const process = async () => {
    setErr("");
    setJob({ id: "", kind: "process_paper", status: "running", result: {}, error: "" });
    try {
      const j = await api.process(id);
      const done = await pollJob(j.id, setJob);
      await load();
      onChanged();
      // 处理成功直接展示核心产物：精读报告
      if (done.status === "done" && done.result?.final_status === "indexed") setTab("report");
    } catch (e) {
      setErr(String(e));
      setJob(null);
      load(); // 失败也刷新：paper.error 可能已被后端更新
    }
  };

  const doAsk = async () => {
    if (!askQ.trim()) return;
    setAsking(true);
    setErr("");
    try {
      const out = await api.ask(askQ.trim(), id);
      setAskOut(out);
    } catch (e) {
      setErr(String(e));
    } finally {
      setAsking(false);
    }
  };

  if (err && !paper) return <div className="detail-pane"><button onClick={onClose}>← 返回文献库</button><div className="local-error" role="alert">{err}</div><button onClick={() => { setErr(""); void load(); }}>重新加载</button></div>;
  if (!paper) return <div className="detail-pane"><div className="loading"><span className="spin" />加载中…</div></div>;

  const exp = paper.experiment;
  const hasReproArtifacts = !!paper.repro_plan?.trim() || (repro?.files.length ?? 0) > 0;
  const tabs = ([
    { k: "report", label: "精读报告", show: !!paper.report?.trim() },
    { k: "experiment", label: "实验结构", show: !!exp },
    { k: "facts", label: "字段依据", show: true },
    { k: "plan", label: "复现", show: hasReproArtifacts },
    { k: "fulltext", label: "全文", show: !!paper.full_text },
    { k: "abstract", label: "摘要", show: !!paper.abstract },
  ] as { k: Tab; label: string; show: boolean }[]).filter((t) => t.show);
  const active = tabs.find((t) => t.k === tab) ? tab : tabs[0]?.k;
  const processing = job?.status === "running";
  const jobDone = job != null && (job.status === "done" || job.status === "error");
  const stageRecs = ((job?.result?.stages as StageRec[] | undefined) ?? []);
  const stageMap = new Map(stageRecs.map((s) => [s.stage, s]));
  const failedStage = stageRecs.find((s) => s.status === "failed");
  const finalStatus = typeof job?.result?.final_status === "string" ? String(job.result.final_status) : null;
  const totalMs = stageRecs.reduce((acc, s) => acc + (s.duration_ms || 0), 0);
  // 针对本文提问的前提：全文已入知识库（处理链完成或至少已分析）
  const canAsk = paper.index_state?.can_ask === true;

  const doDelete = async () => {
    const shown = paper.title.length > 40 ? paper.title.slice(0, 40) + "…" : paper.title;
    if (!window.confirm(`确定删除《${shown}》？\n将同时删除其原文、解析文本、知识与索引产物，不可恢复。`)) return;
    setErr("");
    try {
      await api.deletePaper(id);
      onChanged();
      onClose();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div className="detail-pane">
      <button className="reader-back" onClick={onClose}>← 返回文献库</button>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <h2>{paper.title}</h2>
        <div style={{ display: "flex", gap: 8, whiteSpace: "nowrap", height: 36 }}>
          <button className="danger" onClick={doDelete}>删除</button>
          <button
            className="secondary"
            style={{ height: "100%" }}
            onClick={makePlan}
            disabled={planning || processing || !paper.experiment}
            title={paper.experiment ? "依据实验知识生成复现方案（repro_plan.md）" : "需要先完成知识提取（experiment.json）"}
          >
            {planning ? "规划中…" : paper.repro_plan ? "重新规划" : "生成复现方案"}
          </button>
          <button
            className="primary"
            style={{ height: "100%" }}
            onClick={process}
            disabled={processing || planning}
            title="下载全文 → 解析 → 提取实验结构与精读报告 → 向量索引。完成后可针对本文提问、生成复现方案"
          >
            {processing ? "处理中…" : paper.full_text ? "重新处理" : "一键处理"}
          </button>
        </div>
      </div>
      {err && <div className="local-error" role="alert">{err}</div>}
      <div className="readiness-strip"><span className={`readiness-badge ${canAsk ? "ready" : "pending"}`}>{paper.index_state?.status === "ready" ? "索引就绪" : paper.index_state?.status === "legacy" ? "历史索引，建议更新" : paper.index_state?.status === "stale" ? "索引已过期" : "尚未建立可用索引"}</span><span>{paper.index_state?.chunk_count ?? "—"} 个知识块 · 问答范围：仅本文</span>{paper.knowledge_current === false && paper.experiment && <span>知识产物需要重新核对或处理</span>}</div>
      <div className="meta-row">
        <span className={`tag status-${paper.status}`}>{statusLabel(paper.status)}</span>
        <span>{paper.source}</span>
        <select
          value={paper.tag || "__none__"}
          onChange={(e) => changeTag(e.target.value)}
          style={{ height: 26, fontSize: 12 }}
          title="移动到其它分组"
        >
          <option value="__none__">未分组</option>
          {groups.map((g) => (
            <option key={g.name} value={g.name}>分组：{g.name}</option>
          ))}
          <option value="__new__">＋ 新建分组…</option>
        </select>
        {paper.year && <span>{paper.year} 年</span>}
        {paper.citations != null && <span>被引 {paper.citations}</span>}
        {paper.doi && <span>DOI: {paper.doi}</span>}
        {paper.authors.length > 0 && <span>{paper.authors.slice(0, 4).join(", ")}{paper.authors.length > 4 ? " 等" : ""}</span>}
      </div>

      {job && (processing || (jobDone && stageRecs.length > 0)) && (
        <div className="section-block pprocess">
          <h3>处理进度</h3>
          <div className="pexplain">
            一键处理 = 下载全文 → 解析 → 提取实验结构与精读报告 → 向量索引入知识库。
            完成后可针对本文提问、生成复现方案。
          </div>
          <div className="ptimeline">
            {CANON_STAGES.map((cs) => {
              const s = stageMap.get(cs.k);
              const status = s?.status ?? "pending";
              return (
                <div key={cs.k} className={`pstage p-${status}`}>
                  <span className="p-ico">
                    {status === "ok" ? "✓" : status === "failed" ? "✗" : status === "running" ? <span className="spin" /> : status === "skipped" ? "–" : "○"}
                  </span>
                  <span className="p-label">{cs.label}</span>
                  <span className="p-detail">
                    {s?.detail || (status === "pending" ? (processing ? "等待中" : "未执行") : "")}
                  </span>
                  <span className="p-ms">{s && s.duration_ms > 0 ? fmtMs(s.duration_ms) : ""}</span>
                </div>
              );
            })}
          </div>
          {jobDone && !failedStage && finalStatus === "indexed" && (
            <div className="done-card ok">
              {stageRecs.length > 0 && stageRecs.every((s) => s.status === "skipped")
                ? "✓ 产物已就绪——这篇论文此前已处理完成，全文在知识库中，可直接在下方「针对本文提问」，或生成复现方案。"
                : `✓ 处理完成${totalMs > 0 ? `（共 ${fmtMs(totalMs)}）` : ""}——全文已入知识库，可在下方「针对本文提问」验证效果，或生成复现方案。`}
            </div>
          )}
          {jobDone && (failedStage || (finalStatus && finalStatus !== "indexed")) && (
            <div className="done-card bad">
              ✗ {failedStage ? `「${failedStage.label}」失败：${failedStage.detail}` : `最终状态：${statusLabel(finalStatus!)}`}
              <span className="hint">
                已完成的部分都保留了；问题排除后重新「一键处理」会跳过已完成的阶段继续推进。
                若该论文没有开放全文，可手动下载 PDF 后用列表上方「上传」导入。
              </span>
            </div>
          )}
          {job.status === "error" && <div className="err" style={{ marginTop: 8 }}>⚠ 任务失败：{job.error}</div>}
        </div>
      )}
      {paper.error && <div className="err" style={{ marginBottom: 10 }}>上次错误：{paper.error}</div>}

      {tabs.length === 0 ? (
        <div className="hint" style={{ marginTop: 30, lineHeight: 1.8 }}>
          还没有产物。点击右上角「一键处理」，这条链路会依次执行：
          <br />① <b>下载全文</b>（OA 链接）→ ② <b>解析</b>成 Markdown → ③ <b>提取实验结构</b>（研究目标/方法/数据集/结果 + 置信度）→
          <br />④ <b>生成精读报告</b> → ⑤ <b>向量索引</b>——完成后这里会出现报告与实验结构，并且可以<b>针对本文提问</b>。
        </div>
      ) : (
        <>
          <div className="chips" style={{ marginBottom: 10 }}>
            {tabs.map((t) => (
              <span key={t.k} className={`chip ${active === t.k ? "on" : ""}`} onClick={() => setTab(t.k)}>
                {t.label}
              </span>
            ))}
          </div>
          {active === "report" && <div className="section-block"><Markdown className="body-md tall" text={paper.report ?? ""} /></div>}
          {active === "plan" && (
            <div className="section-block">
              {paper.repro_plan?.trim() ? (
                <>
                  <div className="hint" style={{ marginBottom: 8 }}>复现方案（repro_plan.md）——Agent 按此方案写原型并执行：</div>
                  <Markdown className="body-md" text={paper.repro_plan} />
                </>
              ) : (
                <div className="hint" style={{ marginBottom: 8 }}>该论文尚无复现方案，但工作区已有产物（此前由 Agent 生成）：</div>
              )}
              <ReproWorkspace repro={repro} />
              {repro && (repro.files.length > 0 || repro.runs.length > 0) && (
                <div className="repro-handoff">
                  <button
                    className="ghost sm"
                    onClick={() =>
                      onStartAgent(
                        `继续完善这篇论文的复现：检查工作区现有代码（run_real.py 等）与运行记录，推进下一步——`
                        + `修复失败或超时的运行、补齐评估、或扩大训练规模。只针对本论文的工作区，不要检索或入库新论文。`,
                        id,
                      )
                    }
                    title="以本论文为锚定发起 Agent 任务：检索/入库类工具将被移除，防止任务漂移（M25 教训）"
                  >
                    🤖 交给 Agent 续做（锚定本论文）
                  </button>
                  <span className="hint">任务会预填到「助手 → Agent 任务」，确认后执行；执行器将移除检索/入库类工具</span>
                </div>
              )}
            </div>
          )}
          {active === "abstract" && <div className="section-block"><div className="body">{stripHtml(paper.abstract)}</div></div>}
          {active === "fulltext" && (
            <div className="section-block"><Markdown className="body-md tall" text={paper.full_text ?? ""} /></div>
          )}
          {active === "experiment" && exp && <ExperimentView exp={exp} />}
          {active === "facts" && <PaperFacts key={`${id}-${revision}`} id={id} title={paper.title} />}
        </>
      )}

      {!canAsk && <div className="surface-card"><h3>针对本文提问</h3><p className="hint">本文还没有当前可用的索引。点击上方处理按钮建立索引后即可提问；已有全文和报告仍可继续阅读。</p></div>}
      {canAsk && (
        <div className="section-block askbox">
          <h3>针对本文提问</h3>
          <div className="hint">只在本文的已索引片段中查找答案，提供章节、页码和原文。</div>
          <div className="ask-row">
            <input
              type="text"
              placeholder="例：这篇方法的核心创新点是什么？比基线提升了多少？"
              value={askQ}
              onChange={(e) => setAskQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doAsk()}
            />
            <button className="primary" disabled={asking || !askQ.trim()} onClick={doAsk}>
              {asking ? "思考中…" : "提问"}
            </button>
          </div>
          {askOut && (
            <div className="ask-out">
              <Markdown text={askOut.answer} />
              {askOut.sources.length > 0 && (
                <div className="ask-src">
                  {askOut.sources.map((s, i) => (
                    <div key={i}><span className="hint">出处 [{s.n.join(",")}] {s.page ? `第 ${s.page} 页 · ` : ""}{s.section}</span><Evidence source={s} /></div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 复现工作区（M24）：Agent 写下的代码文件与 run_code 运行记录，浏览器可见可点。 */
function ReproWorkspace({ repro }: { repro: ReproView | null }) {
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  const [loading, setLoading] = useState("");
  const [err, setErr] = useState("");
  if (!repro) return null;
  const { files, runs } = repro;
  if (files.length === 0 && runs.length === 0) return null;

  const open = async (name: string) => {
    setLoading(name);
    setErr("");
    try {
      setFile(await api.paperReproFile(repro.source_id, name));
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading("");
    }
  };

  const fmtTs = (ts: number) =>
    new Date(ts * 1000).toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });

  return (
    <>
      {files.length > 0 && (
        <div className="section-block">
          <h3>工作区文件（Agent 生成，点击查看）</h3>
          <div className="chips" style={{ marginBottom: 10 }}>
            {files.map((f) => (
              <span
                key={f.name}
                className={`chip ${file?.name === f.name ? "on" : ""}`}
                onClick={() => void open(f.name)}
                title={`${f.bytes} 字节`}
              >
                {loading === f.name ? "读取中…" : f.name}
              </span>
            ))}
          </div>
          {err && <div className="err">⚠ {err}</div>}
          {file && <pre className="code-view">{file.content}</pre>}
        </div>
      )}
      {runs.length > 0 && (
        <div className="section-block">
          <h3>运行记录（{runs.length} 次，点击展开输出）</h3>
          {runs.map((r, i) => (
            <details key={i} className="run-rec">
              <summary>
                <span className={`tag ${r.timed_out ? "conf-mid" : r.exit_code === 0 ? "conf-high" : "conf-low"}`}>
                  {r.timed_out ? "超时" : r.exit_code === 0 ? "exit 0" : `exit ${r.exit_code}`}
                </span>
                <code className="run-file">{r.filename}</code>
                <span className="hint">
                  {fmtTs(r.ts)}
                  {r.duration_ms ? ` · ${fmtMs(r.duration_ms)}` : ""}
                </span>
              </summary>
              <pre className="code-view">
                {[r.stdout, r.stderr].filter((x) => x && x.trim()).join("\n") || "（无输出）"}
              </pre>
            </details>
          ))}
        </div>
      )}
    </>
  );
}

/** 毫秒 → 人类可读时长（阶段耗时用）。 */
function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  return `${m}m${Math.round((ms - m * 60_000) / 1000)}s`;
}

/** EuropePMC 等源的摘要带 HTML 标记（<h4>、<p>…），纯文本展示前剥掉。 */
function stripHtml(s: string): string {
  return s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function ExperimentView({ exp }: { exp: ExperimentSchema }) {
  const conf = exp.confidence ?? {};
  const assets = isRecord(exp.repro_assets) ? exp.repro_assets : null;
  const notes = typeof exp.reproducibility_notes === "string" ? exp.reproducibility_notes : "";
  const unverified = Array.isArray(exp.unverified_claims) ? (exp.unverified_claims as unknown[]) : [];
  const assetGroups: [string, string][] = [
    ["code_url", "代码仓库"],
    ["dataset_urls", "数据集"],
    ["project_page", "项目主页"],
  ];
  return (
    <div className="section-block">
      <div className="kv" style={{ marginBottom: 14 }}>
        <div className="k">研究目标</div><div>{renderValue(exp.research_goal)}</div>
        <div className="k">方法</div><div>{renderValue(exp.method)}</div>
        <div className="k">数据集</div><div>{renderValue(exp.datasets)}</div>
        <div className="k">评价指标</div><div>{renderValue(exp.metrics)}</div>
        <div className="k">结论</div><div>{renderValue(exp.conclusions)}</div>
      </div>
      <div className="section-block">
        <h3>实验步骤</h3>
        <div className="body"><ol className="plain" style={{ margin: 0, paddingLeft: 20 }}>{toList(exp.experiment_steps).map((s, i) => <li key={i}>{renderValue(s)}</li>)}</ol></div>
      </div>
      {isRecord(exp.hyperparameters) && Object.keys(exp.hyperparameters).length > 0 && (
        <div className="section-block">
          <h3>超参数</h3>
          <div className="body"><div className="kv">{Object.entries(exp.hyperparameters).map(([k, v]) => (<><div className="k" key={k}>{k}</div><div key={k + "v"}>{renderValue(v)}</div></>))}</div></div>
        </div>
      )}
      <div className="section-block">
        <h3>主要结果</h3>
        <div className="body"><ul className="plain">{toList(exp.main_results).map((s, i) => <li key={i}>{renderValue(s)}</li>)}</ul></div>
      </div>
      {assets && assetGroups.some(([k]) => toList(assets[k]).length > 0 && toList(assets[k]).some((x) => x !== "—")) && (
        <div className="section-block">
          <h3>复现资产</h3>
          <div className="body">
            {assetGroups.map(([k, label]) => {
              const urls = toList(assets[k]).filter((u) => typeof u === "string" && u);
              if (urls.length === 0) return null;
              return (
                <div key={k} style={{ marginBottom: 6 }}>
                  <span className="muted">{label}：</span>
                  {urls.map((u, i) => (
                    <React.Fragment key={i}>
                      {i > 0 && "、"}
                      <a href={u as string} target="_blank" rel="noreferrer">{u as string}</a>
                    </React.Fragment>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {notes.trim() && (
        <div className="section-block">
          <h3>可复现性备注</h3>
          <div className="body">{notes}</div>
        </div>
      )}
      {unverified.length > 0 && (
        <div className="section-block">
          <h3>未验证声明（LLM 推断，文中未明确）</h3>
          <div className="body"><ul className="plain">{unverified.map((c, i) => <li key={i}>{renderValue(c)}</li>)}</ul></div>
        </div>
      )}
      <div className="conf-row">
        <span className="hint">字段置信度：</span>
        {Object.keys(conf).length > 0
          ? Object.entries(conf).map(([k, v]) => (
              <span key={k} className={`tag conf-${v === "high" ? "high" : v === "low" ? "low" : "mid"}`} title={FIELD_LABELS[k] ?? k}>
                {(FIELD_LABELS[k] ?? k)} {String(v)}
              </span>
            ))
          : <span className="hint">未标注</span>}
      </div>
    </div>
  );
}

/** experiment.json 常见键的中文名（递归渲染与置信度徽章共用）。 */
const FIELD_LABELS: Record<string, string> = {
  name: "名称", architecture: "架构", key_components: "关键组件",
  source: "来源", split: "划分",
  step: "步骤", description: "说明", details: "细节",
  value: "结果值", baseline: "基线",
  code_url: "代码", dataset_urls: "数据集", project_page: "主页",
  research_goal: "研究目标", method: "方法", datasets: "数据集",
  hyperparameters: "超参数", experiment_steps: "实验步骤", metrics: "评价指标",
  main_results: "主要结果", conclusions: "结论", repro_assets: "复现资产",
  reproducibility_notes: "可复现性备注",
};

/** URL 列表在正文里渲染为可点击链接。 */

/** 递归渲染 schema 值：对象 → 有序键值；数组 → 列表；字符串原样。 */
/** 递归渲染 schema 值：对象 → 有序键值；数组 → 列表；URL → 链接；字符串原样。 */
function renderValue(v: unknown): React.ReactNode {
  if (v == null || v === "") return "—";
  if (typeof v === "string") {
    if (/^https?:\/\/\S+$/.test(v)) {
      return <a href={v} target="_blank" rel="noreferrer">{v}</a>;
    }
    return v;
  }
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return "—";
    const items = v.map((x, i) => <li key={i}>{renderValue(x)}</li>);
    return <ul className="plain">{items}</ul>;
  }
  if (isRecord(v)) {
    const parts = Object.entries(v)
      .filter(([, x]) => x != null && x !== "" && !(Array.isArray(x) && x.length === 0))
      .map(([k, x]) => (
        <span key={k}>
          <span className="muted">{FIELD_LABELS[k] ?? k}: </span>
          {renderValue(x)}
        </span>
      ));
    return parts.length ? <>{parts.map((p, i) => <React.Fragment key={i}>{p}{i < parts.length - 1 ? "；" : ""}</React.Fragment>)}</> : "—";
  }
  return String(v);
}

function toList(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [v];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
