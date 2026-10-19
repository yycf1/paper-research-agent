import { useEffect, useState } from "react";
import { api, type SourceRef } from "../api";
import Evidence from "./Evidence";

export default function AnswerSources({ sources, onOpenDetail }: {
  sources: SourceRef[]; onOpenDetail: (id: string) => void;
}) {
  const [types, setTypes] = useState<Record<string, string | null | undefined>>({});
  useEffect(() => {
    let current = true;
    void Promise.allSettled([...new Set(sources.map(s => s.paper_id))].map(id => api.paper(id))).then(results => {
      if (!current) return;
      const papers = results.flatMap(r => r.status === "fulfilled" && r.value.has_local_file ? [r.value] : []);
      setTypes(Object.fromEntries(papers.map(p => [p.source_id, p.local_file_type])));
    });
    return () => { current = false; };
  }, [sources]);
  return <div className="conversation-sources">{sources.map((s, j) => <div className="source-card" key={j}>
    <button className="ghost" onClick={() => onOpenDetail(s.paper_id)}>{s.n.map(n => `[${n}]`).join("")} {s.title} · {s.section}</button>
    <Evidence source={s} fileType={types[s.paper_id]} />
  </div>)}</div>;
}
