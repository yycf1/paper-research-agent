import { createPortal } from "react-dom";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Paper,
  type SearchReply,
  type StatusSummary,
  type TagsReply,
} from "../api";
import PaperDetail from "../components/PaperDetail";
import { STATUS_ORDER, STATUS_HELP, statusLabel } from "../labels";

const PAGE_SIZE = 200; // 后端单页上限；超出部分用「加载更多」追加
const SOURCE_LABELS: Record<string, string> = {
  openalex: "OpenAlex",
  europepmc: "Europe PMC",
  arxiv: "arXiv",
};

type FieldScope = "all" | "title" | "abstract";
type SortKey = "relevance" | "citations" | "newest";

const FIELD_LABELS: Record<FieldScope, string> = {
  all: "全部字段",
  title: "仅标题",
  abstract: "仅摘要",
};
const SORT_LABELS: Record<SortKey, string> = {
  relevance: "相关度",
  citations: "引用数",
  newest: "最新发表",
};

const ERR_LABELS: Record<string, string> = {
  "403": "源站拒绝访问",
  "404": "全文不存在",
  "401": "需要授权",
  "429": "请求过频",
  "500": "源站异常",
  "502": "源站异常",
  "503": "源站异常",
};

/** 表格行的错误摘要：原始报错（长 URL + 状态码）收敛为一句话，全文留在 title 提示。 */
function fmtError(raw: string): string {
  const m = raw.match(/->\s*HTTP\s+(\d{3})/i);
  if (m) return `${ERR_LABELS[m[1]] ?? "下载失败"}（${m[1]}）`;
  if (/timed?[\s-]?out|timeout/i.test(raw)) return "连接超时";
  if (/非 PDF|not a pdf|text\/html/i.test(raw)) return "返回内容不是 PDF";
  return raw.length > 64 ? `${raw.slice(0, 61)}…` : raw;
}

export default function Library({
  summary,
  detailId,
  onOpenDetail,
  onChanged,
  onStartAgent,
  view = "library",
  groupHost,
  onDiscover,
  onResearch,
}: {
  summary: StatusSummary | null;
  detailId: string | null;
  onOpenDetail: (id: string | null) => void;
  onChanged: () => void;
  onStartAgent: (task: string, anchor: string) => void;
  view?: "library" | "discover";
  groupHost?: HTMLElement | null;
  onDiscover?: () => void;
  onResearch?: (ids: string[], compare: boolean) => void;
}) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("");
  const [tag, setTag] = useState("");
  const [untaggedOnly, setUntaggedOnly] = useState(false);
  const [tagInfo, setTagInfo] = useState<TagsReply | null>(null);
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [loading, setLoading] = useState(false);

  // 检索预览
  const [searchQuery, setSearchQuery] = useState("");
  const [sources, setSources] = useState<string[]>(["openalex", "europepmc"]);
  const [searching, setSearching] = useState(false);
  const [preview, setPreview] = useState<SearchReply | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ingestTag, setIngestTag] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");

  // 高级检索（M13）：折叠面板，默认收起保持简单
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [author, setAuthor] = useState("");
  const [field, setField] = useState<FieldScope>("all");
  const [sortKey, setSortKey] = useState<SortKey>("relevance");
  const [minCitations, setMinCitations] = useState("");
  const [oaOnly, setOaOnly] = useState(false);

  // 分组管理（M13）
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  // 多选归类
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [bulkTarget, setBulkTarget] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [deleteTargets, setDeleteTargets] = useState<
    { id: string; title: string }[] | null
  >(null);
  const [deleteProgress, setDeleteProgress] = useState(0);
  const knownTitles = useRef(new Map<string, string>());

  const loadTags = useCallback(
    () =>
      api
        .tags()
        .then(setTagInfo)
        .catch(() => {}),
    [],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const data = await api.papers({
        status: status || undefined,
        tag: untaggedOnly ? undefined : tag || undefined,
        untagged: untaggedOnly || undefined,
        q: q || undefined,
        limit: PAGE_SIZE,
      });
      data.papers.forEach((p) => knownTitles.current.set(p.source_id, p.title));
      setPapers(data.papers);
      setTotal(data.total);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [status, tag, untaggedOnly, q]);

  useEffect(() => {
    void load();
    void loadTags();
  }, [load, loadTags, summary]);

  // 库内筛选防抖：每个按键一次请求在 200 条 ×  SQLite 上是浪费
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setQ(qInput.trim()), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [qInput]);

  const loadMore = async () => {
    setErr("");
    try {
      const data = await api.papers({
        status: status || undefined,
        tag: untaggedOnly ? undefined : tag || undefined,
        untagged: untaggedOnly || undefined,
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: papers.length,
      });
      data.papers.forEach((p) => knownTitles.current.set(p.source_id, p.title));
      setPapers((cur) => [...cur, ...data.papers]);
    } catch (e) {
      setErr(String(e));
    }
  };

  const toggleSource = (s: string) =>
    setSources((cur) =>
      cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s],
    );

  const doSearch = async () => {
    if (!searchQuery.trim() || sources.length === 0) return;
    setSearching(true);
    setErr("");
    setNotes([]);
    try {
      const out = await api.search(searchQuery.trim(), {
        sources,
        year_from: yearFrom ? Number(yearFrom) : null,
        year_to: yearTo ? Number(yearTo) : null,
        field,
        author: author.trim(),
        sort: sortKey,
        min_citations: minCitations ? Number(minCitations) : null,
        oa_only: oaOnly,
      });
      setPreview(out);
      setSelected(
        new Set(
          out.candidates.filter((c) => !c.duplicate_of).map((c) => c.source_id),
        ),
      );
      setIngestTag(searchQuery.trim());
      setNotes(out.notes);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSearching(false);
    }
  };

  const toggleCandidate = (sid: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });

  const doIngest = async () => {
    if (!preview) return;
    setIngesting(true);
    setErr("");
    try {
      const chosen = preview.candidates.filter((c) =>
        selected.has(c.source_id),
      );
      const out = await api.ingest(chosen, ingestTag.trim());
      setPreview(null);
      setNotes([
        `已新增 ${out.ingested} 条文献信息，尚未下载全文；${out.duplicates} 条与库内记录重复，仅合并信息。`,
      ]);
      // 总是刷新列表：目标分组与当前过滤相同时 setTag 不触发重渲染
      if (ingestTag.trim() && tag !== ingestTag.trim()) {
        setUntaggedOnly(false);
        setTag(ingestTag.trim());
      } else {
        await load();
      }
      onChanged();
      loadTags();
    } catch (e) {
      setErr(String(e));
    } finally {
      setIngesting(false);
    }
  };

  const doUpload = async (file: File) => {
    setUploading(true);
    setErr("");
    try {
      const out = await api.upload(file);
      setNotes([
        out.is_new
          ? `已上传：${out.paper.title}（可直接「一键处理」）`
          : `该文件已存在（幂等跳过）：${out.paper.title}`,
      ]);
      setTag("");
      setUntaggedOnly(false);
      await load();
      onChanged();
      loadTags();
    } catch (e) {
      setErr(String(e));
    } finally {
      setUploading(false);
    }
  };

  // ---- 分组管理（M13）----

  const doCreateGroup = async () => {
    const name = newGroupName.trim();
    if (!name) return;
    setErr("");
    try {
      await api.createTag(name);
      setCreatingGroup(false);
      setNewGroupName("");
      loadTags();
    } catch (e) {
      setErr(String(e));
    }
  };

  const doRenameGroup = async (oldName: string) => {
    const name = window.prompt("重命名分组（组内论文同步更新）：", oldName);
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed || trimmed === oldName) return;
    setErr("");
    try {
      await api.renameTag(oldName, trimmed);
      if (tag === oldName) setTag(trimmed);
      loadTags();
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  const doDeleteGroup = async (name: string, count: number) => {
    if (
      !window.confirm(
        `删除分组「${name}」？\n组内 ${count} 篇论文将回到未分组（论文本身不删）。`,
      )
    )
      return;
    setErr("");
    try {
      await api.deleteTag(name);
      if (tag === name) setTag("");
      loadTags();
      load();
    } catch (e) {
      setErr(String(e));
    }
  };

  // ---- 多选归类（M13）----

  const toggleChecked = (sid: string) =>
    setChecked((cur) => {
      const next = new Set(cur);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });

  const allVisibleChecked =
    papers.length > 0 && papers.every((p) => checked.has(p.source_id));

  const toggleAllVisible = () =>
    setChecked((cur) => {
      if (papers.every((p) => cur.has(p.source_id))) {
        const next = new Set(cur);
        papers.forEach((p) => next.delete(p.source_id));
        return next;
      }
      return new Set([...cur, ...papers.map((p) => p.source_id)]);
    });

  const applyBulk = async (target: string) => {
    if (checked.size === 0 || !target) return;
    setBulkBusy(true);
    setErr("");
    try {
      let name = target;
      if (target === "__new__") {
        const typed = window.prompt("新分组名称：");
        if (!typed || !typed.trim()) {
          setBulkTarget("");
          return;
        }
        name = typed.trim();
        await api.createTag(name);
      }
      const out = await api.batchTag([...checked], name);
      setNotes([`已把 ${out.updated} 篇移入分组「${name}」。`]);
      setChecked(new Set());
      setBulkTarget("");
      setUntaggedOnly(false);
      setTag(name);
      loadTags();
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBulkBusy(false);
    }
  };

  const removeBulk = async () => {
    if (checked.size === 0) return;
    setBulkBusy(true);
    setErr("");
    try {
      const out = await api.batchTag([...checked], null);
      setNotes([`已把 ${out.updated} 篇移出分组。`]);
      setChecked(new Set());
      setBulkTarget("");
      loadTags();
      load();
      onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBulkBusy(false);
    }
  };

  const deleteSelected = async () => {
    if (!deleteTargets || bulkBusy) return;
    const targets = [...deleteTargets];
    setBulkBusy(true);
    setDeleteProgress(0);
    setErr("");
    const failed = new Set<string>();
    const errors: string[] = [];
    for (const [index, target] of targets.entries()) {
      try {
        const result = await api.deletePaper(target.id);
        if (!result.ok) throw new Error("服务未确认删除成功");
      } catch (e) {
        failed.add(target.id);
        errors.push(`${target.title}：${e}`);
      }
      setDeleteProgress(index + 1);
    }
    setChecked(failed);
    setDeleteTargets(null);
    setBulkBusy(false);
    setNotes([
      `已删除 ${targets.length - failed.size} 篇${failed.size ? `；${failed.size} 篇未删除，已保留勾选，可重试。` : "。"}`,
    ]);
    await load();
    if (errors.length) setNotes((current) => [...current, ...errors]);
    void loadTags();
    onChanged();
  };

  const freshCount =
    preview?.candidates.filter((c) => !c.duplicate_of).length ?? 0;
  const groups = tagInfo?.tags ?? [];
  const groupPanel = (
    <div
      className="lib-groups"
      onKeyDown={(e) => {
        if (
          (e.key === "Enter" || e.key === " ") &&
          (e.target as HTMLElement).tagName === "SPAN"
        ) {
          e.preventDefault();
          (e.target as HTMLElement).click();
        }
      }}
    >
      <span className="hint row-label">分组</span>
      <div className="chips">
        <span
          role="button"
          tabIndex={0}
          className={`chip ${tag === "" && !untaggedOnly ? "on" : ""}`}
          onClick={() => {
            setTag("");
            setUntaggedOnly(false);
          }}
        >
          不限
        </span>
        {groups.slice(0, 12).map((g) => (
          <span
            key={g.name}
            role="button"
            tabIndex={0}
            className={`chip ${tag === g.name && !untaggedOnly ? "on" : ""}`}
            onClick={() => {
              setUntaggedOnly(false);
              setTag(g.name);
            }}
            title={g.name}
          >
            {g.name.length > 18 ? g.name.slice(0, 18) + "…" : g.name} {g.count}
            <span
              className="grp-act"
              title="重命名"
              onClick={(e) => {
                e.stopPropagation();
                doRenameGroup(g.name);
              }}
            >
              ✎
            </span>
            <span
              className="grp-act"
              title="删除分组（论文回未分组）"
              onClick={(e) => {
                e.stopPropagation();
                doDeleteGroup(g.name, g.count);
              }}
            >
              ✕
            </span>
          </span>
        ))}
        {(tagInfo?.untagged ?? 0) > 0 && (
          <span
            className={`chip ${untaggedOnly ? "on" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => {
              setTag("");
              setUntaggedOnly(true);
            }}
            title="检索全量入库等来源不明的论文"
          >
            未分组 {tagInfo!.untagged}
          </span>
        )}
        {creatingGroup ? (
          <span className="chip" onClick={(e) => e.stopPropagation()}>
            <input
              autoFocus
              type="text"
              placeholder="分组名"
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") doCreateGroup();
                if (e.key === "Escape") setCreatingGroup(false);
              }}
              style={{
                width: 110,
                border: "none",
                background: "transparent",
                outline: "none",
              }}
            />
            <span className="grp-act" title="确定" onClick={doCreateGroup}>
              ✓
            </span>
            <span
              className="grp-act"
              title="取消"
              onClick={() => setCreatingGroup(false)}
            >
              ✕
            </span>
          </span>
        ) : (
          <span
            className="chip chip-new"
            role="button"
            tabIndex={0}
            style={{ cursor: "pointer" }}
            onClick={() => setCreatingGroup(true)}
          >
            ＋ 新建分组
          </span>
        )}
      </div>
    </div>
  );

  return (
    <div
      className={`library ${view === "discover" ? "discovery-view" : "library-view"}${detailId ? " with-detail" : ""}`}
    >
      <div className="list-pane">
        <div className="workspace-heading">
          <div>
            <span className="eyebrow">
              {view === "library" ? "YOUR RESEARCH LIBRARY" : "DISCOVER PAPERS"}
            </span>
            <h1>{view === "library" ? "文献库" : "发现论文"}</h1>
            <p>
              {view === "library"
                ? "把论文整理成可以持续追问的研究资料。"
                : "检索公开学术来源，预览后保存文献信息，全文需另行获取。"}
            </p>
          </div>
          <div className="heading-actions">
            <label className="btn-outline upload-btn">
              {uploading ? "导入中…" : "导入文献"}
              <input
                type="file"
                accept=".pdf,.docx"
                hidden
                disabled={uploading}
                aria-label="导入 PDF 或 DOCX"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void doUpload(file);
                  e.currentTarget.value = "";
                }}
              />
            </label>
            {view === "library" && (
              <button className="primary" onClick={onDiscover}>
                ＋ 发现论文
              </button>
            )}
          </div>
        </div>
        {view === "discover" && (
          <div className="discovery-guide">
            <span>01 搜索主题</span>
            <span>02 预览候选</span>
            <span>03 保存文献信息</span>
          </div>
        )}
        {view === "discover" && (
          <div className="lib-head">
            <div className="lib-row main">
              <input
                type="text"
                className="lib-search"
                placeholder="输入主题检索论文，如「图神经网络 推荐系统」…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doSearch()}
              />
              <button
                className="primary"
                disabled={
                  searching || !searchQuery.trim() || sources.length === 0
                }
                onClick={doSearch}
              >
                {searching ? "检索中…" : "检索"}
              </button>
            </div>
            <div className="lib-row sub">
              <span className="hint row-label">来源</span>
              <div className="chips">
                {Object.entries(SOURCE_LABELS).map(([key, label]) => (
                  <span
                    key={key}
                    className={`chip ${sources.includes(key) ? "on" : ""}`}
                    onClick={() => toggleSource(key)}
                  >
                    {label}
                  </span>
                ))}
              </div>
              <span className="flex-spacer" />
              <button
                className="ghost sm"
                onClick={() => setShowAdvanced((v) => !v)}
                title="作者、标题/摘要限定、排序、引用数与全文过滤"
              >
                高级筛选 {showAdvanced ? "▲" : "▼"}
              </button>
            </div>
            {showAdvanced && (
              <div className="adv-grid">
                <label className="adv-field">
                  <span>作者</span>
                  <input
                    type="text"
                    placeholder="如 Yann LeCun"
                    value={author}
                    onChange={(e) => setAuthor(e.target.value)}
                  />
                </label>
                <label className="adv-field">
                  <span>匹配字段</span>
                  <select
                    value={field}
                    onChange={(e) => setField(e.target.value as FieldScope)}
                  >
                    {(Object.keys(FIELD_LABELS) as FieldScope[]).map((k) => (
                      <option key={k} value={k}>
                        {FIELD_LABELS[k]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="adv-field">
                  <span>排序</span>
                  <select
                    value={sortKey}
                    onChange={(e) => setSortKey(e.target.value as SortKey)}
                  >
                    {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
                      <option key={k} value={k}>
                        {SORT_LABELS[k]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="adv-field">
                  <span>最低被引</span>
                  <input
                    type="number"
                    placeholder="0"
                    min={0}
                    value={minCitations}
                    onChange={(e) => setMinCitations(e.target.value)}
                  />
                </label>
                <label className="adv-field">
                  <span>发表年份</span>
                  <div className="adv-year">
                    <input
                      type="number"
                      placeholder="从"
                      min={1900}
                      max={2100}
                      value={yearFrom}
                      onChange={(e) => setYearFrom(e.target.value)}
                    />
                    <i>–</i>
                    <input
                      type="number"
                      placeholder="到"
                      min={1900}
                      max={2100}
                      value={yearTo}
                      onChange={(e) => setYearTo(e.target.value)}
                    />
                  </div>
                </label>
                <label className="adv-check">
                  <input
                    type="checkbox"
                    checked={oaOnly}
                    onChange={(e) => setOaOnly(e.target.checked)}
                  />
                  仅有全文候选链接
                </label>
              </div>
            )}
          </div>
        )}
        <div className="lib-filter">
          <div className="library-filter-row">
            <input
              type="text"
              className="lib-q"
              placeholder="库内筛选：标题 / 作者 / DOI…"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
            />
            <label className="library-group-select">
              分组{" "}
              <select
                aria-label="筛选文献分组"
                value={untaggedOnly ? "__untagged__" : tag}
                onChange={(e) => {
                  setUntaggedOnly(e.target.value === "__untagged__");
                  setTag(
                    e.target.value === "__untagged__" ? "" : e.target.value,
                  );
                }}
              >
                <option value="">全部分组</option>
                {groups.map((g) => (
                  <option key={g.name} value={g.name}>
                    {g.name}（{g.count}）
                  </option>
                ))}
                <option value="__untagged__">未分组</option>
              </select>
            </label>
          </div>
          <span className="hint filter-label">
            处理阶段（全库数量，非分组）
          </span>
          <div className="seg" aria-label="按处理阶段筛选">
            <button
              className={`seg-item ${status === "" ? "on" : ""}`}
              onClick={() => setStatus("")}
            >
              全部 {summary?.total ?? ""}
            </button>
            {STATUS_ORDER.filter((s) => summary?.counts[s]).map((s) => (
              <button
                key={s}
                title={STATUS_HELP[s]}
                className={`seg-item ${status === s ? "on" : ""}`}
                onClick={() => setStatus(s)}
              >
                {statusLabel(s)} {summary?.counts[s]}
              </button>
            ))}
          </div>
          <p className="stage-explanation">
            {status
              ? STATUS_HELP[status]
              : "文献库既保存待读论文的题录信息，也保存已处理全文；保存信息不等于下载。阶段记录最后完成的步骤，全文列单独显示文件是否已在本地。"}
          </p>
        </div>
        {view === "library" &&
          (groupHost ? createPortal(groupPanel, groupHost) : groupPanel)}
        {checked.size > 0 && (
          <div className="lib-bulk">
            <span className="bulk-count">已选 {checked.size} 篇</span>
            <button
              className="primary"
              disabled={bulkBusy || checked.size < 2 || checked.size > 6}
              onClick={() => onResearch?.([...checked], true)}
            >
              比较所选论文
            </button>
            <button
              disabled={bulkBusy || checked.size > 6}
              onClick={() => onResearch?.([...checked], false)}
            >
              向所选论文提问
            </button>
            {checked.size > 6 && (
              <span className="hint">每次研究最多选择 6 篇</span>
            )}
            <select
              value={bulkTarget}
              onChange={(e) => applyBulk(e.target.value)}
              disabled={bulkBusy}
            >
              <option value="">移动到分组…</option>
              {groups.map((g) => (
                <option key={g.name} value={g.name}>
                  {g.name}
                </option>
              ))}
              <option value="__new__">＋ 新建分组…</option>
            </select>
            <button
              className="ghost sm"
              onClick={removeBulk}
              disabled={bulkBusy}
            >
              移出分组
            </button>
            <button
              className="danger sm"
              disabled={bulkBusy}
              onClick={() =>
                setDeleteTargets(
                  [...checked].map((id) => ({
                    id,
                    title: knownTitles.current.get(id) ?? id,
                  })),
                )
              }
            >
              删除所选论文
            </button>
            <button
              className="ghost sm"
              disabled={bulkBusy}
              onClick={() => setChecked(new Set())}
            >
              清空选择
            </button>
          </div>
        )}
        {(notes.length > 0 || err) && (
          <div className="lib-notes">
            {err && <div className="err">⚠ {err}</div>}
            {notes.map((n, i) => (
              <div key={i} className="hint">
                · {n}
              </div>
            ))}
          </div>
        )}
        <div className="table-wrap">
          {loading ? (
            <div className="loading">
              <span className="spin" />
              加载中…
            </div>
          ) : papers.length === 0 ? (
            <div className="empty-state">
              <div className="es-icon">📚</div>
              <div className="es-title">
                {q || status
                  ? "没有符合筛选条件的论文"
                  : untaggedOnly
                    ? "没有未分组的论文"
                    : tag
                      ? "该分组下暂无论文"
                      : "文献库还是空的"}
              </div>
              <div className="es-sub">
                {q || status || tag || untaggedOnly
                  ? "当前筛选没有匹配的论文，试着放宽条件。"
                  : "导入 PDF/DOCX，或到「发现论文」选择资料；进入论文后开始处理与精读。"}
              </div>
            </div>
          ) : (
            <table className="papers">
              <thead>
                <tr>
                  <th style={{ width: 34 }}>
                    <input
                      type="checkbox"
                      disabled={bulkBusy}
                      checked={allVisibleChecked}
                      onChange={toggleAllVisible}
                      title="全选本页"
                    />
                  </th>
                  <th>论文与研究主题（{total} 篇）</th>
                  <th style={{ width: 112 }}>处理阶段</th>
                  <th style={{ width: 56 }}>年份</th>
                  <th style={{ width: 56 }}>被引</th>
                  <th style={{ width: 116 }}>全文文件</th>
                </tr>
              </thead>
              <tbody>
                {papers.map((p) => (
                  <tr
                    key={p.source_id}
                    className={`row ${detailId === p.source_id ? "sel" : ""}`}
                    onClick={() => onOpenDetail(p.source_id)}
                  >
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`选择 ${p.title}`}
                        disabled={bulkBusy}
                        checked={checked.has(p.source_id)}
                        onChange={() => toggleChecked(p.source_id)}
                      />
                    </td>
                    <td className="title-cell">
                      <button
                        className="paper-title"
                        onClick={(e) => {
                          e.stopPropagation();
                          onOpenDetail(p.source_id);
                        }}
                      >
                        {p.title}
                      </button>
                      <div className="paper-authors hint">
                        {p.authors.slice(0, 3).join(" · ") || p.source}
                      </div>
                      {p.tag && (
                        <div className="hint paper-tagline">分组：{p.tag}</div>
                      )}
                      {p.error && (
                        <div className="paper-err" title={p.error}>
                          ⚠ {fmtError(p.error)}
                        </div>
                      )}
                    </td>
                    <td>
                      <span
                        className={`tag status-${p.status}`}
                        title={STATUS_HELP[p.status]}
                      >
                        {statusLabel(p.status)}
                      </span>
                    </td>
                    <td className="num">{p.year ?? "—"}</td>
                    <td className="num">{p.citations ?? "—"}</td>
                    <td
                      className="file-state"
                      title={
                        p.has_local_file
                          ? "全文文件已保存在本地"
                          : p.has_pdf
                            ? "来源提供下载链接，但文件尚未保存到本地"
                            : "尚无本地全文，可尝试获取全文或导入文件"
                      }
                    >
                      <span>{p.has_local_file ? "已保存" : "未保存"}</span>
                      {!p.has_local_file && (
                        <small>
                          {p.has_pdf
                            ? "有来源链接"
                            : p.abstract_only
                              ? "仅题录/摘要"
                              : "可导入文件"}
                        </small>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!loading && papers.length < total && (
            <div className="load-more">
              <button className="ghost sm" onClick={loadMore}>
                加载更多（已显示 {papers.length} / {total} 篇）
              </button>
            </div>
          )}
        </div>
      </div>
      {view === "library" &&
        (detailId ? (
          <PaperDetail
            key={detailId}
            id={detailId}
            onClose={() => onOpenDetail(null)}
            onStartAgent={onStartAgent}
            onChanged={() => {
              onChanged();
              load();
              loadTags();
            }}
          />
        ) : (
          <div className="detail-pane detail-empty">
            <div className="es-icon">🗂</div>
            <div className="es-sub">
              点击左侧论文，查看详情、精读报告与实验结构
            </div>
          </div>
        ))}

      {deleteTargets && (
        <div className="modal-mask">
          <section
            className="modal delete-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-title"
            onKeyDown={(e) => {
              if (e.key === "Escape" && !bulkBusy) setDeleteTargets(null);
            }}
          >
            <div className="modal-head">
              <h2 id="delete-title">
                删除所选 {deleteTargets.length} 篇论文？
              </h2>
            </div>
            <div className="modal-body">
              <p>
                将从文献库移除记录，并清理对应的本地全文、解析文本、报告与索引。这不是移出分组，删除后无法撤销。
              </p>
              <p className="hint">
                下面列出本次实际删除范围，包含切换筛选前勾选的论文。
              </p>
              <ul>
                {deleteTargets.map((p) => (
                  <li key={p.id}>{p.title}</li>
                ))}
              </ul>
            </div>
            <div className="modal-foot">
              <button
                autoFocus
                disabled={bulkBusy}
                onClick={() => setDeleteTargets(null)}
              >
                取消
              </button>
              <button
                className="danger"
                disabled={bulkBusy}
                onClick={() => void deleteSelected()}
              >
                {bulkBusy
                  ? `删除中 ${deleteProgress}/${deleteTargets.length}`
                  : "确认删除"}
              </button>
            </div>
          </section>
        </div>
      )}

      {preview && (
        <div
          className={view === "discover" ? "discovery-results" : "modal-mask"}
          onClick={() => setPreview(null)}
        >
          <div
            className={
              view === "discover"
                ? "surface-card discovery-result-card"
                : "modal"
            }
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <div>
                <div className="modal-title">
                  检索到 {preview.candidates.length} 条候选（新 {freshCount}{" "}
                  条，库内已有 {preview.candidates.length - freshCount} 条）
                </div>
                <div className="hint">
                  主题：{preview.topics.join(" / ")}
                  {preview.optimized ? "（已自动优化）" : ""} · 各源候选：
                  {JSON.stringify(preview.source_hits)}
                </div>
              </div>
              <span className="modal-x" onClick={() => setPreview(null)}>
                ✕
              </span>
            </div>
            <div className="modal-body">
              {preview.candidates.map((c) => (
                <label
                  key={c.source_id}
                  className={`cand ${c.duplicate_of ? "dup" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selected.has(c.source_id)}
                    disabled={!!c.duplicate_of}
                    onChange={() => toggleCandidate(c.source_id)}
                  />
                  <span className="cand-title">
                    {c.title}
                    <span className="hint">
                      {" "}
                      · {c.source} · {c.year ?? "-"} · 被引 {c.citations ?? "-"}{" "}
                      · {c.has_pdf ? "提供全文链接（未下载）" : "无全文链接"}
                    </span>
                    {c.duplicate_of && (
                      <span className="tag" style={{ marginLeft: 6 }}>
                        库内已有
                      </span>
                    )}
                  </span>
                </label>
              ))}
            </div>
            <div className="modal-foot">
              <span
                className="chip"
                onClick={() =>
                  setSelected(
                    new Set(
                      preview.candidates
                        .filter((c) => !c.duplicate_of)
                        .map((c) => c.source_id),
                    ),
                  )
                }
              >
                全选新的
              </span>
              <input
                type="text"
                placeholder="收入分组（如：知识图谱调研）"
                value={ingestTag}
                onChange={(e) => setIngestTag(e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                className="primary"
                disabled={ingesting || selected.size === 0}
                onClick={doIngest}
              >
                {ingesting ? "入库中…" : `保存所选信息（${selected.size}）`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
