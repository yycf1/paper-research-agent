import { api, type SourceRef } from "../api";

export default function Evidence({ source, fileType }: { source: SourceRef; fileType?: string | null }) {
  if (!source.chunks?.length) return null;
  return <details className="evidence">
    <summary>查看原文证据（{source.chunks.length} 段）</summary>
    {source.chunks.map(c => <div key={c.n} className="evidence-chunk">
      <div className="hint">[{c.n}] {c.page ? `第 ${c.page} 页 · ` : ""}{c.chunk_id}</div>
      {fileType && <a href={api.originalFile(source.paper_id, fileType === "pdf" ? c.page : undefined)} target="_blank" rel="noreferrer">{fileType === "pdf" ? `打开原 PDF${c.page ? ` 第 ${c.page} 页` : ""}` : "下载 DOCX 核对（无稳定页码）"}</a>}
      <p>{c.text}</p>
    </div>)}
  </details>;
}
