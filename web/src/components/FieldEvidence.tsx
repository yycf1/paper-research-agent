import { useEffect, useState } from "react";
import { api, type BoundFields, type FieldFact } from "../api";

const LABELS = { research_goal: "研究目标", datasets: "数据集", metrics: "评价指标" };
const STATES = { unknown: "未知", unverified: "待核对", supported: "人工复核支持", unsupported: "依据不支持", conflict: "存在冲突", not_applicable: "不适用" };

function valueText(fact: FieldFact) {
  const value = fact.normalized_value;
  if (value === null) return fact.issues.includes("invalid_field_type") ? "抽取格式不符合要求" : "暂无已提取内容";
  if (typeof value === "string") return value;
  const parts = [String(value.name ?? "")];
  if (value.value !== null && value.value !== undefined && value.value !== "") parts.push(String(value.value));
  for (const [key, label] of [["split", "划分"], ["dataset", "数据集"], ["task", "任务"], ["unit", "单位"], ["baseline", "基线"]]) {
    if (value[key] !== null && value[key] !== undefined && value[key] !== "") parts.push(`${label}：${value[key]}`);
  }
  return parts.join("；");
}

function FactCell({ fact }: { fact: FieldFact }) {
  const state = fact.citation_status === "invalid" ? "引用失效" :
    fact.support_status === "unverified" && fact.citation_status === "missing" ? "缺少证据" : STATES[fact.support_status];
  return <div className="field-fact">
    <div className="field-value">{valueText(fact)}</div>
    <span className={`field-state state-${fact.support_status}`}>{state}</span>
    {fact.evidence.length > 0 && <details className="field-quotes">
      <summary>查看依据（{fact.evidence.length} 段）</summary>
      {fact.evidence.map((ref, i) => <blockquote key={`${ref.chunk_id}-${i}`}>
        <div className="hint">{ref.section || "原文"}{ref.page ? ` · 第 ${ref.page} 页` : " · 无可用页码"}</div>
        <p>{ref.quote}</p>
      </blockquote>)}
    </details>}
    {fact.review && <p className="hint">复核说明：{fact.review.note}</p>}
    {fact.issues.some(issue => issue.includes("budget")) && <p className="hint">本次展示达到篇幅上限，部分内容未展示。</p>}
  </div>;
}

export default function FieldEvidence({ papers }: { papers: { title: string; field_evidence: BoundFields }[] }) {
  if (!papers.length) return null;
  return <section className="field-panel" aria-label="逐字段原文依据">
    <p className="hint">自动找到的片段仅是候选依据；“待核对”不代表原文已支持结论，未知字段也不代表论文没有报告。</p>
    {papers.length > 1 && <p className="hint">表格较宽时可横向滚动查看其他论文。</p>}
    <div className="field-table-scroll" role="region" aria-label="论文字段比较表" tabIndex={0}>
      <table className="field-table" style={{ minWidth: papers.length > 1 ? 88 + papers.length * 240 : undefined }}>
        <thead><tr><th>对比字段</th>{papers.map(p => <th key={p.field_evidence.paper_id}>{p.title}</th>)}</tr></thead>
        <tbody>{(Object.keys(LABELS) as FieldFact["field"][]).map(field => <tr key={field}>
          <th scope="row">{LABELS[field]}</th>
          {papers.map(p => <td key={p.field_evidence.paper_id}>
            {p.field_evidence.fields[field].map((fact, i) => <FactCell key={`${fact.claim_sha256}-${i}`} fact={fact} />)}
            {!!p.field_evidence.omitted[field] && <p className="hint">另有 {p.field_evidence.omitted[field]} 项因篇幅限制未展示。</p>}
          </td>)}
        </tr>)}</tbody>
      </table>
    </div>
    <p className="field-note">任务、数据划分、指标定义或单位尚未核对，不据这里的裸值排序论文优劣。</p>
    {papers.some(p => p.field_evidence.unmatched_reviews > 0) && <p className="hint">部分历史复核未匹配当前事实或证据版本，未沿用其结论。</p>}
    {papers.some(p => p.field_evidence.load_issues?.length) && <p className="hint">部分抽取或复核记录不可用，请以当前原文为准。</p>}
  </section>;
}

export function PaperFacts({ id, title }: { id: string; title: string }) {
  const [data, setData] = useState<BoundFields | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api.paperFacts(id).then(value => { if (active) setData(value); })
      .catch(reason => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [id]);
  if (error) return <div className="err">字段依据暂时不可用：{error}</div>;
  if (!data || data.paper_id !== id) return <div className="loading">正在核对本地原文片段…</div>;
  return <FieldEvidence papers={[{ title, field_evidence: data }]} />;
}
