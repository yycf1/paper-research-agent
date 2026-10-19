import { useEffect, useState } from "react";
import { api, type Paper } from "../api";

export default function PaperScopePicker({ ids, onChange, disabled, onOpenDetail }: {
  ids: string[] | null; onChange: (ids: string[] | null) => void;
  disabled: boolean; onOpenDetail: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [papers, setPapers] = useState<Paper[]>([]);
  const [known, setKnown] = useState<Record<string, Paper>>({});
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    let current = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      api.papers({ q: query, limit: 50 }).then(r => {
        if (!current) return;
        setPapers(r.papers); setTotal(r.total); setError("");
        setKnown(k => ({ ...k, ...Object.fromEntries(r.papers.map(p => [p.source_id, p])) }));
      }).catch(e => { if (current) setError(String(e)); })
        .finally(() => { if (current) setLoading(false); });
    }, 180);
    return () => { current = false; window.clearTimeout(timer); };
  }, [open, query]);
  useEffect(() => {
    let current = true;
    void Promise.allSettled((ids ?? []).map(id => api.paper(id))).then(results => {
      if (!current) return;
      const loaded = results.flatMap(r => r.status === "fulfilled" ? [r.value] : []);
      setKnown(k => ({ ...k, ...Object.fromEntries(loaded.map(p => [p.source_id, p])) }));
    });
    return () => { current = false; };
  }, [ids]);
  return <div className="research-scope">
    <div className="scope-line">
      <span className="hint">资料范围</span>
      <button aria-expanded={open} aria-controls="research-scope-options" disabled={disabled}
        onClick={() => setOpen(!open)}>{ids === null ? "整个文献库" : `已选 ${ids.length} 篇论文`} <span aria-hidden>⌄</span></button>
      <span className="hint scope-memory">后续追问沿用此范围</span>
    </div>
    {!!ids?.length && <div className="scope-chips">{ids.map(id => <button key={id}
      title={known[id]?.title ?? id} onClick={() => onOpenDetail(id)}>{known[id]?.title ?? id}</button>)}</div>}
    {open && <div className="scope-popover" id="research-scope-options" role="region" aria-label="选择资料范围">
      <div className="scope-popover-head"><strong>选择资料范围</strong><button className="ghost sm" onClick={() => setOpen(false)}>完成</button></div>
      <div className="scope-choice"><button aria-pressed={ids === null} disabled={disabled} onClick={() => onChange(null)}>整个文献库</button>
        <button aria-pressed={ids !== null} disabled={disabled} onClick={() => onChange(ids ?? [])}>指定论文（最多 6 篇）</button></div>
      <input aria-label="搜索范围内的论文" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索全库标题或关键词…" />
      <div className="scope-options">{papers.map(p => <label key={p.source_id}>
        <input type="checkbox" checked={ids?.includes(p.source_id) ?? false}
          disabled={disabled || (!ids?.includes(p.source_id) && (ids?.length ?? 0) >= 6)}
          onChange={() => onChange(ids?.includes(p.source_id) ? ids.filter(x => x !== p.source_id) : [...(ids ?? []), p.source_id])} />
        <span>{p.title}<small>{p.status === "indexed" ? "已索引 · 可查原文证据" : "未索引 · 可查题录"}</small></span></label>)}</div>
      {loading ? <p className="hint">正在加载论文…</p> : <p className="hint">找到 {total} 篇，展示前 50 篇；可搜索全库。比较需要选择 2～6 篇。</p>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </div>;
}
