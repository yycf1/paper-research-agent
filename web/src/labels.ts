/** 状态 → 中文标签（列表行、筛选 chips、详情页共用，M11 起统一中文显示）。 */
export const STATUS_ORDER = [
  "discovered",
  "downloaded",
  "parsed",
  "analyzed",
  "indexed",
  "download_failed",
];

const LABELS: Record<string, string> = {
  discovered: "仅文献信息",
  downloaded: "全文已保存",
  parsed: "文本已提取",
  analyzed: "报告已生成",
  indexed: "已建索引",
  download_failed: "全文下载失败",
};

export const STATUS_HELP: Record<string, string> = {
  discovered:
    "已保存标题、作者、摘要等题录信息，尚未下载全文。进入论文后可获取并处理全文。",
  downloaded: "本地已保存 PDF 或 DOCX，尚未完成文本提取。",
  parsed: "已提取可读文本，尚未完成分析与索引。",
  analyzed: "已完成分析与报告，尚未完成索引。",
  indexed: "已完成索引；当前是否可问答还需检查详情页的索引版本与就绪状态。",
  download_failed:
    "最近一次获取全文失败，例如源站拒绝、链接失效或超时；文献信息仍保留，可重试或导入本地文件。",
};

export function statusLabel(s: string): string {
  return LABELS[s] ?? s;
}
