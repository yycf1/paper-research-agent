import type { SourceRef } from "../api";

export default function Evidence({ source }: { source: SourceRef }) {
  if (!source.chunks?.length) return null;
  return <details className="evidence">
    <summary>查看原文证据（{source.chunks.length} 段）</summary>
    {source.chunks.map(c => <div key={c.n} className="evidence-chunk">
      <div className="hint">[{c.n}] {c.page ? `第 ${c.page} 页 · ` : ""}{c.chunk_id}</div>
      <p>{c.text}</p>
    </div>)}
  </details>;
}
