import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type AgentTaskBrief,
  type AgentTaskState,
  type Job,
} from "../api";
import Markdown from "../md";
import AgentRunReview from "../components/AgentRunReview";
import { OUTCOME_LABEL, exportRun } from "../runReview";
import DownloadProgress from "../components/DownloadProgress";

const STATUS: Record<string, string> = {
  running: "进行中",
  waiting_confirm: "等待确认",
  done: "执行结束",
  error: "未完成",
  interrupted: "已中断",
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
  const [cleanup, setCleanup] = useState<{ source_id: string; error: string }[]>([]);
  const [cleaning, setCleaning] = useState(false);
  const [demos, setDemos] = useState<AgentTaskState[]>([]);
  const [demoError, setDemoError] = useState("");
  const selectedId = useRef<string | null>(null);
  const detailRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected?.id) {
      detailRef.current?.focus({ preventScroll: true });
      detailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }, [selected?.id]);
  const openTask = async (id: string) => {
    selectedId.current = id;
    try { const data = await api.agentTask(id); if (selectedId.current === id) setSelected(data); }
    catch (e) { if (selectedId.current === id) setError(String(e)); }
  };
  const load = useCallback(async () => {
    const results = await Promise.allSettled([api.agentTasks(), api.jobs(), api.pendingCleanup()]);
    if (results[0].status === "fulfilled") setAgents(results[0].value.tasks);
    if (results[1].status === "fulfilled")
      setJobs(results[1].value.jobs.slice().reverse());
    if (results[2].status === "fulfilled") setCleanup(results[2].value.items);
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
    if (active) void api.agentDemos().then(d => { setDemos(d.demos); setDemoError(""); })
      .catch(() => setDemoError("演示回放暂时不可用。"));
  }, [active]);
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
          .then(data => { if (selectedId.current === data.id) setSelected(data); })
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
          <p>从目标验收、原文证据和执行过程回查研究结果。</p>
        </div>
        <button onClick={load}>刷新记录</button>
      </div>
      {error && (
        <div className="local-error" role="alert">
          {error}
        </div>
      )}
      {!!cleanup.length && <section className="surface-card" role="status">
        <h2>待清理的删除产物（{cleanup.length}）</h2>
        <p>这些论文的记录已移除，残留索引已从问答范围排除。恢复后可重试清理。</p>
        {cleanup.map(row => <p key={row.source_id}>{row.source_id}<small>{row.error}</small></p>)}
        <button disabled={cleaning} onClick={async () => {
          setCleaning(true);
          try { await api.retryCleanup(); await load(); }
          catch (e) { setError(String(e)); }
          finally { setCleaning(false); }
        }}>{cleaning ? "清理中…" : "重试清理"}</button>
      </section>}
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
              aria-pressed={selected?.id === task.id}
              onClick={() => void openTask(task.id)}
            >
              <span>
                <strong>{task.task}</strong>
                <small>
                  {new Date(task.ts * 1000).toLocaleString()} ·{" "}
                  {task.step_count} 步
                </small>
              </span>
              <span className={`task-state ${task.status === "done" ? `outcome-${task.verification?.status ?? "unverified"}` : task.status}`}>
                {task.status === "done" ? OUTCOME_LABEL[task.verification?.status ?? "unverified"] : STATUS[task.status]}
              </span>
            </button>
          ))}
        </section>
        <section className="surface-card">
          <h2>全文下载与文献处理</h2>
          <p className="hint">记录与逐篇结果保存在本地，重启后可回看；中断的下载可仅重试未完成项。</p>
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
            const failed =
              stages.some((s) => s.status === "failed") ||
              (job.kind === "download_papers" &&
                Array.isArray(job.result.items) &&
                job.result.items.some((item) =>
                  ["failed", "missing", "no_link", "interrupted"].includes(item.status),
                ));
            return (
              <details className="processing-record" key={job.id}>
                <summary>
                  <span>
                    {job.kind === "download_papers"
                      ? "获取所选全文"
                      : job.kind === "process_paper"
                        ? "处理文献"
                        : "检索与处理"}{" "}
                    <small>{String(job.result.source_id ?? job.id)}</small>
                  </span>
                  <span
                    className={`task-state ${failed ? "error" : job.status}`}
                  >
                    {job.status === "running"
                      ? "进行中"
                      : failed
                        ? job.kind === "download_papers"
                          ? "部分未取得全文"
                          : "处理未完成"
                        : STATUS[job.status]}
                  </span>
                </summary>
                <div className="task-body">
                  {job.kind === "download_papers" && (
                    <DownloadProgress job={job} onOpenDetail={onOpenDetail} onRetried={() => void load()} />
                  )}
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
      <section className="surface-card demo-section">
        <div className="card-heading"><div><span className="eyebrow">REPLAY & VERIFY</span><h2>研究任务演示回放</h2></div><span className="hint">查看已录制过程，不会执行新任务</span></div>
        <p className="hint">隔离资料下的成功与失败案例。每条记录保留验收结果、调用用量和来源，便于复查。</p>
        {demoError && <p className="hint">{demoError}</p>}
        {!demos.length && !demoError && <p className="hint">暂未生成演示记录。</p>}
        <div className="demo-grid">{demos.map(demo => <button className="demo-card" key={demo.id} aria-pressed={selected?.id === demo.id}
          onClick={() => { selectedId.current = demo.id; setSelected(demo); }}>
          <span className={`outcome-tag outcome-${demo.verification?.status ?? "unverified"}`}>{OUTCOME_LABEL[demo.verification?.status ?? "unverified"]}</span>
          <strong>{demo.task}</strong><small>{demo.step_count} 个工具步骤 · {demo.telemetry?.call_count ?? 0} 次调用</small>
        </button>)}</div>
      </section>
      {selected && (
        <section className="surface-card task-detail" ref={detailRef} tabIndex={-1} aria-label="研究任务详情">
          <div className="card-heading">
            <h2>{selected.task}</h2>
            <div className="task-actions"><button onClick={() => exportRun(selected)}>导出记录</button><button onClick={() => { selectedId.current = null; setSelected(null); }}>收起</button></div>
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
          <AgentRunReview task={selected} />
          {selected.answer && <div className="run-answer"><h3>研究结果</h3><Markdown text={selected.answer} /></div>}
          {selected.error && <div className="err">{selected.error}</div>}
        </section>
      )}
    </section>
  );
}
