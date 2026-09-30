import { useEffect, useState } from "react";
import { api, type SettingsView, type StatusSummary } from "./api";
import Library from "./pages/Library";
import Assistant from "./pages/Assistant";
import SettingsModal from "./components/SettingsModal";
import { applyTheme } from "./theme";

type Tab = "library" | "assistant";

/** URL 深链：?paper=<id> 直开详情，?tab=chat 直开助手（可分享、可回退）。 */
function readLocation(): { tab: Tab; paper: string | null } {
  const p = new URLSearchParams(window.location.search);
  return {
    tab: p.get("tab") === "chat" ? "assistant" : "library",
    paper: p.get("paper"),
  };
}

export default function App() {
  const [{ tab, paper }, setLocation] = useState(readLocation);
  const [summary, setSummary] = useState<StatusSummary | null>(null);
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);

  const refresh = () => api.status().then(setSummary).catch(() => {});
  // 后端 settings 是主题的持久层；localStorage 没有用户选择时（新设备）跟随后端
  const refreshSettings = () =>
    api
      .settings()
      .then((s) => {
        setSettings(s);
        if (!localStorage.getItem("pa-theme") && s.theme) applyTheme(s.theme);
      })
      .catch(() => {});
  useEffect(() => {
    refresh();
    refreshSettings(); // 未配 Key 时顶栏齿轮亮提醒点，引导完成首次配置
  }, []);

  const openDetail = (id: string | null) => {
    const u = new URL(window.location.href);
    if (id) u.searchParams.set("paper", id);
    else u.searchParams.delete("paper");
    window.history.replaceState(null, "", u);
    setLocation({ tab: "library", paper: id });
  };
  const switchTab = (t: Tab) => {
    const u = new URL(window.location.href);
    if (t === "assistant") u.searchParams.set("tab", "chat");
    else u.searchParams.delete("tab");
    window.history.replaceState(null, "", u);
    setLocation({ tab: t, paper });
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">📄</span> paper-agent
          <span className="ver">{summary ? `v${summary.version}` : ""}</span>
        </div>
        <nav className="tabs">
          <button className={tab === "library" ? "on" : ""} onClick={() => switchTab("library")}>
            文献库
          </button>
          <button className={tab === "assistant" ? "on" : ""} onClick={() => switchTab("assistant")}>
            助手{agentBusy && <span className="run-dot" title="Agent 任务执行中，切回可查看进度" />}
          </button>
        </nav>
        <div className="summary">
          {summary && (
            <>
              <span>共 {summary.total} 篇</span>
              <span className="dot">·</span>
              <span>{summary.vector_chunks} 知识块</span>
            </>
          )}
        </div>
        <button
          className={`icon-btn ${settings && !settings.has_key ? "warn" : ""}`}
          title={settings && !settings.has_key ? "尚未配置 API Key，点击设置" : "设置（模型平台 / API Key）"}
          onClick={() => setSettingsOpen(true)}
        >
          ⚙
        </button>
      </header>
      {settingsOpen && (
        <SettingsModal
          onClose={() => setSettingsOpen(false)}
          onSaved={refreshSettings}
        />
      )}
      {/* 两页常驻挂载、仅显隐切换：组件不卸载，Agent 任务轮询/文献库筛选与滚动位置都得以保留 */}
      <main>
        <div className={`page ${tab === "library" ? "" : "off"}`}>
          <Library summary={summary} onOpenDetail={openDetail} detailId={paper} onChanged={refresh} />
        </div>
        <div className={`page ${tab === "assistant" ? "" : "off"}`}>
          <Assistant
            active={tab === "assistant"}
            onAgentBusy={setAgentBusy}
            onOpenDetail={openDetail}
            onLibChanged={refresh}
          />
        </div>
      </main>
    </div>
  );
}
