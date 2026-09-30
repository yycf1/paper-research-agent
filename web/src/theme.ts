/** 主题管理：localStorage 同步预加载（防首帧闪烁），设置页可切换并持久化到后端。 */

export type Theme = "dark" | "light";

const KEY = "pa-theme";

export function applyTheme(t: Theme): void {
  document.documentElement.dataset.theme = t;
  localStorage.setItem(KEY, t);
}

/** 首帧前调用：localStorage 优先，其次跟随 document 上已有的 data-theme（默认 dark）。 */
export function initTheme(): void {
  const saved = localStorage.getItem(KEY);
  document.documentElement.dataset.theme = saved === "light" ? "light" : saved === "dark" ? "dark" : document.documentElement.dataset.theme || "dark";
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}
