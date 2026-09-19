import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Paper, type SearchReply, type StatusSummary, type TagsReply } from "../api";
import PaperDetail from "../components/PaperDetail";
import { STATUS_ORDER, statusLabel } from "../labels";

const PAGE_SIZE = 200; // 后端单页上限；超出部分用「加载更多」追加
const SOURCE_LABELS: Record<string, string> = {
  openalex: "OpenAlex",
  europepmc: "Europe PMC",
  arxiv: "arXiv（常限流）",
};

export default function Library({
  summary,
  detailId,
  onOpenDetail,
  onChanged,
}: {
  summary: StatusSummary | null;
  detailId: string | null;
  onOpenDetail: (id: string | null) => void;
  onChanged: () => void;
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
  const [searchYear, setSearchYear] = useState("");
  const [sources, setSources] = useState<string[]>(["openalex", "europepmc"]);
  const [searching, setSearching] = useState(false);
  const [preview, setPreview] = useState<SearchReply | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [ingestTag, setIngestTag] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");

  const loadTags = useCallback(
    () => api.tags().then(setTagInfo).catch(() => {}),
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
      setPapers(data.papers);
      setTotal(data.total);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [status, tag, untaggedOnly, q]);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    loadTags();
  }, [loadTags]);

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
      setPapers((cur) => [...cur, ...data.papers]);
    } catch (e) {
      setErr(String(e));
    }
  };

  const toggleSource = (s: string) =>
    setSources((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  const doSearch = async () => {
    if (!searchQuery.trim() || sources.length === 0) return;
    setSearching(true);
    setErr("");
    setNotes([]);
    try {
      const year = searchYear ? Number(searchYear) : null;
      const out = await api.search(searchQuery.trim(), year, sources);
      setPreview(out);
      setSelected(new Set(out.candidates.filter((c) => !c.duplicate_of).map((c) => c.source_id)));
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
      const chosen = preview.candidates.filter((c) => selected.has(c.source_id));
      const out = await api.ingest(chosen, ingestTag.trim());
      setPreview(null);
      setNotes([`已入库 ${out.ingested} 篇（${out.duplicates} 篇与库内已有重复，仅合并信息）。`]);
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
      setNotes([out.is_new ? `已上传：${out.paper.title}（可直接「一键处理」）` : `该文件已存在（幂等跳过）：${out.paper.title}`]);
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

  const freshCount = preview?.candidates.filter((c) => !c.duplicate_of).length ?? 0;

  return (
    <div className="library">
      <div className="list-pane">
        <div className="toolbar">
          <input
            type="text"
            placeholder="检索论文（支持中文，入库前可预览筛选）…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
          />
          <input
            type="number"
            placeholder="年份 ≥"
            min={1900}
            max={2100}
            style={{ width: 84 }}
            value={searchYear}
            onChange={(e) => setSearchYear(e.target.value)}
          />
          <button className="primary" disabled={searching || !searchQuery.trim() || sources.length === 0} onClick={doSearch}>
            {searching ? "检索中…" : "检索"}
          </button>
          <label className="chip upload-btn">
            {uploading ? "上传中…" : "⬆ 上传 PDF/DOCX"}
            <input
              type="file"
              accept=".pdf,.docx"
              hidden
              disabled={uploading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) doUpload(f);
                e.currentTarget.value = "";
              }}
            />
          </label>
        </div>
        <div className="toolbar" style={{ borderTop: "none", paddingTop: 0 }}>
          <span className="hint" style={{ whiteSpace: "nowrap" }}>检索源：</span>
          {Object.entries(SOURCE_LABELS).map(([key, label]) => (
            <span key={key} className={`chip ${sources.includes(key) ? "on" : ""}`} onClick={() => toggleSource(key)}>
              {label}
            </span>
          ))}
        </div>
        <div className="toolbar" style={{ borderTop: "none", paddingTop: 0 }}>
          <input
            type="text"
            placeholder="在库内筛选：标题 / 作者 / DOI…"
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            style={{ maxWidth: 260 }}
          />
          <div className="chips">
            <span
              className={`chip ${status === "" ? "on" : ""}`}
              onClick={() => setStatus("")}
            >
              全部 {summary?.total ?? ""}
            </span>
            {STATUS_ORDER.filter((s) => summary?.counts[s]).map((s) => (
              <span key={s} className={`chip ${status === s ? "on" : ""}`} onClick={() => setStatus(s)}>
                {statusLabel(s)} {summary?.counts[s]}
              </span>
            ))}
          </div>
        </div>
        {(Object.keys(tagInfo?.tags ?? {}).length > 0 || (tagInfo?.untagged ?? 0) > 0) && (
          <div className="toolbar" style={{ borderTop: "none", paddingTop: 0 }}>
            <span className="hint" style={{ whiteSpace: "nowrap" }}>分组：</span>
            <div className="chips">
              <span
                className={`chip ${tag === "" && !untaggedOnly ? "on" : ""}`}
                onClick={() => {
                  setTag("");
                  setUntaggedOnly(false);
                }}
              >
                不限
              </span>
              {Object.entries(tagInfo!.tags).slice(0, 12).map(([t, n]) => (
                <span
                  key={t}
                  className={`chip ${tag === t && !untaggedOnly ? "on" : ""}`}
                  onClick={() => {
                    setUntaggedOnly(false);
                    setTag(t);
                  }}
                  title={t}
                >
                  {t.length > 18 ? t.slice(0, 18) + "…" : t} {n}
                </span>
              ))}
              {(tagInfo?.untagged ?? 0) > 0 && (
                <span
                  className={`chip ${untaggedOnly ? "on" : ""}`}
                  onClick={() => {
                    setTag("");
                    setUntaggedOnly(true);
                  }}
                  title="检索全量入库等来源不明的论文"
                >
                  未分组 {tagInfo!.untagged}
                </span>
              )}
            </div>
          </div>
        )}
        {(notes.length > 0 || err) && (
          <div style={{ padding: "6px 16px" }}>
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
            <div className="loading">
              {untaggedOnly
                ? "没有未分组的论文。"
                : tag
                  ? "该分组下暂无论文。"
                  : "没有符合条件的论文。检索一批，或上传本地 PDF/DOCX。"}
            </div>
          ) : (
            <table className="papers">
              <thead>
                <tr>
                  <th style={{ width: 90 }}>状态</th>
                  <th style={{ width: 56 }}>年份</th>
                  <th style={{ width: 56 }}>引用</th>
                  <th style={{ width: 64 }}>全文</th>
                  <th>标题（{total} 篇）</th>
                </tr>
              </thead>
              <tbody>
                {papers.map((p) => (
                  <tr
                    key={p.source_id}
                    className={`row ${detailId === p.source_id ? "sel" : ""}`}
                    onClick={() => onOpenDetail(p.source_id)}
                  >
                    <td>
                      <span className={`tag status-${p.status}`}>
                        {statusLabel(p.status)}
                      </span>
                    </td>
                    <td>{p.year ?? "-"}</td>
                    <td>{p.citations ?? "-"}</td>
                    <td>{p.has_pdf ? "✓" : p.abstract_only ? "摘要" : "-"}</td>
                    <td className="title-cell">
                      <div className="paper-title">{p.title}</div>
                      {p.tag && <div className="hint" style={{ fontSize: 11.5 }}>分组：{p.tag}</div>}
                      {p.error && <div className="paper-err" title={p.error}>⚠ {p.error}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!loading && papers.length < total && (
            <div className="loading" style={{ padding: 12 }}>
              <span className="chip" onClick={loadMore}>
                加载更多（已显示 {papers.length} / {total} 篇）
              </span>
            </div>
          )}
        </div>
      </div>
      {detailId ? (
        <PaperDetail
          key={detailId}
          id={detailId}
          onClose={() => onOpenDetail(null)}
          onChanged={() => {
            onChanged();
            load();
            loadTags();
          }}
        />
      ) : (
        <div className="detail-pane" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div className="hint">← 点击左侧论文查看详情、精读报告与实验结构</div>
        </div>
      )}

      {preview && (
        <div className="modal-mask" onClick={() => setPreview(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div style={{ fontWeight: 700 }}>
                  检索到 {preview.candidates.length} 条候选（新 {freshCount} 条，库内已有 {preview.candidates.length - freshCount} 条）
                </div>
                <div className="hint">
                  主题：{preview.topics.join(" / ")}
                  {preview.optimized ? "（已自动优化）" : ""} · 各源候选：{JSON.stringify(preview.source_hits)}
                </div>
              </div>
              <span style={{ cursor: "pointer", color: "var(--muted)" }} onClick={() => setPreview(null)}>
                ✕
              </span>
            </div>
            <div className="modal-body">
              {preview.candidates.map((c) => (
                <label key={c.source_id} className={`cand ${c.duplicate_of ? "dup" : ""}`}>
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
                      · {c.source} · {c.year ?? "-"} · 被引 {c.citations ?? "-"} ·{" "}
                      {c.has_pdf ? "有全文" : "仅摘要"}
                    </span>
                    {c.duplicate_of && <span className="tag" style={{ marginLeft: 6 }}>库内已有</span>}
                  </span>
                </label>
              ))}
            </div>
            <div className="modal-foot">
              <span className="chip" onClick={() => setSelected(new Set(preview.candidates.filter((c) => !c.duplicate_of).map((c) => c.source_id)))}>
                全选新的
              </span>
              <input
                type="text"
                placeholder="收入分组（如：知识图谱调研）"
                value={ingestTag}
                onChange={(e) => setIngestTag(e.target.value)}
                style={{ flex: 1 }}
              />
              <button className="primary" disabled={ingesting || selected.size === 0} onClick={doIngest}>
                {ingesting ? "入库中…" : `入库所选（${selected.size}）`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
