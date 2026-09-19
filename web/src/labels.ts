/** 状态 → 中文标签（列表行、筛选 chips、详情页共用，M11 起统一中文显示）。 */
export const STATUS_ORDER = ["discovered", "downloaded", "parsed", "analyzed", "indexed", "download_failed"];

const LABELS: Record<string, string> = {
  discovered: "已发现",
  downloaded: "已下载",
  parsed: "已解析",
  analyzed: "已分析",
  indexed: "已入库",
  download_failed: "失败",
};

export function statusLabel(s: string): string {
  return LABELS[s] ?? s;
}
