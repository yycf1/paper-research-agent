import type { DownloadItem, Job } from "../api";

const LABELS: Record<DownloadItem["status"], string> = {
  pending: "等待下载",
  running: "正在下载",
  saved: "全文已保存",
  already_saved: "本地已有全文",
  no_link: "没有全文链接",
  failed: "下载失败",
  missing: "记录已移除",
};

export default function DownloadProgress({
  job,
  onOpenDetail,
}: {
  job: Job;
  onOpenDetail: (id: string) => void;
}) {
  const items = (job.result.items ?? []) as DownloadItem[];
  const count = (statuses: string[]) =>
    items.filter((item) => statuses.includes(item.status)).length;
  return (
    <section className="download-progress" aria-label="所选论文下载进度">
      <div className="card-heading">
        <strong>
          {job.status === "running"
            ? "正在下载所选论文"
            : job.status === "error"
              ? "下载任务中断"
              : "所选论文下载结果"}
        </strong>
        <span className="hint">
          {Number(job.result.completed ?? 0)} /{" "}
          {Number(job.result.total ?? items.length)}
        </span>
      </div>
      <p className="hint">
        全文已保存 {count(["saved", "already_saved"])} · 无链接{" "}
        {count(["no_link"])} · 未下载成功 {count(["failed", "missing"])}
        。下载完成后可进入论文开始解析与精读。
      </p>
      {job.error && <p className="err">{job.error}</p>}
      <ul>
        {items.map((item) => (
          <li key={item.source_id}>
            <div>
              <button
                className="paper-title"
                onClick={() => onOpenDetail(item.source_id)}
              >
                {item.title || item.source_id}
              </button>
              <small>{item.detail}</small>
            </div>
            <span className={`download-state ${item.status}`}>
              {LABELS[item.status]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
