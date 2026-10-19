import type { AgentTaskState } from "../api";
import { OUTCOME_LABEL } from "../runReview";
import Evidence from "./Evidence";
import FieldEvidence from "./FieldEvidence";
import CallLedgerView from "./CallLedgerView";

const TOOLS: Record<string, string> = {
  search_papers: "查找论文", add_papers: "保存题录", library_status: "查看资料范围",
  ask_library: "检索证据并回答", compare_papers: "比较论文证据", explore_directions: "推荐研究方向",
  read_repro_plan: "读取复现方案", write_code: "编写原型", run_code: "运行原型",
};
const CHECKS: Record<string, string> = {
  library_add: "题录已保存", no_library_mutation: "文献记录未变更", tool_success: "指定操作已执行",
  evidence_answer: "来源与引用结构可追溯",
};

export default function AgentRunReview({ task }: { task: AgentTaskState }) {
  const finished = ["done", "error"].includes(task.status);
  const verification = task.verification;
  const telemetry = task.telemetry?.call_count !== undefined ? task.telemetry : undefined;
  const tokens = (task.usage?.prompt || 0) + (task.usage?.completion || 0);
  return <div className="run-review">
    {task.provenance && <p className="demo-provenance">演示回放 · {task.provenance}</p>}
    <div className="run-metrics">
      <div><span>结果验收</span><strong className={`outcome-${verification?.status ?? "unverified"}`}>
        {finished ? OUTCOME_LABEL[verification?.status ?? "unverified"] : "执行中"}</strong></div>
      <div><span>工具步骤</span><strong>{task.steps?.length ?? task.step_count} 步</strong></div>
      <div><span>模型调用</span><strong>{telemetry ? `${telemetry.call_count} 次` : "未记录"}</strong></div>
      <div><span>已报告对话用量</span><strong>{telemetry ? tokens.toLocaleString() : "未记录"}<small>{telemetry && " tokens"}</small></strong></div>
      <div><span>总耗时</span><strong>{task.duration_ms ? `${(task.duration_ms / 1000).toFixed(1)} 秒` : "—"}</strong></div>
    </div>
    {finished && <div className={`outcome-panel outcome-${verification?.status ?? "unverified"}`}>
      <p>{verification?.note || "此记录没有目标验收信息，不能仅凭模型回复判断任务成功。"}</p>
      {!!verification?.checks?.length && <ul className="acceptance-checks">
        {verification.checks.map((c, i) => <li key={i}><span className={`check-mark ${c.passed === false ? "outcome-failed" : ""}`} aria-label={c.passed === true ? "通过" : c.passed === false ? "未通过" : "未知"}>{c.passed === true ? "✓" : c.passed === false ? "×" : "?"}</span>
          <div><strong>{CHECKS[c.kind] || c.kind}</strong><p>{c.detail}</p></div></li>)}
      </ul>}
    </div>}
    {!!task.steps?.length && <ol className="run-timeline" aria-label="任务执行过程">
      {task.steps.map((step, i) => <li key={i} className={step.status === "ok" ? "step-ok" : "step-failed"}>
        <div className="timeline-top"><strong>{TOOLS[step.tool] || step.tool}</strong>
          <span>{step.observation?.declined ? "已拒绝" : step.status === "ok" ? "已执行" : step.status === "degraded" ? "降级执行" : "未成功"} · {(step.duration_ms / 1000).toFixed(1)} 秒</span></div>
        <p>{step.observation?.declined ? "用户已拒绝本次操作，未执行。" : step.detail}</p>
        {step.observation?.sources?.map(source => <div key={`${source.paper_id}-${source.section}`} className="run-source"><strong>{source.title} · {source.section}</strong><Evidence source={source} /></div>)}
        {step.comparison?.papers?.length ? <FieldEvidence papers={step.comparison.papers} /> : null}
        <details className="technical-detail"><summary>调用参数</summary><code>{step.tool}</code><pre>{JSON.stringify(step.arguments, null, 2)}</pre></details>
      </li>)}
    </ol>}
    {telemetry && <CallLedgerView telemetry={telemetry} />}
  </div>;
}
