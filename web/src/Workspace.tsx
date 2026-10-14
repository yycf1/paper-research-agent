import { useCallback, useEffect, useState } from "react";
import { api, type SettingsView, type StatusSummary } from "./api";
import Library from "./pages/Library";
import Assistant from "./pages/Assistant";
import Tasks from "./pages/Tasks";
import SettingsModal from "./components/SettingsModal";
import Icon from "./components/Icon";
import { applyTheme } from "./theme";

type Tab = "library" | "discover" | "assistant" | "tasks";
const LABELS: Record<Tab, string> = {
  library: "文献库",
  discover: "发现论文",
  assistant: "研究助手",
  tasks: "任务记录",
};
function readLocation(): { tab: Tab; paper: string | null } {
  const params = new URLSearchParams(location.search);
  const value = params.get("tab");
  return {
    tab:
      value === "chat"
        ? "assistant"
        : value && value in LABELS
          ? (value as Tab)
          : "library",
    paper: params.get("paper"),
  };
}

export default function Workspace() {
  const [{ tab, paper }, setLocation] = useState(readLocation);
  const [summary, setSummary] = useState<StatusSummary | null>(null);
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [agentBusy, setAgentBusy] = useState(false);
  const [groupHost, setGroupHost] = useState<HTMLDivElement | null>(null);
  const [pendingTask, setPendingTask] = useState<{
    task: string;
    anchor: string;
  } | null>(null);
  const [researchScope, setResearchScope] = useState<{
    ids: string[];
    compare: boolean;
    key: number;
  } | null>(null);
  const consumePendingTask = useCallback(() => setPendingTask(null), []);
  const refresh = useCallback(() => {
    api
      .status()
      .then(setSummary)
      .catch(() => {});
  }, []);
  const refreshSettings = useCallback(() => {
    api
      .settings()
      .then((s) => {
        setSettings(s);
        if (!localStorage.getItem("pa-theme") && s.theme) applyTheme(s.theme);
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    refresh();
    refreshSettings();
  }, [refresh, refreshSettings]);
  useEffect(() => {
    const onPop = () => setLocation(readLocation());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);
  const navigate = (next: Tab, id: string | null = null) => {
    const url = new URL(location.href);
    url.searchParams.set("tab", next === "assistant" ? "chat" : next);
    if (id) url.searchParams.set("paper", id);
    else url.searchParams.delete("paper");
    history.pushState(null, "", url);
    setLocation({ tab: next, paper: id });
  };
  const openDetail = (id: string | null) => navigate("library", id);
  const startAgent = (task: string, anchor: string) => {
    setPendingTask({ task, anchor });
    navigate("assistant");
  };
  const startResearch = (ids: string[], compare: boolean) => {
    setResearchScope({ ids, compare, key: Date.now() });
    navigate("assistant");
  };
  return (
    <div className="workspace">
      <aside className="workspace-rail">
        <a
          className="workspace-brand"
          href="?tab=library"
          onClick={(e) => {
            e.preventDefault();
            navigate("library");
          }}
        >
          <span className="brand-mark">
            <Icon name="library" size={20} />
          </span>
          <span>
            <strong>Paper Research</strong>
            <small>个人文献库智能体</small>
          </span>
        </a>
        <nav className="workspace-nav" aria-label="主导航">
          {(Object.keys(LABELS) as Tab[]).map((key) => (
            <button
              key={key}
              className={tab === key ? "on" : ""}
              aria-current={tab === key ? "page" : undefined}
              onClick={() => navigate(key)}
            >
              <Icon name={key} />
              <span>{LABELS[key]}</span>
              {key === "assistant" && agentBusy && (
                <span className="run-dot" title="任务进行中" />
              )}
            </button>
          ))}
        </nav>
        <div
          className="rail-groups"
          ref={setGroupHost}
          hidden={tab !== "library" || !!paper}
        />
        <div className="rail-footer">
          <span className="hint">本地研究工作区</span>
          <button onClick={() => setSettingsOpen(true)}>
            <Icon name="settings" />
            设置
            {settings && !settings.has_key && <span className="config-dot" />}
          </button>
        </div>
      </aside>
      <div className="workspace-content">
        <header className="workspace-topbar">
          <span>
            工作区{" "}
            <span className="muted">
              / {paper && tab === "library" ? "论文阅读" : LABELS[tab]}
            </span>
          </span>
          <span className="hint">
            {summary
              ? `${summary.total} 条文献记录 · ${summary.vector_chunks} 索引片段`
              : "正在连接工作区…"}
          </span>
        </header>
        {settings && !settings.has_key && (
          <div className="setup-notice">
            导入文献后即可整理资料；提取、问答和语义检索需要配置模型。
            <button onClick={() => setSettingsOpen(true)}>配置模型 →</button>
          </div>
        )}
        <main className="workspace-main">
          <div className={`page ${tab === "library" ? "" : "off"}`}>
            <Library
              summary={summary}
              detailId={paper}
              onOpenDetail={openDetail}
              onChanged={refresh}
              onStartAgent={startAgent}
              view="library"
              groupHost={groupHost}
              onDiscover={() => navigate("discover")}
              onResearch={startResearch}
            />
          </div>
          <div className={`page ${tab === "discover" ? "" : "off"}`}>
            <Library
              summary={summary}
              detailId={null}
              onOpenDetail={openDetail}
              onChanged={refresh}
              onStartAgent={startAgent}
              view="discover"
              onResearch={startResearch}
            />
          </div>
          <div className={`page ${tab === "assistant" ? "" : "off"}`}>
            <Assistant
              active={tab === "assistant"}
              onAgentBusy={setAgentBusy}
              onOpenDetail={openDetail}
              onLibChanged={refresh}
              pendingTask={pendingTask}
              onConsumePendingTask={consumePendingTask}
              researchScope={researchScope}
              onDiscover={() => navigate("discover")}
            />
          </div>
          <div className={`page ${tab === "tasks" ? "" : "off"}`}>
            <Tasks active={tab === "tasks"} onOpenDetail={openDetail} />
          </div>
        </main>
      </div>
      {settingsOpen && (
        <SettingsModal
          onClose={() => setSettingsOpen(false)}
          onSaved={refreshSettings}
        />
      )}
    </div>
  );
}
