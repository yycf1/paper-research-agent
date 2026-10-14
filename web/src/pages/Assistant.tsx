import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type ChatSession,
  type ChatReply,
  type SourceRef,
  type AgentTaskState,
  type AgentTaskBrief,
  type AgentStepView,
} from "../api";
import Markdown from "../md";
import Evidence from "../components/Evidence";
import FieldEvidence from "../components/FieldEvidence";

interface Turn {
  role: "user" | "bot";
  text: string;
  understanding?: string;
  notes?: string[];
  newPapers?: { source_id: string; title: string }[];
  sources?: SourceRef[];
}

const WELCOME: Turn[] = [
  {
    role: "bot",
    text:
      "你好，我是 paper-agent 助手。可以：\n① 检索新论文——「帮我找 2023 年以后的 transformer 综述」\n② 问答库内文献——「图像分割那篇综述的主要结论？」（带出处）\n③ 查看库状态——「库里有多少论文」\n④ 推荐方向——「不知道该看什么论文」\n\n对话会自动保存，左侧可新建或切换会话。",
  },
];

const AGENT_SAMPLES = [
  "找 2 篇 2024 年以后的 GNN 推荐综述入库",
  "看看库状态，再给我推荐研究方向",
  "库里医学影像的论文都用了什么数据集？",
];

const STATUS_TEXT: Record<string, string> = {
  ok: "ok",
  degraded: "degraded",
  retryable_error: "retryable",
  fatal: "fatal",
};

function turnsFromHistory(messages: { role: string; content: string; understanding: string | null; notes: string[]; sources?: SourceRef[] | null }[]): Turn[] {
  return messages.map((m) => ({
    role: m.role === "user" ? "user" : "bot",
    text: m.content,
    understanding: m.understanding ?? undefined,
    notes: m.notes ?? undefined,
    sources: m.sources ?? undefined,
  }));
}

export default function Assistant({
  active,
  onAgentBusy,
  onOpenDetail,
  onLibChanged,
  pendingTask,
  onConsumePendingTask,
}: {
  /** 本页是否可见（App 双页常驻挂载，切走时仅隐藏） */
  active: boolean;
  /** Agent 任务运行状态上报：顶栏「助手」上亮执行中小红点 */
  onAgentBusy: (busy: boolean) => void;
  onOpenDetail: (id: string) => void;
  onLibChanged: () => void;
  /** M26：详情页「交给 Agent 续做」跨页传入的锚定任务 */
  pendingTask: { task: string; anchor: string } | null;
  onConsumePendingTask: () => void;
}) {
  const [mode, setMode] = useState<"chat" | "agent">("chat");
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>(WELCOME);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [agentTask, setAgentTask] = useState<AgentTaskState | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const pollTimer = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<AgentTaskBrief[]>([]);
  const [anchorId, setAnchorId] = useState("");  // M26：本任务锚定论文（提交时随任务下发）

  const refreshSessions = useCallback(
    () => api.sessions().then((d) => setSessions(d.sessions)).catch(() => {}),
    [],
  );

  const openSession = useCallback(async (sid: string | null) => {
    setActiveId(sid);
    const u = new URL(window.location.href);
    if (sid) u.searchParams.set("s", sid);
    else u.searchParams.delete("s");
    window.history.replaceState(null, "", u);
    if (sid === null) {
      setTurns(WELCOME);
      return;
    }
    try {
      const data = await api.sessionMessages(sid);
      const t = turnsFromHistory(data.messages);
      setTurns(t.length > 0 ? t : WELCOME);
    } catch {
      setTurns(WELCOME);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
    const fromUrl = new URLSearchParams(window.location.search).get("s");
    if (fromUrl) openSession(fromUrl);
  }, [refreshSessions, openSession]);

  // 组件卸载时停掉轮询与 SSE，避免 setState 到已卸载组件
  useEffect(
    () => () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      esRef.current?.close();
    },
    [],
  );

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, busy, agentTask, agentBusy]);

  // 常驻挂载后 autoFocus 只在首次挂载生效，切回本页时补一次焦点
  useEffect(() => {
    if (active) inputRef.current?.focus();
  }, [active]);
  // 运行状态上报顶栏（切到文献库也能看到任务还活着）
  useEffect(() => {
    onAgentBusy(agentBusy);
  }, [agentBusy, onAgentBusy]);

  // M26：领取跨页传入的锚定任务——切 Agent 模式、预填不自动运行（用户确认后执行）
  useEffect(() => {
    if (!pendingTask) return;
    setMode("agent");
    setInput(pendingTask.task);
    setAnchorId(pendingTask.anchor);
    onConsumePendingTask();
  }, [pendingTask, onConsumePendingTask]);

  const newChat = () => openSession(null);

  const removeSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
    } catch {
      /* 列表刷新时暴露错误 */
    }
    if (sid === activeId) await openSession(null);
    refreshSessions();
  };

  const send = async (text?: string) => {
    const msg = (text ?? input).trim();
    if (!msg || busy) return;
    setInput("");
    setTurns((t) => [...t, { role: "user", text: msg }]);
    setBusy(true);
    try {
      const reply: ChatReply = await api.chat(msg, activeId);
      setTurns((t) => [
        ...t,
        {
          role: "bot",
          text: reply.reply,
          understanding: reply.understanding,
          notes: reply.notes,
          newPapers: reply.new_papers,
          sources: reply.sources,
        },
      ]);
      if (reply.session_id !== activeId) {
        await openSession(reply.session_id);
        refreshSessions();
      } else {
        refreshSessions();
      }
      if (reply.intent === "search" && reply.new_papers.length > 0) onLibChanged();
    } catch (e) {
      setTurns((t) => [...t, { role: "bot", text: `请求失败：${e}` }]);
    } finally {
      setBusy(false);
    }
  };

  // ---- Agent 模式：提交 + SSE 事件流（轮询兜底） + 确认门 ----

  const tick = useCallback(
    async (id: string) => {
      try {
        const t = await api.agentTask(id);
        setAgentTask(t);
        if (t.status === "done" || t.status === "error") {
          setAgentBusy(false);
          if (t.status === "done") onLibChanged(); // 任务可能入库了论文
          return;
        }
      } catch {
        setAgentBusy(false);
        return;
      }
      pollTimer.current = window.setTimeout(() => void tick(id), 1200);
    },
    [onLibChanged],
  );

  const runAgent = async (text?: string) => {
    const task = (text ?? input).trim();
    if (!task || agentBusy) return;
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
    const anchor = anchorId; // 锚只随本次提交走，提交后清掉，不污染下一个任务
    setInput("");
    setAgentBusy(true);
    setAgentTask(null);
    try {
      const t = await api.agentSubmit(task, undefined, anchor || undefined);
      setAnchorId("");
      setAgentTask(t);
      listenEvents(t.id);
    } catch (e) {
      setAgentBusy(false);
      setAgentTask({
        id: "", task, status: "error", step_count: 0, pending_confirm: null,
        answer: "", stopped_reason: "error", usage: { prompt: 0, completion: 0 },
        error: String(e), steps: [],
      });
    }
  };

  // SSE 事件流：连接即收全量快照，此后每步/状态变化增量推送，终态后服务端发 end。
  // 断流（代理缓冲/服务重启）时回退到轮询兜底，保证 UI 不冻结。
  const listenEvents = (id: string) => {
    esRef.current?.close();
    const es = new EventSource(`/api/agent/tasks/${encodeURIComponent(id)}/events`);
    esRef.current = es;
    es.onmessage = (ev) => {
      let m:
        | ({ type: "snapshot" } & AgentTaskState)
        | { type: "step"; step: AgentStepView }
        | { type: "status"; state: Partial<AgentTaskState> }
        | { type: "end" };
      try {
        m = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (m.type === "snapshot") {
        setAgentTask(m);
      } else if (m.type === "step") {
        const step = m.step;
        setAgentTask((cur) => (cur ? { ...cur, steps: [...(cur.steps ?? []), step] } : cur));
      } else if (m.type === "status") {
        const st = m.state;
        setAgentTask((cur) => (cur ? { ...cur, ...st, steps: cur.steps } : (st as AgentTaskState)));
        if (st.status === "done" || st.status === "error") setAgentBusy(false);
        if (st.status === "done") onLibChanged();
      } else {
        es.close();
        esRef.current = null;
      }
    };
    es.onerror = () => {
      es.close();
      esRef.current = null;
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      pollTimer.current = window.setTimeout(() => void tick(id), 300);
    };
  };

  // ---- 任务历史（M23）：live 条目来自注册表，其余由磁盘轨迹回读 ----

  const toggleHistory = async () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next) {
      const d = await api.agentTasks().catch(() => null);
      if (d) setHistory(d.tasks);
    }
  };

  const loadHistory = async (h: AgentTaskBrief) => {
    setHistoryOpen(false);
    esRef.current?.close();
    esRef.current = null;
    if (pollTimer.current) window.clearTimeout(pollTimer.current);
    try {
      const full = await api.agentTask(h.id); // 历史 id 由轨迹文件回读，含全部步骤
      setAgentBusy(false);
      setAgentTask(full);
    } catch {
      /* 轨迹缺失等：保留当前视图 */
    }
  };

  const decideConfirm = async (approve: boolean) => {
    if (!agentTask) return;
    try {
      await api.agentConfirm(agentTask.id, approve);
    } catch {
      /* 状态已变化时忽略，下一次轮询会取到最新状态 */
    }
    try {
      setAgentTask(await api.agentTask(agentTask.id));
    } catch {
      /* 下一次轮询兜底 */
    }
  };

  const steps = agentTask?.steps ?? [];
  const tokens = agentTask ? agentTask.usage.prompt + agentTask.usage.completion : 0;

  return (
    <div className="assistant">
      {mode === "chat" && (
        <aside className="session-pane">
          <button className="primary new-chat" onClick={newChat} disabled={busy}>
            ＋ 新对话
          </button>
          <div className="session-list">
            {sessions.length === 0 && <div className="hint" style={{ padding: "8px 12px" }}>还没有历史会话</div>}
            {sessions.map((s) => (
              <div
                key={s.id}
                className={`session-item ${s.id === activeId ? "on" : ""}`}
                onClick={() => openSession(s.id)}
              >
                <div className="s-title">{s.title}</div>
                <div className="s-meta">
                  {s.message_count} 条 · {s.updated_at.slice(5, 16).replace("T", " ")}
                </div>
                <span
                  className="s-del"
                  title="删除会话"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.id);
                  }}
                >
                  ✕
                </span>
              </div>
            ))}
          </div>
        </aside>
      )}
      <div className="chat">
        <div className="mode-switch">
          <button className={mode === "chat" ? "on" : ""} onClick={() => { setMode("chat"); setAnchorId(""); }}>
            对话
          </button>
          <button className={mode === "agent" ? "on" : ""} onClick={() => setMode("agent")}>
            Agent 任务
          </button>
          {mode === "agent" && (
            <>
              <span className="hint" style={{ alignSelf: "center", marginLeft: 8 }}>
                LLM 自主调用工具完成多步任务；批量入库会先请求确认
              </span>
              <span className="flex-spacer" />
              <button className={historyOpen ? "on" : ""} onClick={() => void toggleHistory()}>
                历史 ▾
              </button>
            </>
          )}
        </div>

        {mode === "agent" && historyOpen && (
          <div className="agent-history">
            {history.length === 0 && (
              <div className="hint" style={{ padding: "10px 28px" }}>
                还没有任务记录（提交过一次任务后，这里跨会话可回看）
              </div>
            )}
            {history.map((h) => (
              <div
                key={`${h.id}-${h.ts}`}
                className={`agent-hist-item ${agentTask?.id === h.id ? "on" : ""}`}
                onClick={() => void loadHistory(h)}
              >
                <span
                  className={`hist-badge ${h.status === "done" ? "done" : h.status === "error" ? "error" : "running"}`}
                >
                  {h.status === "done" ? "完成" : h.status === "error" ? "失败" : h.status === "waiting_confirm" ? "待确认" : "进行中"}
                </span>
                <div className="hi-main">
                  <div className="hi-task">{h.task}</div>
                  <div className="hi-meta hint">
                    {new Date(h.ts * 1000).toLocaleString("zh-CN", {
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {" · "}
                    {h.step_count} 步 · 约 {h.usage.prompt + h.usage.completion} tokens
                    {h.live && (h.status === "running" || h.status === "waiting_confirm") ? " · 进行中" : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {mode === "chat" ? (
          <>
            <div className="chat-log" ref={logRef}>
              {turns.map((t, i) => (
                <div key={i} className={`msg ${t.role}`}>
                  {t.understanding && <div className="under">（{t.understanding}）</div>}
                  {t.role === "bot" ? <Markdown text={t.text} /> : t.text}
                  {t.sources && t.sources.length > 0 && (
                    <div className="sources">
                      {t.sources.map((s, j) => (
                        <div key={j}><div className="src" onClick={() => onOpenDetail(s.paper_id)}>
                          [{(s.n ?? [j + 1]).join("][")}] {s.title.slice(0, 50)}{s.title.length > 50 ? "…" : ""} · {s.section}
                          {s.page ? ` · 第${s.page}页` : ""}
                        </div><Evidence source={s} /></div>
                      ))}
                    </div>
                  )}
                  {t.notes && t.notes.length > 0 && t.notes.map((n, j) => <div key={j} className="note">⚠ {n}</div>)}
                  {t.newPapers && t.newPapers.length > 0 && (
                    <div className="papers-mini">
                      {t.newPapers.map((p) => (
                        <div key={p.source_id} className="paper-mini" onClick={() => onOpenDetail(p.source_id)}>
                          📄 {p.title}
                        </div>
                      ))}
                      <div className="hint">点击可在「文献库」页打开详情</div>
                    </div>
                  )}
                </div>
              ))}
              {busy && (
                <div className="msg bot">
                  <span className="spin" />
                  思考中…（检索类请求通常需要十几秒到一分钟）
                </div>
              )}
            </div>
            <div className="quick">
              {["库里现在有多少论文？", "不知道该看什么论文", "帮我找 2024 年以后的 LLM agent 综述"].map((s) => (
                <span key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </span>
              ))}
            </div>
          </>
        ) : (
          <div className="chat-log agent-log" ref={logRef}>
            {!agentTask && !agentBusy && (
              <div className="agent-empty">
                <div className="hint">
                  描述一个目标，Agent 会自主决定调用哪些工具（检索 / 入库 / 问答 / 状态 / 方向推荐），
                  每一步工具调用实时可见。批量入库（≥5 篇）会先请求你的确认。
                </div>
                <div className="quick" style={{ padding: 0 }}>
                  {AGENT_SAMPLES.map((s) => (
                    <span key={s} className="chip" onClick={() => runAgent(s)}>
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {agentTask && (
              <>
                <div className="agent-task-line">🎯 {agentTask.task}</div>
                {steps.map((s, i) => (
                  <div key={i} className="astep">
                    <span className="s-no">{s.step}</span>
                    <span className="s-tool">{s.tool}</span>
                    <code className="s-args">{JSON.stringify(s.arguments)}</code>
                    <span className={`s-badge s-${s.status}`}>{STATUS_TEXT[s.status] ?? s.status}</span>
                    <span className="s-ms">{s.duration_ms} ms</span>
                    {s.detail && <div className="s-detail">{s.detail}</div>}
                    {s.comparison?.papers?.length ? <FieldEvidence papers={s.comparison.papers} /> : null}
                  </div>
                ))}
                {agentBusy && (
                  <div className="astep running">
                    <span className="spin" /> 执行中…（每步都会实时显示在这里）
                  </div>
                )}

                {agentTask.status === "waiting_confirm" && agentTask.pending_confirm && (
                  <div className="confirm-box">
                    <div className="c-title">
                      ⚠{" "}
                      {agentTask.pending_confirm.titles[0]?.startsWith("运行代码")
                        ? `Agent 请求运行代码（${agentTask.pending_confirm.count} 项）`
                        : `Agent 请求入库 ${agentTask.pending_confirm.count} 篇论文`}
                    </div>
                    {agentTask.pending_confirm.titles.slice(0, 5).map((t, i) => (
                      <div key={i} className="c-item">· {t}</div>
                    ))}
                    {agentTask.pending_confirm.titles.length > 5 && (
                      <div className="c-item hint">…等共 {agentTask.pending_confirm.titles.length} 篇</div>
                    )}
                    <div className="c-actions">
                      <button className="primary" onClick={() => decideConfirm(true)}>
                        {agentTask.pending_confirm.titles[0]?.startsWith("运行代码") ? "确认运行" : "确认入库"}
                      </button>
                      <button className="ghost" onClick={() => decideConfirm(false)}>拒绝</button>
                    </div>
                  </div>
                )}

                {(agentTask.status === "done" || agentTask.status === "error") && (
                  <div className="agent-answer">
                    {agentTask.status === "error" ? (
                      <div className="err">任务失败：{agentTask.error}</div>
                    ) : (
                      <>
                        {agentTask.stopped_reason === "max_steps" && (
                          <div className="note">⚠ 已达步数上限，以下为基于现有信息的总结</div>
                        )}
                        {agentTask.stopped_reason === "token_budget" && (
                          <div className="note">⚠ 已达 token 预算，以下为基于现有信息的总结</div>
                        )}
                        <Markdown text={agentTask.answer || "（模型未返回内容）"} />
                      </>
                    )}
                    <div className="agent-meta hint">
                      工具调用 {steps.length} 步 · 约 {tokens} tokens
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {mode === "agent" && anchorId && (
          <div className="anchor-hint">⚓ 已锚定论文 {anchorId} —— 检索 / 入库 / 方向推荐类工具将被移除，防止任务漂移</div>
        )}

        <div className="chat-input">
          <input
            ref={inputRef}
            type="text"
            placeholder={mode === "agent" ? "描述一个任务，Agent 自主规划工具调用…" : "用自然语言说需求…"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && (mode === "agent" ? runAgent() : send())}
            autoFocus
          />
          <button
            className="primary"
            onClick={() => (mode === "agent" ? runAgent() : send())}
            disabled={(mode === "agent" ? agentBusy : busy) || !input.trim()}
          >
            {mode === "agent" ? "运行" : "发送"}
          </button>
        </div>
      </div>
    </div>
  );
}
