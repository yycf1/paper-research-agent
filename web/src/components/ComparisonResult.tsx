import { useEffect, useState } from "react";
import { api, type ComparisonReply } from "../api";
import FieldEvidence from "./FieldEvidence";

export default function ComparisonResult({ result }: { result: ComparisonReply }) {
  const [fileTypes, setFileTypes] = useState<Record<string, string | null | undefined>>({});
  useEffect(() => {
    let current = true;
    void Promise.allSettled(result.papers.map(p => api.paper(p.paper_id))).then(results => {
      if (!current) return;
      const papers = results.flatMap(r => r.status === "fulfilled" && r.value.has_local_file ? [r.value] : []);
      setFileTypes(Object.fromEntries(papers.map(p => [p.source_id, p.local_file_type])));
    });
    return () => { current = false; };
  }, [result]);
  return <section className="conversation-comparison">
    <h2>论文比较与原文依据</h2>
    <p className="hint">研究目标、数据集、评价指标均为待核对字段；可展开原文依据，继续在下方追问。</p>
    <FieldEvidence papers={result.papers} fileTypes={fileTypes} />
    {!result.papers.length && <p>{result.note ?? "暂无可用比较证据，请先处理所选论文。"}</p>}
    {result.comparability?.pairs.map((pair, i) => <div className="condition-check" key={i}>
      <h3>比较条件核对 · {i + 1}</h3>
      {!pair.metrics.length && <p className="hint">尚未提取指标，无法核对条件。</p>}
      {pair.metrics.map((metric, n) => <p key={n}><strong>{metric.left_metric ?? "未知指标"} / {metric.right_metric ?? "未知指标"}</strong><br />
        {metric.different_conditions.length ? `条件不同：${metric.different_conditions.join("、")}` :
          metric.missing_conditions.length ? `条件缺失：${metric.missing_conditions.join("、")}` : "已记录条件相同，仍需人工核对可比性"}</p>)}
    </div>)}
    <p className="hint">未按指标裸值对论文排名。</p>
  </section>;
}
