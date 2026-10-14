import { useCallback, useEffect, useState } from "react";
import {
  api,
  type AgentTaskBrief,
  type AgentTaskState,
  type Job,
} from "../api";
import Markdown from "../md";
import FieldEvidence from "../components/FieldEvidence";

const STATUS: Record<string, string> = {
  running: "进行中",
  waiting_confirm: "等待确认",
  done: "已完成",
  error: "未完成",
};
export default function Tasks({
  active,
  onOpenDetail,
}: {
  active: boolean;
  onOpenDetail: (id: string) => void;
}) {
  const [agents, setAgents] = useState<AgentTaskBrief[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<AgentTaskState | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const results = await Promise.allSettled([api.agentTasks(), api.jobs()]);
    if (results[0].status === "fulfilled") setAgents(results[0].value.tasks);
    if (results[1].status === "fulfilled")
      setJobs(results[1].value.jobs.slice().reverse());
    setError(
      results.some((r) => r.status === "rejected")
        ? "部分任务记录暂时不可用，请重试刷新。"
        : "",
    );
  }, []);
  // Fetching on workspace activation synchronizes with the server; updates happen after await.
  useEffect(() => {
    if (!active) return;
    // eslint-disable-next-line react/set-state-in-effect -- load updates only after awaiting the API.
    void load();
    const timer = setInterval(load, 2500);
    return () => clearInterval(timer);
  }, [active, load]);
  useEffect(() => {
    if (
      !active ||
      !selected ||
      !["running", "waiting_confirm"].includes(selected.status)
    )
      return;
    const timer = setInterval(
      () =>
        api
          .agentTask(selected.id)
          .then(setSelected)
          .catch(() => {}),
      1500,
    );
    return () => clearInterval(timer);
  }, [active, selected]);
  const decide = async (approve: boolean) => {
    if (!selected) return;
    try {
      await api.agentConfirm(selected.id, approve);
      setSelected(await api.agentTask(selected.id));
      void load();
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <section className="tasks-page">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">RESEARCH ACTIVITY</span>
          <h1>任务记录</h1>
          <p>查看处理进度、研究结果和待确认的操作。</p>
        </div>
        <button onClick={load}>刷新记录</button>
      </div>
      {error && (
        <div className="local-error" role="alert">
          {error}
        </div>
      )}
      <div className="task-columns">
        <section className="surface-card">
          <h2>Agent 研究任务</h2>
          {!agents.length && (
            <p className="empty-copy">
              暂无研究任务。在研究助手中创建一个任务，过程与结果会保存在这里。
            </p>
          )}
          {agents.map((task) => (
            <button
              className="task-row"
              key={task.id}
              onClick={() =>
                api
                  .agentTask(task.id)
                  .then(setSelected)
                  .catch((e) => setError(String(e)))
              }
            >
              <span>
                <strong>{task.task}</strong>
                <small>
                  {new Date(task.ts * 1000).toLocaleString()} ·{" "}
                  {task.step_count} 步
                </small>
              </span>
              <span className={`task-state ${task.status}`}>
                {STATUS[task.status]}
              </span>
            </button>
          ))}
        </section>
        <section className="surface-card">
          <h2>文献处理</h2>
          <p className="hint">此处显示本次服务启动后的处理记录。</p>
          {!jobs.length && (
            <p className="empty-copy">
              尚无处理记录。导入论文后，进入阅读页开始处理。
            </p>
          )}
          {jobs.map((job) => {
            const stages = (job.result.stages ?? []) as {
              stage: string;
              label: string;
              status: string;
              detail: string;
            }[];
            const failed = stages.some((s) => s.status === "failed");
            return (
              <details className="processing-record" key={job.id}>
                <summary>
                  <span>
                    {job.kind === "process_paper" ? "处理文献" : "检索与处理"}{" "}
                    <small>{String(job.result.source_id ?? job.id)}</small>
                  </span>
                  <span
                    className={`task-state ${failed ? "error" : job.status}`}
                  >
                    {failed ? "处理未完成" : STATUS[job.status]}
                  </span>
                </summary>
                <div className="task-body">
                  {stages.map((s) => (
                    <p key={s.stage}>
                      {s.label} ·{" "}
                      {s.status === "ok"
                        ? "完成"
                        : s.status === "skipped"
                          ? "已复用"
                          : s.status === "failed"
                            ? "失败"
                            : "进行中"}
                      <small>{s.detail}</small>
                    </p>
                  ))}
                  {job.error && <p className="err">{job.error}</p>}
                  {typeof job.result.source_id === "string" && (
                    <button
                      onClick={() => onOpenDetail(String(job.result.source_id))}
                    >
                      查看论文 →
                    </button>
                  )}
                </div>
              </details>
            );
          })}
        </section>
      </div>
      {selected && (
        <section className="surface-card task-detail">
          <div className="card-heading">
            <h2>{selected.task}</h2>
            <button onClick={() => setSelected(null)}>收起</button>
          </div>
          {selected.allowed_paper_ids && (
            <p className="scope-caption">
              论文范围：{selected.allowed_paper_ids.join("、")}
            </p>
          )}
          {selected.pending_confirm && (
            <div className="confirm-card">
              <strong>操作需要确认</strong>
              {selected.pending_confirm.titles.map((title) => (
                <p key={title}>{title}</p>
              ))}
              <button className="primary" onClick={() => decide(true)}>
                同意执行
              </button>
              <button onClick={() => decide(false)}>拒绝</button>
            </div>
          )}
          {selected.steps?.map((step, i) => (
            <details className="task-step" key={i}>
              <summary>
                {step.step}. {step.tool} · {step.status}
              </summary>
              <p>{step.detail}</p>
              <pre>{JSON.stringify(step.arguments, null, 2)}</pre>
              {step.comparison && (
                <FieldEvidence papers={step.comparison.papers} />
              )}
            </details>
          ))}
          {selected.answer && <Markdown text={selected.answer} />}
          {selected.error && <div className="err">{selected.error}</div>}
        </section>
      )}
    </section>
  );
}
