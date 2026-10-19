/** 后端 /api 的类型定义与请求封装。 */

export interface Paper {
  source_id: string;
  source: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  citations: number | null;
  has_pdf: boolean;
  /** 已保存的本地 PDF/DOCX；has_pdf 仅表示来源提供下载链接。 */
  has_local_file?: boolean;
  local_file_type?: "pdf" | "docx" | null;
  landing_page: string | null;
  abstract_only?: boolean;
  status: string;
  error: string | null;
  added_at: string;
  tag: string;
  abstract?: string;
}

export interface PaperDetail extends Paper {
  abstract: string;
  experiment: ExperimentSchema | null;
  report: string | null;
  repro_plan: string | null;
  full_text: string | null;
  index_state?: { status: "ready" | "stale" | "legacy" | "unindexed" | "unknown"; chunk_count: number | null; can_ask: boolean; mode?: string };
  knowledge_current?: boolean;
}

/** extraction/normalize_experiment 的 schema（M2），字段值宽松处理。 */
export interface ExperimentSchema {
  research_goal: string;
  method: string;
  datasets: string[];
  hyperparameters: Record<string, string>;
  experiment_steps: string[];
  metrics: string[];
  main_results: string[];
  conclusions: string;
  confidence: Record<string, string>;
  missing_fields?: string[];
  token_usage?: { prompt: number; completion: number };
  [k: string]: unknown;
}

export interface StatusSummary {
  total: number;
  counts: Record<string, number>;
  vector_chunks: number;
  version: string;
}

export interface SearchReply {
  candidates: (Paper & { abstract: string; duplicate_of: string | null })[];
  total_hits: number;
  notes: string[];
  source_hits: Record<string, number>;
  topics: string[];
  optimized: boolean;
}

export interface IngestReply {
  ingested: number;
  duplicates: number;
  papers: Paper[];
  download_job?: Job | null;
}

export interface DownloadItem {
  source_id: string;
  title: string;
  status: "pending" | "running" | "saved" | "already_saved" | "no_link" | "failed" | "missing" | "interrupted";
  detail: string;
  failure_kind?: string;
  action?: string;
  attempts?: string[];
}

export interface TagWithCount {
  name: string;
  count: number;
}

export interface TagsReply {
  tags: TagWithCount[];
  sources: Record<string, number>;
  untagged: number;
}

/** 高级检索条件（M13）：全部可选，缺省走后端默认。 */
export interface SearchOptions {
  year_from?: number | null;
  year_to?: number | null;
  sources?: string[];
  max_results?: number;
  field?: "all" | "title" | "abstract";
  author?: string;
  sort?: "relevance" | "citations" | "newest";
  min_citations?: number | null;
  oa_only?: boolean;
}

export interface SourceRef {
  n: number[];
  paper_id: string;
  title: string;
  section: string;
  page: number;
  chunks?: { n: number; chunk_id: string; page: number; text: string }[];
}

export interface AskReply {
  answer: string;
  sources: SourceRef[];
  answer_status?: string;
  citation_check?: { status: string; semantic_support: string } | null;
  telemetry?: CallTelemetry;
  retrieval_notes?: string[];
}

export interface ComparisonReply {
  papers: { paper_id: string; title: string; field_evidence: BoundFields }[];
  note?: string;
  comparability?: { pairs: { paper_ids: string[]; metrics: { left_metric: string | null; right_metric: string | null; missing_conditions: string[]; different_conditions: string[] }[] }[] };
}

export interface ChatReply {
  session_id: string;
  intent: string;
  reply: string;
  understanding: string;
  notes: string[];
  new_papers: Paper[];
  sources?: SourceRef[];
  routing?: { intent: string; via: string; fallback_reason?: string; catalogue?: { operation: string; query: string } };
  telemetry?: CallTelemetry;
  answer_status?: string;
  paper_ids?: string[] | null;
  comparison?: ComparisonReply | null;
}

export interface ChatSession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  intent: string | null;
  understanding: string | null;
  notes: string[];
  sources?: SourceRef[] | null;
  details?: { paper_ids?: string[] | null; action?: string; comparison?: ComparisonReply | null;
    task_id?: string; telemetry?: CallTelemetry; answer_status?: string };
  created_at: string;
}

export interface Job {
  id: string;
  kind: string;
  status: "running" | "done" | "error" | "interrupted";
  result: Record<string, unknown>;
  error: string;
}

/** 运行时设置（M17/M20）：Key 永远只回脱敏形式（尾 4 位）。 */
export interface SettingsView {
  base_url: string;
  model: string;
  api_key_masked: string;
  has_key: boolean;
  theme: "dark" | "light";
}

/** Agent 任务的一步工具调用（后端 AgentStep 的序列化）。 */
export interface AgentStepView {
  step: number;
  tool: string;
  arguments: Record<string, unknown>;
  status: "ok" | "degraded" | "retryable_error" | "fatal";
  detail: string;
  duration_ms: number;
  comparison?: { papers: { paper_id: string; title: string; field_evidence: BoundFields }[] } | null;
  observation?: { sources?: SourceRef[]; selected_ids?: string[]; answer_status?: string; declined?: boolean } | null;
}

export interface TaskVerification {
  status: "passed" | "failed" | "partial" | "unverified";
  execution_finished?: boolean;
  checks: { kind: string; passed: boolean | null; detail: string }[];
  note?: string;
  added_ids?: string[] | null;
}
export interface CallTelemetry {
  call_count: number;
  known_usage_calls: number;
  unknown_usage_calls: number;
  usage_complete: boolean;
  prompt: number;
  completion: number;
  embedding_tokens: number;
  rerank_tokens: number;
  cost_note: string;
  calls: { id: number; kind: string; stage: string; model: string; status: string;
    elapsed_ms: number; usage_known: boolean; usage: { prompt?: number; completion?: number; total?: number };
    error_type?: string; units?: Record<string, number> }[];
}

export interface FieldFact {
  field: "research_goal" | "datasets" | "metrics";
  raw_value: unknown;
  normalized_value: string | Record<string, string | number | null> | null;
  citation_status: "valid" | "missing" | "invalid";
  support_status: "unknown" | "unverified" | "supported" | "unsupported" | "conflict" | "not_applicable";
  claim_sha256: string;
  issues: string[];
  evidence: { paper_id: string; chunk_id: string; section: string; page: number | null; quote: string }[];
  review: { reviewer: string; review_kind: string; note: string } | null;
}

export interface BoundFields {
  paper_id: string;
  source_version: string;
  fields: Record<FieldFact["field"], FieldFact[]>;
  omitted: Partial<Record<FieldFact["field"], number>>;
  unmatched_reviews: number;
  load_issues: string[];
  numeric_comparison: { ranking_allowed: boolean; reason: string };
  title?: string;
}

/** Agent 任务状态：轮询 /agent/tasks/{id} 增量可见。 */
export interface AgentTaskState {
  id: string;
  task: string;
  status: "running" | "waiting_confirm" | "done" | "error";
  steps?: AgentStepView[];
  step_count: number;
  pending_confirm: { count: number; titles: string[] } | null;
  answer: string;
  stopped_reason: "final" | "max_steps" | "token_budget" | "error";
  usage: { prompt: number; completion: number };
  error: string;
  allowed_paper_ids?: string[] | null;
  anchor?: string;
  verification?: TaskVerification;
  telemetry?: CallTelemetry;
  duration_ms?: number;
  provenance?: string;
}

/** 任务历史条目（M23）：live=注册表内存态；否则来自磁盘轨迹回读。 */
export interface AgentTaskBrief {
  id: string;
  task: string;
  status: AgentTaskState["status"];
  step_count: number;
  answer: string;
  stopped_reason: string;
  usage: { prompt: number; completion: number };
  error: string;
  live: boolean;
  ts: number;
  verification?: TaskVerification;
}

/** 复现产物（M24）：方案 + 工作区文件 + run_code 运行记录。 */
export interface ReproFile {
  name: string;
  bytes: number;
  mtime: number;
}

export interface ReproRun {
  ts: number;
  filename: string;
  exit_code: number | null;
  timed_out: boolean;
  duration_ms?: number;
  stdout?: string;
  stderr?: string;
}

export interface ReproView {
  source_id: string;
  plan: string | null;
  files: ReproFile[];
  runs: ReproRun[];
}

const BASE = ""; // 开发模式经 Vite 代理转发到 pa serve；生产由 FastAPI 同端口托管

async function req<T>(path: string, init?: RequestInit, timeoutMs = 120_000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    const headers: Record<string, string> = {};
    if (!(init?.body instanceof FormData)) headers["Content-Type"] = "application/json";
    res = await fetch(`${BASE}/api${path}`, { headers, ...init, signal: ctrl.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error("请求超时（LLM 平台响应慢或数据源限流），请稍后重试");
    }
    throw new Error("网络错误：服务是否已启动（pa serve）？");
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* 非 JSON 响应 */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => req<StatusSummary>("/status"),
  papers: (params: { status?: string; tag?: string; untagged?: boolean; q?: string; limit?: number; offset?: number }) => {
    const usp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && v !== false && usp.set(k, String(v)));
    return req<{ total: number; papers: Paper[] }>(`/papers?${usp}`);
  },
  paper: (id: string) => req<PaperDetail>(`/papers/${encodeURIComponent(id)}`),
  paperFacts: (id: string) => req<BoundFields>(`/papers/${encodeURIComponent(id)}/facts`),
  reviewFact: (id: string, source_version: string, claim_sha256: string, status: string, note: string) =>
    req<BoundFields>(`/papers/${encodeURIComponent(id)}/facts/reviews`, { method: "POST", body: JSON.stringify({
      source_version, claim_sha256, status, note, reviewer: "本人", review_kind: "human",
    }) }),
  deletePaper: (id: string) =>
    req<{ ok: boolean; removed?: boolean; cleanup_pending?: boolean; detail?: string }>(`/papers/${encodeURIComponent(id)}`, { method: "DELETE" }),
  download: (source_ids: string[]) => req<Job>("/papers/download", {
    method: "POST", body: JSON.stringify({ source_ids }),
  }),
  retryDownload: (id: string) => req<Job>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  pendingCleanup: () => req<{ items: { source_id: string; error: string }[] }>("/maintenance/cleanup"),
  retryCleanup: () => req<{ remaining: number }>("/maintenance/cleanup", { method: "POST" }),
  originalFile: (id: string, page?: number) => `/api/papers/${encodeURIComponent(id)}/file${page ? `#page=${page}` : ""}`,
  attachFulltext: (id: string, file: File, replaceExisting = false) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("replace_existing", String(replaceExisting));
    return req<{ paper: Paper }>(`/papers/${encodeURIComponent(id)}/fulltext`, { method: "POST", body: fd }, 300_000);
  },
  tags: () => req<TagsReply>("/tags"),
  createTag: (name: string) =>
    req<TagWithCount>("/tags", { method: "POST", body: JSON.stringify({ name }) }),
  renameTag: (oldName: string, name: string) =>
    req<TagWithCount>(`/tags/${encodeURIComponent(oldName)}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteTag: (name: string) =>
    req<{ ok: boolean; ungrouped: number }>(`/tags/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  setPaperTag: (id: string, tag: string | null) =>
    req<Paper>(`/papers/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ tag }),
    }),
  batchTag: (sourceIds: string[], tag: string | null) =>
    req<{ updated: number; missing: string[]; tag: string }>("/papers/batch-tag", {
      method: "POST",
      body: JSON.stringify({ source_ids: sourceIds, tag }),
    }),
  search: (query: string, opts: SearchOptions = {}) => {
    const {
      sources = ["openalex", "europepmc", "arxiv"],
      max_results = 10,
      ...rest
    } = opts;
    return req<SearchReply>("/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        sources: sources.join(","),
        max_results,
        ...rest,
        year_from: rest.year_from ?? undefined,
        year_to: rest.year_to ?? undefined,
        author: rest.author || undefined,
        min_citations: rest.min_citations ?? undefined,
      }),
    });
  },
  ingest: (
    papers: { source: string; source_id: string; title: string; abstract?: string }[],
    tag: string,
    download_fulltext = false,
  ) =>
    req<IngestReply>("/papers/ingest", {
      method: "POST",
      body: JSON.stringify({ papers, tag, download_fulltext }),
    }),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ is_new: boolean; paper: Paper }>("/papers/upload", { method: "POST", body: fd }, 300_000);
  },
  ask: (question: string, paper_id?: string, paper_ids?: string[]) =>
    req<AskReply>("/ask", { method: "POST", body: JSON.stringify({ question, paper_id, paper_ids }) }),
  compare: (topic: string, paper_ids: string[]) =>
    req<ComparisonReply>("/compare", { method: "POST", body: JSON.stringify({ topic, paper_ids }) }),
  chat: (message: string, session_id?: string | null, paper_ids?: string[] | null, action: "chat" | "compare" = "chat") =>
    req<ChatReply>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: session_id ?? undefined, paper_ids, action }),
    }),
  sessions: () => req<{ sessions: ChatSession[] }>("/sessions"),
  createSession: (title = "新对话") =>
    req<ChatSession>("/sessions", { method: "POST", body: JSON.stringify({ title }) }),
  sessionMessages: (sid: string) =>
    req<{ session_id: string; paper_ids?: string[] | null; messages: ChatMessage[] }>(`/sessions/${sid}/messages`),
  deleteSession: (sid: string) =>
    req<{ ok: boolean }>(`/sessions/${sid}`, { method: "DELETE" }),
  process: (id: string) =>
    req<Job>(`/papers/${encodeURIComponent(id)}/process`, { method: "POST" }),
  plan: (id: string) =>
    req<{ source_id: string; repro_plan: string; tokens: number }>(
      `/papers/${encodeURIComponent(id)}/plan`,
      { method: "POST" },
      180_000,
    ),
  paperRepro: (id: string) =>
    req<ReproView>(`/papers/${encodeURIComponent(id)}/repro`),
  paperReproFile: (id: string, name: string) =>
    req<{ name: string; content: string }>(
      `/papers/${encodeURIComponent(id)}/repro/file?name=${encodeURIComponent(name)}`,
    ),
  run: (query: string) =>
    req<Job>("/jobs/run", { method: "POST", body: JSON.stringify({ query }) }),
  job: (id: string) => req<Job>(`/jobs/${id}`),
  jobs: () => req<{ jobs: Job[] }>("/jobs"),
  agentSubmit: (task: string, maxSteps?: number, anchorSourceId?: string, allowedPaperIds?: string[] | null, acceptance?: Record<string, unknown>[], sessionId?: string) =>
    req<AgentTaskState>("/agent/tasks", {
      method: "POST",
      body: JSON.stringify({
        task,
        max_steps: maxSteps ?? undefined,
        anchor_source_id: anchorSourceId?.trim() || undefined,
        allowed_paper_ids: allowedPaperIds,
        acceptance,
        session_id: sessionId,
      }),
    }),
  agentTask: (id: string) => req<AgentTaskState>(`/agent/tasks/${encodeURIComponent(id)}`),
  agentTasks: () => req<{ tasks: AgentTaskBrief[] }>("/agent/tasks"),
  agentDemos: () => req<{ demos: AgentTaskState[] }>("/agent/demos"),
  agentConfirm: (id: string, approve: boolean) =>
    req<{ ok: boolean }>(`/agent/tasks/${encodeURIComponent(id)}/confirm`, {
      method: "POST",
      body: JSON.stringify({ approve }),
    }),
  settings: () => req<SettingsView>("/settings"),
  saveSettings: (p: { base_url: string; model: string; api_key?: string | null; theme?: "dark" | "light" }) =>
    req<{ ok: boolean; api_key_masked: string; has_key: boolean }>("/settings", {
      method: "PUT",
      body: JSON.stringify(p),
    }),
  testSettings: (p: { base_url?: string; model?: string; api_key?: string | null }) =>
    req<{ ok: boolean; message: string }>("/settings/test", {
      method: "POST",
      body: JSON.stringify(p),
    }, 60_000),
};

/** 轮询任务直至 done/error（或超时）。 */
export async function pollJob(id: string, onTick?: (j: Job) => void, timeoutMs = 600_000) {
  const start = Date.now();
  for (;;) {
    const job = await api.job(id);
    onTick?.(job);
    if (job.status !== "running") return job;
    if (Date.now() - start > timeoutMs) throw new Error("任务超时");
    await new Promise((r) => setTimeout(r, 1500));
  }
}
