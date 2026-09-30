import { useCallback, useEffect, useState } from "react";
import { api, type SettingsView } from "../api";
import { applyTheme, currentTheme, type Theme } from "../theme";

/** OpenAI 兼容端点预设：点击即填 base_url + 推荐模型（Key 需自己填）。 */
const PRESETS: { label: string; base_url: string; model: string }[] = [
  { label: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "GLM-4.7" },
  { label: "DeepSeek", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { label: "Moonshot Kimi", base_url: "https://api.moonshot.cn/v1", model: "kimi-k2-0905-preview" },
  { label: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
];

export default function SettingsModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [view, setView] = useState<SettingsView | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState(""); // 留空 = 保持已存 Key
  const [theme, setTheme] = useState<Theme>(currentTheme());
  const [testing, setTesting] = useState(false);
  const [testOut, setTestOut] = useState<{ ok: boolean; message: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setView(s);
        setBaseUrl(s.base_url);
        setModel(s.model);
        if (!localStorage.getItem("pa-theme") && s.theme) {
          applyTheme(s.theme); // 新设备：跟随后端持久化的主题
          setTheme(s.theme);
        }
      })
      .catch((e) => setErr(String(e)));
  }, []);

  // Esc 关闭：设置弹窗是轻量中断，键盘可达性顺手补上
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const activePreset = PRESETS.find((p) => p.base_url === baseUrl.trim());

  const applyPreset = (p: (typeof PRESETS)[number]) => {
    setBaseUrl(p.base_url);
    setModel(p.model);
    setSavedNote("");
    setTestOut(null);
  };

  const doTest = useCallback(async () => {
    setTesting(true);
    setTestOut(null);
    setErr("");
    try {
      const out = await api.testSettings({
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || null, // null → 后端用已存/环境 Key
      });
      setTestOut(out);
    } catch (e) {
      setTestOut({ ok: false, message: String(e) });
    } finally {
      setTesting(false);
    }
  }, [baseUrl, model, apiKey]);

  const doSave = async () => {
    setSaving(true);
    setErr("");
    try {
      const out = await api.saveSettings({
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || null,
        theme, // 主题点选时已即时生效，保存只是持久化到后端
      });
      setView((v) => (v ? { ...v, has_key: out.has_key, api_key_masked: out.api_key_masked } : v));
      setApiKey("");
      setSavedNote(
        out.has_key
          ? "已保存，立即生效（后续请求即用新配置，无需重启）。"
          : "已保存端点，但还没有 Key——对话/Agent 功能在填入 Key 前不可用。",
      );
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="settings-title">设置</div>
            <div className="hint">模型平台（OpenAI 兼容接口）· 保存到本机 data/settings.json，不进 git、不上传</div>
          </div>
          <span className="modal-x" onClick={onClose}>✕</span>
        </div>

        <div className="settings-body">
          <div className="field-label">外观主题</div>
          <div className="theme-row">
            {([
              { k: "dark", name: "深色", desc: "暗色面板，适合长时间阅读", swatch: ["#0f1115", "#171a21", "#4f8cff"] },
              { k: "light", name: "浅色", desc: "白底黑字，明亮清爽", swatch: ["#f4f6fa", "#ffffff", "#3b7cf0"] },
            ] as { k: Theme; name: string; desc: string; swatch: string[] }[]).map((t) => (
              <div
                key={t.k}
                className={`theme-chip ${theme === t.k ? "on" : ""}`}
                onClick={() => {
                  setTheme(t.k);
                  applyTheme(t.k); // 即时生效，保存时持久化
                }}
              >
                <span className="theme-swatch">
                  {t.swatch.map((c) => <i key={c} style={{ background: c }} />)}
                </span>
                <span>
                  <div className="theme-name">{t.name}</div>
                  <div className="theme-desc">{t.desc}</div>
                </span>
              </div>
            ))}
          </div>

          <div className="field-label">厂商预设</div>
          <div className="chips preset-row">
            {PRESETS.map((p) => (
              <span
                key={p.label}
                className={`chip ${activePreset?.label === p.label ? "on" : ""}`}
                onClick={() => applyPreset(p)}
              >
                {p.label}
              </span>
            ))}
            <span className={`chip ${!activePreset && baseUrl ? "on" : ""}`}>自定义</span>
          </div>

          <label className="field">
            <span className="field-label">Base URL</span>
            <input
              type="text"
              placeholder="https://open.bigmodel.cn/api/paas/v4"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>

          <label className="field">
            <span className="field-label">API Key</span>
            <input
              type="password"
              placeholder={
                view?.has_key
                  ? `已配置（${view.api_key_masked}）——留空表示不修改`
                  : "sk-…（必填，否则对话与 Agent 不可用）"
              }
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="new-password"
            />
          </label>

          <label className="field">
            <span className="field-label">模型名</span>
            <input
              type="text"
              placeholder="GLM-4.7 / deepseek-chat / …"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </label>

          {testOut && (
            <div className={`test-out ${testOut.ok ? "ok" : "bad"}`}>
              {testOut.ok ? "✓ " : "✕ "}
              {testOut.message}
            </div>
          )}
          {err && <div className="err">⚠ {err}</div>}
          {savedNote && <div className="hint saved-note">· {savedNote}</div>}
        </div>

        <div className="modal-foot">
          <button className="ghost" disabled={testing} onClick={doTest}>
            {testing ? "测试中…" : "测试连接"}
          </button>
          <span className="hint grow">测试会发一次极小的真实补全请求（约 8 token）。</span>
          <button className="primary" disabled={saving || !baseUrl.trim() || !model.trim()} onClick={doSave}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
