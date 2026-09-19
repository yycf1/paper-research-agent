import React, { useEffect, useState } from "react";
import { api, pollJob, type ExperimentSchema, type Job, type PaperDetail } from "../api";
import Markdown from "../md";
import { statusLabel } from "../labels";

type Tab = "report" | "experiment" | "plan" | "fulltext" | "abstract";

export default function PaperDetail({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [tab, setTab] = useState<Tab>("report");
  const [err, setErr] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [planning, setPlanning] = useState(false);

  const load = () =>
    api.paper(id).then(setPaper).catch((e) => setErr(String(e)));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

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
      await pollJob(j.id, setJob);
      await load();
      onChanged();
    } catch (e) {
      setErr(String(e));
      setJob(null);
      load(); // 失败也刷新：paper.error 可能已被后端更新
    }
  };

  if (err) return <div className="detail-pane"><div className="err">⚠ {err}</div></div>;
  if (!paper) return <div className="detail-pane"><div className="loading"><span className="spin" />加载中…</div></div>;

  const exp = paper.experiment;
  const tabs = ([
    { k: "report", label: "精读报告", show: !!paper.report?.trim() },
    { k: "experiment", label: "实验结构", show: !!exp },
    { k: "plan", label: "复现方案", show: !!paper.repro_plan?.trim() },
    { k: "fulltext", label: "全文", show: !!paper.full_text },
    { k: "abstract", label: "摘要", show: !!paper.abstract },
  ] as { k: Tab; label: string; show: boolean }[]).filter((t) => t.show);
  const active = tabs.find((t) => t.k === tab) ? tab : tabs[0]?.k;
  const processing = job?.status === "running";
  const steps = ((job?.result?.steps as { stage: string; ok: boolean; detail: string }[]) ?? []);

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
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <h2>{paper.title}</h2>
        <div style={{ display: "flex", gap: 8, whiteSpace: "nowrap", height: 36 }}>
          <button className="danger" onClick={doDelete}>删除</button>
          <button
            className="primary"
            style={{ height: "100%" }}
            onClick={makePlan}
            disabled={planning || processing || !paper.experiment}
            title={paper.experiment ? "依据实验知识生成复现方案（repro_plan.md）" : "需要先完成知识提取（experiment.json）"}
          >
            {planning ? "规划中…" : paper.repro_plan ? "重新规划" : "生成复现方案"}
          </button>
          <button className="primary" style={{ height: "100%" }} onClick={process} disabled={processing || planning}>
            {processing ? "处理中…" : "一键处理"}
          </button>
        </div>
      </div>
      <div className="meta-row">
        <span className={`tag status-${paper.status}`}>{statusLabel(paper.status)}</span>
        <span>{paper.source}</span>
        {paper.tag && <span className="tag">分组：{paper.tag}</span>}
        {paper.year && <span>{paper.year} 年</span>}
        {paper.citations != null && <span>被引 {paper.citations}</span>}
        {paper.doi && <span>DOI: {paper.doi}</span>}
        {paper.authors.length > 0 && <span>{paper.authors.slice(0, 4).join(", ")}{paper.authors.length > 4 ? " 等" : ""}</span>}
        <span style={{ cursor: "pointer", color: "var(--accent-2)" }} onClick={onClose}>
          关闭 ✕
        </span>
      </div>

      {job && (processing || steps.length > 0) && (
        <div className="section-block">
          <h3>处理进度{!processing && job.status === "done" ? "（已结束）" : ""}</h3>
          <div className="steps">
            {steps.length === 0 && processing && <div className="hint"><span className="spin" />排队启动中…</div>}
            {steps.map((s, i) => (
              <div key={i} className="step">
                <span className={s.ok ? "ok" : "no"}>{s.ok ? "✓" : "✗"}</span>
                <span>{stageLabel(s.stage)} — {s.detail}</span>
              </div>
            ))}
          </div>
          {!processing && job.status === "done" && typeof job.result?.final_status === "string" && (
            <div className="hint" style={{ marginTop: 8 }}>
              最终状态：{statusLabel(String(job.result.final_status))}（失败步骤修正后可重新「一键处理」）
            </div>
          )}
        </div>
      )}
      {job?.status === "error" && <div className="err">⚠ 任务失败：{job.error}</div>}
      {paper.error && <div className="err" style={{ marginBottom: 10 }}>上次错误：{paper.error}</div>}

      {tabs.length === 0 ? (
        <div className="hint" style={{ marginTop: 30 }}>
          还没有产物。点击右上角「一键处理」：下载 → 解析 → 知识提取 → 索引，完成后这里会出现精读报告与实验结构。
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
          {active === "report" && <div className="section-block"><Markdown className="body-md" text={paper.report ?? ""} /></div>}
          {active === "plan" && <div className="section-block"><Markdown className="body-md" text={paper.repro_plan ?? ""} /></div>}
          {active === "abstract" && <div className="section-block"><div className="body">{paper.abstract}</div></div>}
          {active === "fulltext" && (
            <div className="section-block"><Markdown className="body-md tall" text={paper.full_text ?? ""} /></div>
          )}
          {active === "experiment" && exp && <ExperimentView exp={exp} />}
        </>
      )}
    </div>
  );
}

function stageLabel(s: string): string {
  const m: Record<string, string> = { download: "下载", parse: "解析", analyze: "知识提取", index: "向量索引" };
  return m[s] ?? s;
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
