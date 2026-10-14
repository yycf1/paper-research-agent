import { useEffect, useState } from "react";
import { api, type AskReply, type ComparisonReply, type Paper } from "../api";
import Markdown from "../md";
import Evidence from "./Evidence";
import FieldEvidence from "./FieldEvidence";

export default function ResearchBoard({
  scope,
  onOpenDetail,
}: {
  scope: { ids: string[]; compare: boolean; key: number } | null;
  onOpenDetail: (id: string) => void;
}) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [mode, setMode] = useState<"ask" | "compare">(
    scope?.compare ? "compare" : "ask",
  );
  const [range, setRange] = useState<"all" | "selected">(
    scope ? "selected" : "all",
  );
  const [ids, setIds] = useState<string[]>(scope?.ids ?? []);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [requestScope, setRequestScope] = useState("");
  const [answer, setAnswer] = useState<AskReply | null>(null);
  const [comparison, setComparison] = useState<ComparisonReply | null>(null);
  useEffect(() => {
    api
      .papers({ limit: 200 })
      .then((r) => setPapers(r.papers))
      .catch((e) => setError(String(e)));
  }, []);
  const toggle = (id: string) => {
    setError("");
    setIds((current) =>
      current.includes(id)
        ? current.filter((x) => x !== id)
        : current.length < 6
          ? [...current, id]
          : current,
    );
  };
  const run = async () => {
    if (!query.trim() || busy) return;
    setBusy(true);
    setError("");
    setAnswer(null);
    setComparison(null);
    setNotice("");
    setRequestScope(
      mode === "compare" || range === "selected"
        ? ids
            .map((id) => papers.find((p) => p.source_id === id)?.title ?? id)
            .join("；")
        : "整个文献库",
    );
    try {
      if (mode === "compare")
        setComparison(await api.compare(query.trim(), ids));
      else
        setAnswer(
          await api.ask(
            query.trim(),
            undefined,
            range === "selected" ? ids : undefined,
          ),
        );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const activeIds = mode === "compare" || range === "selected" ? ids : [];
  return (
    <div className="research-board">
      <div className="research-intro">
        <span className="eyebrow">EVIDENCE FIRST</span>
        <h1>从论文里寻找答案</h1>
        <p>先选择资料范围，再提问或比较；每个结论都回到原文核对。</p>
      </div>
      <div className="research-controls">
        <div className="seg" aria-label="研究方式">
          <button
            className={`seg-item ${mode === "ask" ? "on" : ""}`}
            disabled={busy}
            onClick={() => setMode("ask")}
          >
            文献问答
          </button>
          <button
            className={`seg-item ${mode === "compare" ? "on" : ""}`}
            disabled={busy}
            onClick={() => {
              setMode("compare");
              setRange("selected");
            }}
          >
            论文比较
          </button>
        </div>
        {mode === "ask" && (
          <label className="range-select">
            资料范围{" "}
            <select
              aria-label="资料范围"
              value={range}
              disabled={busy}
              onChange={(e) => setRange(e.target.value as "all" | "selected")}
            >
              <option value="all">整个文献库</option>
              <option value="selected">选择论文（最多 6 篇）</option>
            </select>
          </label>
        )}
        {(mode === "compare" || range === "selected") && (
          <details className="scope-picker" open={!scope?.ids.length}>
            <summary>已选 {ids.length} 篇 · 调整论文范围</summary>
            <div className="scope-options">
              {papers.map((p) => (
                <label key={p.source_id}>
                  <input
                    type="checkbox"
                    checked={ids.includes(p.source_id)}
                    disabled={
                      busy || (!ids.includes(p.source_id) && ids.length >= 6)
                    }
                    onChange={() => toggle(p.source_id)}
                  />
                  <span>
                    {p.title}
                    <small>{p.source_id}</small>
                  </span>
                </label>
              ))}
              {!papers.length && (
                <p className="hint">当前列表为空，可先到文献库导入资料。</p>
              )}
            </div>
            <p className="hint">
              显示最近 200 篇；也可从文献库勾选论文后进入这里。
            </p>
            <button
              disabled={busy || ids.length === 0}
              onClick={() => setIds([])}
            >
              清空选择
            </button>
          </details>
        )}
        <div className="scope-caption">
          本次范围：
          {activeIds.length
            ? activeIds.map((id) => (
                <button key={id} onClick={() => onOpenDetail(id)}>
                  {papers.find((p) => p.source_id === id)?.title ?? id}
                </button>
              ))
            : mode === "ask" && range === "all"
              ? "整个文献库"
              : "尚未选择论文"}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
          className="research-query"
        >
          <textarea
            aria-label="研究问题"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              mode === "ask"
                ? "例如：这些论文采用了什么数据集和评估设置？"
                : "例如：比较数据划分、Recall@K 与评估协议"
            }
            rows={3}
            maxLength={mode === "compare" ? 500 : 2000}
          />
          <button
            className="primary"
            disabled={
              busy ||
              !query.trim() ||
              ((range === "selected" || mode === "compare") &&
                ids.length < (mode === "compare" ? 2 : 1))
            }
          >
            {busy
              ? "正在查找证据…"
              : mode === "ask"
                ? "查找答案"
                : "比较所选论文"}
          </button>
        </form>
        <p className="hint">
          问答需要已建立索引。比较展示原文候选与条件差异，待核对字段不能作为确定结论。
        </p>
      </div>
      {error && (
        <div className="local-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <p className="hint" role="status">
          {notice}
        </p>
      )}
      {answer && (
        <section className="surface-card research-result">
          <div className="card-heading">
            <h2>问答结果</h2>
            <button
              onClick={() =>
                navigator.clipboard
                  .writeText(
                    answer.answer +
                      "\n\n来源：\n" +
                      answer.sources
                        .map(
                          (s) =>
                            `${s.n.map((n) => `[${n}]`).join("")} ${s.title} · ${s.section} · ${s.paper_id}${s.page ? ` · 第${s.page}页` : ""}`,
                        )
                        .join("\n"),
                  )
                  .then(() => setNotice("已复制答案与来源。"))
                  .catch(() => setError("复制失败，请手动选择文本。"))
              }
            >
              复制答案与来源
            </button>
          </div>
          <p className="hint">此结果使用的范围：{requestScope}</p>
          {answer.answer_status && (
            <p className="hint">
              {(
                {
                  generated: "已生成，引用结构通过；语义支持仍需核对",
                  refused: "现有证据不足",
                  no_hits: "本次没有召回",
                  scope_unindexed: "所选论文未索引",
                  citation_invalid: "引用校验未通过",
                  low_evidence: "相关证据不足",
                } as Record<string, string>
              )[answer.answer_status] ?? "本次回答状态待核对"}
            </p>
          )}
          <Markdown text={answer.answer} />
          {answer.sources.map((source) => (
            <div className="source-card" key={source.paper_id + source.section}>
              <button onClick={() => onOpenDetail(source.paper_id)}>
                {source.n.map((n) => `[${n}]`).join("")} {source.title} ·{" "}
                {source.section}
              </button>
              <Evidence source={source} />
            </div>
          ))}
          {answer.retrieval_notes?.map((note) => (
            <p className="hint" key={note}>
              {note}
            </p>
          ))}
        </section>
      )}
      {comparison && (
        <section className="surface-card research-result">
          <h2>论文比较与原文依据</h2>
          <p className="hint">此结果使用的范围：{requestScope}</p>
          <FieldEvidence papers={comparison.papers} />
          {!comparison.papers.length && (
            <p className="empty-copy">
              {comparison.note ?? "暂无可用比较证据，请先处理所选论文。"}
            </p>
          )}
          {comparison.comparability?.pairs.map((pair, i) => (
            <div className="condition-check" key={i}>
              <h3>{pair.paper_ids.join(" / ")}</h3>
              {!pair.metrics.length && (
                <p className="hint">指标未提取，暂无法核对条件。</p>
              )}
              {pair.metrics.map((metric, n) => (
                <p key={n}>
                  <strong>
                    {metric.left_metric ?? "未知指标"} /{" "}
                    {metric.right_metric ?? "未知指标"}
                  </strong>
                  <br />
                  {metric.different_conditions.length
                    ? `条件不同：${metric.different_conditions.join("、")}`
                    : metric.missing_conditions.length
                      ? `条件缺失：${metric.missing_conditions.join("、")}`
                      : "已记录条件相同，仍需人工核对可比性"}
                </p>
              ))}
            </div>
          ))}
          <p className="hint">未按指标裸值对论文排名。</p>
        </section>
      )}
    </div>
  );
}
