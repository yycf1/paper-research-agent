import type { AgentTaskState } from "../api";
import Markdown from "../md";
import AgentRunReview from "./AgentRunReview";

export default function ConversationTask({ task, onConfirm, confirming }: {
  task: AgentTaskState; onConfirm: (id: string, approve: boolean) => void; confirming: boolean;
}) {
  return <section className="conversation-task" aria-label="执行操作记录">
    <h2>执行操作</h2>
    <AgentRunReview task={task} />
    {task.status === "running" && <p className="hint"><span className="spin" /> 正在执行，步骤会显示在这里。</p>}
    {task.status === "waiting_confirm" && task.pending_confirm && <div className="confirm-box">
      <strong>需要确认 · {task.pending_confirm.count} 项操作</strong>
      {task.pending_confirm.titles.slice(0, 5).map((t, i) => <p key={i}>{t}</p>)}
      {task.pending_confirm.titles.length > 5 && <p>共 {task.pending_confirm.count} 项</p>}
      <div className="c-actions"><button className="primary" disabled={confirming} onClick={() => onConfirm(task.id, true)}>同意执行</button>
        <button disabled={confirming} onClick={() => onConfirm(task.id, false)}>拒绝</button></div>
    </div>}
    {task.status === "error" && <p className="err">任务失败：{task.error}</p>}
    {task.status === "done" && <div className="run-answer">
      {task.stopped_reason === "max_steps" && <p className="hint">已达步数上限，以下总结基于已有结果。</p>}
      {task.stopped_reason === "token_budget" && <p className="hint">已达用量预算，以下总结基于已有结果。</p>}
      <Markdown text={task.answer || "模型未返回总结，请查看执行步骤与验收结果。"} />
    </div>}
  </section>;
}
