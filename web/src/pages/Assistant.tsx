import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ChatSession, type ChatMessage, type ChatReply, type SourceRef, type ComparisonReply,
  type AgentTaskState, type AgentTaskBrief, type AgentStepView } from "../api";
import Markdown from "../md";
import AnswerSources from "../components/AnswerSources";
import CallLedgerView from "../components/CallLedgerView";
import PaperScopePicker from "../components/PaperScopePicker";
import ComparisonResult from "../components/ComparisonResult";
import ConversationTask from "../components/ConversationTask";
import { OUTCOME_LABEL } from "../runReview";

type Action = "chat" | "compare" | "execute";
interface Turn {
  role: "user" | "bot"; text: string; notes?: string[]; understanding?: string;
  sources?: SourceRef[]; telemetry?: ChatReply["telemetry"]; comparison?: ComparisonReply | null;
  taskId?: string; paperIds?: string[] | null; action?: string; intent?: string;
  answerStatus?: string;
}
const ACCEPTANCE: Record<string, Record<string, unknown>[]> = {
  process: [], save: [{ kind: "library_add", min_added: 1 }],
  evidence: [{ kind: "evidence_answer" }, { kind: "no_library_mutation" }],
  preview: [{ kind: "tool_success", tools: ["search_papers"] }, { kind: "no_library_mutation" }],
};
const actionLabel: Record<Action, string> = { chat: "提问", compare: "比较论文", execute: "执行操作" };
function turnsFromHistory(messages: ChatMessage[]): Turn[] {
  return messages.map(m => ({ role: m.role === "user" ? "user" : "bot", text: m.content,
    notes: m.notes, understanding: m.understanding ?? undefined, sources: m.sources ?? undefined,
    telemetry: m.details?.telemetry, comparison: m.details?.comparison, taskId: m.details?.task_id,
    paperIds: m.details?.paper_ids, action: m.details?.action, intent: m.intent ?? undefined,
    answerStatus: m.details?.answer_status }));
}
function setSessionUrl(sid: string | null) {
  const u = new URL(window.location.href);
  if (sid) u.searchParams.set("s", sid); else u.searchParams.delete("s");
  window.history.replaceState(null, "", u);
}

export default function Assistant({ active, onAgentBusy, onOpenDetail, onLibChanged,
  pendingTask, onConsumePendingTask, researchScope, onDiscover }: {
  active: boolean; onAgentBusy: (busy: boolean) => void; onOpenDetail: (id: string) => void;
  onLibChanged: () => void; pendingTask: { task: string; anchor: string } | null;
  onConsumePendingTask: () => void;
  researchScope?: { ids: string[]; compare: boolean; key: number } | null;
  onDiscover: () => void;
}) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [action, setAction] = useState<Action>("chat");
  const [paperIds, setPaperIds] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [history, setHistory] = useState<AgentTaskBrief[]>([]);
  const [tasks, setTasks] = useState<Record<string, AgentTaskState>>({});
  const [taskErrors, setTaskErrors] = useState<Record<string, string>>({});
  const [runningId, setRunningId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [anchor, setAnchor] = useState("");
  const [acceptance, setAcceptance] = useState("process");
  const viewRequest = useRef(0);
  const handledResearch = useRef<number | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const nearBottom = useRef(true);

  const refreshSessions = useCallback(async () => {
    try { setSessions((await api.sessions()).sessions); }
    catch (e) { setError(`对话历史加载失败：${e}`); }
  }, []);
  const refreshHistory = useCallback(async () => {
    try { setHistory((await api.agentTasks()).tasks); }
    catch (e) { setError(`执行记录加载失败：${e}`); }
  }, []);
  const loadTask = useCallback(async (id: string) => {
    try {
      const task = await api.agentTask(id);
      setTasks(t => ({ ...t, [id]: task }));
      setTaskErrors(t => ({ ...t, [id]: "" }));
      if (task.status === "running" || task.status === "waiting_confirm") setRunningId(current => current ?? id);
    } catch (e) { setTaskErrors(t => ({ ...t, [id]: String(e) })); }
  }, []);
  const openSession = useCallback(async (sid: string | null) => {
    const version = ++viewRequest.current;
    setActiveId(sid); setSessionUrl(sid); setError(""); setNotice("");
    setAction("chat"); setInput(""); setAnchor(""); setAcceptance("process"); nearBottom.current = true;
    setTurns([]); setPaperIds(null); setLoadingSession(!!sid);
    if (!sid) return;
    try {
      const data = await api.sessionMessages(sid);
      if (version !== viewRequest.current) return;
      setTurns(turnsFromHistory(data.messages)); setPaperIds(data.paper_ids ?? null);
      for (const m of data.messages) if (m.details?.task_id) void loadTask(m.details.task_id);
    } catch (e) { if (version === viewRequest.current) setError(`无法打开对话：${e}`); }
    finally { if (version === viewRequest.current) setLoadingSession(false); }
  }, [loadTask]);
  useEffect(() => {
    void refreshSessions(); void refreshHistory();
    const fromUrl = new URLSearchParams(window.location.search).get("s");
    if (fromUrl) void openSession(fromUrl);
  }, [refreshSessions, refreshHistory, openSession]);
  useEffect(() => { if (active) inputRef.current?.focus(); }, [active]);
  useEffect(() => { onAgentBusy(runningId !== null); }, [runningId, onAgentBusy]);
  useEffect(() => {
    if (nearBottom.current) logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, busy, tasks]);
  useEffect(() => {
    if (busy || loadingSession) return;
    if (pendingTask) {
      // Receiving a cross-page draft never submits an operation.
      void openSession(null);
      setInput(pendingTask.task); setAnchor(pendingTask.anchor);
      setPaperIds([pendingTask.anchor]); setAction("execute");
      onConsumePendingTask();
    } else if (researchScope && handledResearch.current !== researchScope.key) {
      handledResearch.current = researchScope.key;
      setPaperIds(researchScope.ids); setAction(researchScope.compare ? "compare" : "chat");
      setInput(""); setAnchor(""); setNotice("已带入文献库中的选择，请输入问题后发送。");
    }
  }, [pendingTask, researchScope, onConsumePendingTask, openSession, busy, loadingSession]);

  // The live connection follows its task independently of the conversation on screen.
  useEffect(() => {
    if (!runningId) return;
    const id = runningId;
    let closed = false, polling = false, timer: number | undefined;
    const finish = () => {
      setRunningId(current => current === id ? null : current);
      void refreshHistory(); onLibChanged();
    };
    const update = (task: AgentTaskState) => {
      if (closed) return;
      setTasks(t => ({ ...t, [id]: task }));
      if (task.status === "done" || task.status === "error") finish();
    };
    const poll = async () => {
      try {
        const task = await api.agentTask(id); update(task);
        if (!closed && (task.status === "running" || task.status === "waiting_confirm")) timer = window.setTimeout(() => void poll(), 1500);
      } catch (e) {
        if (!closed) { setTaskErrors(t => ({ ...t, [id]: `连接中断，可重新加载记录：${e}` }));
          setRunningId(current => current === id ? null : current); }
      }
    };
    const es = new EventSource(`/api/agent/tasks/${encodeURIComponent(id)}/events`);
    es.onmessage = event => {
      if (closed) return;
      let m: { type: string; state?: Partial<AgentTaskState>; step?: AgentStepView } & Partial<AgentTaskState>;
      try { m = JSON.parse(event.data); } catch { return; }
      if (m.type === "snapshot") update(m as AgentTaskState);
      else if (m.type === "step" && m.step) {
        const step = m.step;
        setTasks(t => t[id] ? { ...t, [id]: { ...t[id], steps: [...(t[id].steps ?? []), step] } } : t);
      } else if (m.type === "status" && m.state) {
        const state = m.state;
        setTasks(t => t[id] ? { ...t, [id]: { ...t[id], ...state, steps: t[id].steps } } : t);
        if (state.status === "done" || state.status === "error") finish();
      } else if (m.type === "end") es.close();
    };
    es.onerror = () => { es.close(); if (!closed && !polling) { polling = true; void poll(); } };
    return () => { closed = true; es.close(); if (timer) window.clearTimeout(timer); };
  }, [runningId, refreshHistory, onLibChanged]);

  const removeSession = async (id: string) => {
    try { await api.deleteSession(id); if (id === activeId) await openSession(null); await refreshSessions(); }
    catch (e) { setError(`删除对话失败：${e}`); }
  };
  const send = async () => {
    const msg = input.trim();
    if (!msg || busy || loadingSession || (action === "execute" && runningId)) return;
    if (paperIds !== null && paperIds.length === 0) { setError("请至少选择一篇论文，或切换为整个文献库。"); return; }
    if (action === "compare" && (!paperIds || paperIds.length < 2)) { setError("比较前请在资料范围中选择 2～6 篇论文。"); return; }
    const request = viewRequest.current, submittedAction = action, ids = paperIds;
    setError(""); setNotice(""); setBusy(true); nearBottom.current = true;
    try {
      if (submittedAction === "execute") {
        const sid = activeId ?? (await api.createSession(msg)).id;
        setActiveId(sid); setSessionUrl(sid);
        const task = await api.agentSubmit(msg, undefined, anchor || undefined, ids, ACCEPTANCE[acceptance], sid);
        if (request !== viewRequest.current) return;
        setActiveId(sid); setSessionUrl(sid); setTasks(t => ({ ...t, [task.id]: task }));
        setTurns(t => [...t, { role: "user", text: msg, paperIds: ids, action: "execute" },
          { role: "bot", text: "", taskId: task.id, paperIds: ids }]);
        if (task.status === "running" || task.status === "waiting_confirm") setRunningId(task.id);
        setAnchor(""); void refreshHistory();
      } else {
        const reply = await api.chat(msg, activeId, ids, submittedAction);
        if (request !== viewRequest.current) return;
        setActiveId(reply.session_id); setSessionUrl(reply.session_id);
        setTurns(t => [...t, { role: "user", text: msg, paperIds: ids, action: submittedAction },
          { role: "bot", text: reply.reply, understanding: reply.understanding, notes: reply.notes,
            sources: reply.sources, telemetry: reply.telemetry, comparison: reply.comparison,
            paperIds: ids, intent: reply.intent, answerStatus: reply.answer_status }]);
      }
      setInput(""); setAction("chat"); void refreshSessions(); inputRef.current?.focus();
    } catch (e) { if (request === viewRequest.current) setError(String(e)); void refreshSessions(); }
    finally { setBusy(false); }
  };
  const openLegacyTask = async (h: Pick<AgentTaskBrief, "id" | "live">) => {
    const version = ++viewRequest.current;
    setLoadingSession(true); setError("");
    try {
      const task = await api.agentTask(h.id);
      if (version !== viewRequest.current) return;
      setActiveId(null); setSessionUrl(null); setInput(""); setAction("chat"); setAnchor("");
      setPaperIds(task.allowed_paper_ids ?? null); nearBottom.current = true;
      setTasks(t => ({ ...t, [task.id]: task }));
      setTurns([{ role: "user", text: task.task, action: "execute", paperIds: task.allowed_paper_ids }, { role: "bot", text: "", taskId: task.id }]);
      setNotice("正在查看独立执行记录。新提问会创建对话；执行过程也可在任务记录中回看。");
      if (h.live && (task.status === "running" || task.status === "waiting_confirm")) setRunningId(current => current ?? task.id);
    } catch (e) { if (version === viewRequest.current) setError(String(e)); }
    finally { if (version === viewRequest.current) setLoadingSession(false); }
  };
  const decideConfirm = async (id: string, approve: boolean) => {
    setConfirming(true); setError("");
    try {
      await api.agentConfirm(id, approve);
      const task = await api.agentTask(id); setTasks(t => ({ ...t, [id]: task }));
      if (task.status === "done" || task.status === "error") setRunningId(current => current === id ? null : current);
      void refreshHistory();
    } catch (e) { setError(`确认操作失败，请重新加载执行记录：${e}`); }
    finally { setConfirming(false); }
  };
  const copy = async (turn: Turn) => {
    const sources = turn.sources?.map(s => `${s.n.map(n => `[${n}]`).join("")} ${s.title} · ${s.section} · ${s.paper_id}${s.page ? ` · 第${s.page}页` : ""}`).join("\n") ?? "";
    try { await navigator.clipboard.writeText(turn.text + (sources ? `\n\n来源：\n${sources}` : "")); setNotice("已复制答案与来源。"); }
    catch { setError("复制失败，请手动选择文本。"); }
  };
  const visibleRunning = turns.some(t => t.taskId === runningId);
  const selectedTitle = sessions.find(s => s.id === activeId)?.title ?? "新的研究对话";
  return <div className="assistant unified-assistant">
    <aside className="session-pane" aria-label="研究历史">
      <button className="primary new-chat" disabled={busy} onClick={() => void openSession(null)}>＋ 新对话</button>
      <div className="session-list">
        <div className="history-heading"><span>研究对话</span><button className="ghost sm" onClick={() => void refreshSessions()}>刷新</button></div>
        {!sessions.length && <p className="hint history-empty">提问、比较和操作会保存在同一条对话里。</p>}
        {sessions.map(s => <div className={`research-session ${s.id === activeId ? "on" : ""}`} key={s.id}>
          <button className="session-open" disabled={busy} onClick={() => void openSession(s.id)} title={s.title}>
            <span className="s-title">{s.title}</span><span className="s-meta">{s.message_count} 条 · {s.updated_at.slice(5, 16).replace("T", " ")}</span>
          </button><button className="session-delete" disabled={busy} aria-label={`删除对话 ${s.title}`} onClick={() => void removeSession(s.id)}>×</button>
        </div>)}
        <details className="execution-history"><summary>执行记录 <span>{history.length}</span></summary>
          <button className="ghost sm" onClick={() => void refreshHistory()}>刷新执行记录</button>
          {history.map(h => <button className="session-item task-session" key={h.id} disabled={busy} onClick={() => void openLegacyTask(h)}>
            <span className="s-title">{h.task}</span><span className="s-meta">{h.status === "done" ? OUTCOME_LABEL[h.verification?.status ?? "unverified"] :
              h.status === "error" || !h.live ? "已结束 / 中断" : h.status === "waiting_confirm" ? "等待确认" : "运行中"} · {h.step_count} 步</span>
          </button>)}
          {!history.length && <p className="hint">还没有执行记录。</p>}
        </details>
      </div>
      <p className="history-footnote hint">新对话清空上下文。历史记录随时可以继续。</p>
    </aside>
    <div className="chat research-chat">
      <header className="research-chat-header"><div><span className="eyebrow">RESEARCH CONVERSATION</span><h1 title={selectedTitle}>{selectedTitle}</h1></div>
        <button className="ghost" onClick={onDiscover}>发现新论文 ↗</button></header>
      <PaperScopePicker ids={paperIds} onChange={ids => { setPaperIds(ids); setAnchor(""); }} disabled={busy || loadingSession} onOpenDetail={onOpenDetail} />
      {runningId && !visibleRunning && <div className="research-running" role="status">有一项操作仍在执行
        <button className="ghost sm" onClick={() => void openLegacyTask({ id: runningId, live: true })}>查看进度</button></div>}
      <div className="chat-log research-log" ref={logRef} aria-label="研究对话内容" aria-busy={busy || loadingSession}
        onScroll={() => { const log = logRef.current; if (log) nearBottom.current = log.scrollHeight - log.scrollTop - log.clientHeight < 100; }}>
        {loadingSession ? <p className="hint">正在恢复对话…</p> : !turns.length && <div className="research-welcome">
          <span className="welcome-mark" aria-hidden>⌁</span><h2>从一个研究问题开始</h2>
          <p>提问、比较、继续追问，都在这里完成。<br />需要保存论文或生成方案时，在下方选择「执行操作」。</p>
          <div className="research-starters">{[ ["梳理方向", "当前库中的论文都是哪些研究方向上的？"], ["统计论文", "RAG 方向上有多少篇论文？"], ["理解方法", "解释库中论文所使用的方法"] ].map(([label, text]) =>
            <button key={label} onClick={() => { setInput(text); setAction("chat"); inputRef.current?.focus(); }}><strong>{label}</strong><span>{text}</span></button>)}</div>
          <p className="hint">选中资料后可以比较；首次提问自动保存对话，后续问题会沿用最近语境。</p>
        </div>}
        {!loadingSession && turns.map((turn, i) => <article className={`research-turn ${turn.role}`} key={i}>
          <div className="turn-heading"><strong>{turn.role === "user" ? "你" : "研究助手"}</strong>
            {turn.role === "user" && <span>{actionLabel[(turn.action ?? "chat") as Action] ?? "提问"} · {turn.paperIds?.length ? `${turn.paperIds.length} 篇所选论文` : "整个文献库"}</span>}
            {turn.role === "bot" && !turn.taskId && <button className="ghost sm" onClick={() => void copy(turn)}>复制答案与来源</button>}</div>
          {turn.taskId ? tasks[turn.taskId] ? <ConversationTask task={tasks[turn.taskId]} onConfirm={(id, yes) => void decideConfirm(id, yes)} confirming={confirming} /> :
            <p className="hint">{taskErrors[turn.taskId] || "正在加载执行记录…"}<button onClick={() => void loadTask(turn.taskId!)}>重新加载记录</button></p> :
            turn.role === "user" ? <p className="user-question">{turn.text}</p> : <>
              {turn.answerStatus === "generated" && <p className="hint">回答已生成，请结合下方原文证据核对。</p>}
              {turn.comparison ? <ComparisonResult result={turn.comparison} /> : <Markdown text={turn.text} />}
              {!!turn.sources?.length && <AnswerSources sources={turn.sources} onOpenDetail={onOpenDetail} />}
              {turn.notes?.map((note, j) => <p className="hint" key={j}>{note}</p>)}
              {turn.intent === "search" && <button onClick={() => { setInput(turns[i - 1]?.text ?? ""); setAction("execute"); }}>作为操作执行（先预填）</button>}
              {turn.telemetry?.call_count !== undefined && <CallLedgerView telemetry={turn.telemetry} />}
            </>}
        </article>)}
        {busy && <p className="hint" role="status"><span className="spin" /> {action === "execute" ? "正在提交操作…" : "正在查找资料与整理回答…"}</p>}
      </div>
      <div className="research-composer">
        {error && <div className="local-error" role="alert">{error}</div>}
        {notice && <p className="hint composer-notice" role="status">{notice}</p>}
        <form onSubmit={e => { e.preventDefault(); void send(); }}>
          <div className="composer-toolbar"><label>本次操作 <select aria-label="本次操作" value={action} disabled={busy || loadingSession}
            onChange={e => { setAction(e.target.value as Action); setError(""); }}>
            <option value="chat">提问</option><option value="compare">比较论文</option><option value="execute">执行操作</option></select></label>
            <span className="hint">{action === "execute" ? "提交后规划并执行，批量入库或运行代码时会请求确认" : action === "compare" ? "比较所选论文，结果保存在当前对话" : "分析库内资料，可连续追问"}</span></div>
          <textarea ref={inputRef} aria-label="研究问题" placeholder={action === "execute" ? "描述要完成的操作，例如：查找两篇综述并保存题录…" : action === "compare" ? "希望比较什么？例如：数据集、指标和评估条件…" : "提问或继续追问，例如：这些论文采用了什么方法？"}
            value={input} onChange={e => setInput(e.target.value)} rows={2} maxLength={action === "compare" ? 500 : 2000} disabled={loadingSession}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
          <div className="composer-footer"><span className="hint">Enter 发送 · Shift + Enter 换行{anchor ? " · 已锚定当前论文" : ""}</span>
            <button className="primary" disabled={busy || loadingSession || !input.trim() || (action === "execute" && !!runningId)}>{busy ? "处理中…" : action === "execute" ? "提交执行" : action === "compare" ? "开始比较" : "发送"}</button></div>
        </form>
        {action === "execute" && <details className="execution-options"><summary>操作验收选项{runningId ? " · 请等待当前操作结束后再提交" : ""}</summary>
          <label>验收目标 <select aria-label="结果验收方式" value={acceptance} onChange={e => setAcceptance(e.target.value)}>
            <option value="process">仅记录过程，结果待核验</option><option value="save">至少保存 1 篇新题录</option>
            <option value="evidence">有来源与有效引用的库内回答</option><option value="preview">检索预览，文献记录不变更</option></select></label>
          <p className="hint">验收只覆盖所选条件。用量预算在下一轮决策前检查，工具内部调用和收尾总结仍会产生用量。</p></details>}
      </div>
    </div>
  </div>;
}
