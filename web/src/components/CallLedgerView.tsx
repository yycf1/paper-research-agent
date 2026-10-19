import type { CallTelemetry } from "../api";

const PHASES: Record<string, string> = {
  "agent.decision": "任务决策", "agent.summary": "最终总结", "qa.rewrite": "改写检索问题",
  "qa.answer": "证据问答", "qa.empty_retry": "重试空回复", "qa.evidence_review": "复核证据",
  "qa.citation_repair": "修复引用", "retrieval.embedding": "检索向量化", "retrieval.rerank": "证据重排序",
  "intent.classify": "意图分析", "catalogue.topics": "题录方向归纳",
};

function reportedUsage(telemetry: CallTelemetry, kind: string, tokens: number) {
  const calls = telemetry.calls.filter(c => c.kind === kind);
  const unknown = calls.some(c => !c.usage_known);
  if (unknown && !calls.some(c => c.usage_known)) return "用量未知";
  return `已报告 ${tokens.toLocaleString()} tokens${unknown ? "，另有未知用量" : ""}`;
}

export default function CallLedgerView({ telemetry }: { telemetry: CallTelemetry }) {
  return <details className="call-ledger"><summary>调用明细与统计范围 · {telemetry.call_count} 次</summary>
    <p className="hint">{telemetry.known_usage_calls} 次返回完整用量，{telemetry.unknown_usage_calls} 次用量未知。
      失败或未返回用量的调用不计入已报告总量。{telemetry.cost_note}。</p>
    <p className="hint">对话{reportedUsage(telemetry, "chat", telemetry.prompt + telemetry.completion)} · 向量化{reportedUsage(telemetry, "embedding", telemetry.embedding_tokens)} · 重排序{reportedUsage(telemetry, "rerank", telemetry.rerank_tokens)}</p>
    <div className="call-table-wrap"><table><thead><tr><th>环节</th><th>模型</th><th>结果</th><th>用量</th><th>耗时</th></tr></thead><tbody>
      {telemetry.calls.map((c, i) => <tr key={i}><td>{PHASES[c.stage] || (c.kind === "embedding" ? "向量化" : c.kind === "rerank" ? "重排序" : "工具内部调用")}</td>
        <td>{c.model || "未提供模型名"}</td><td>{c.status === "ok" ? "成功" : "失败"}{c.error_type && <small>{c.error_type}</small>}</td>
        <td>{c.usage_known ? `${c.usage.total ?? (c.usage.prompt || 0) + (c.usage.completion || 0)} tokens` : "未知"}</td><td>{(c.elapsed_ms / 1000).toFixed(2)} 秒</td></tr>)}
    </tbody></table></div>
  </details>;
}
