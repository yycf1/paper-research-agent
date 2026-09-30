"""运行时设置（M17）测试：覆盖层合并、Key 脱敏、测试连接的取值优先级。

API 测试沿用 test_api_agent 的模式：monkeypatch routes.load_config 指向
tmp_path 数据目录（复用真实合并逻辑 load_runtime_settings），全程离线；
「测试连接」注入假 test_endpoint 只捕获参数，不发网络请求。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from paper_agent import config as cfg_mod
from paper_agent.config import resolve_endpoint
from paper_agent.server import routes as rt
from paper_agent.server.app import create_app

_ENV_KEY = "env-key-12345678"
_WEB_KEY = "web-key-98765432"


def _fake_loader(tmp_path):
    """带运行时合并的假 load_config：等价于真实 load_config，但数据目录在 tmp。"""

    def _load() -> dict:
        cfg = {
            "data_dir": str(tmp_path / "data"),
            "sources": {},
            "llm": {
                "base_url": "https://cfg.example/v1",
                "model": "cfg-model",
                "api_key_env": "PAPER_AGENT_TEST_KEY",
            },
        }
        s = cfg_mod.load_runtime_settings(cfg)
        if s.get("base_url"):
            cfg["llm"]["base_url"] = s["base_url"]
        if s.get("model"):
            cfg["llm"]["model"] = s["model"]
        if s.get("api_key"):
            cfg["llm"]["runtime_api_key"] = s["api_key"]
        return cfg

    return _load


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(rt, "load_config", _fake_loader(tmp_path))
    monkeypatch.setenv("PAPER_AGENT_TEST_KEY", _ENV_KEY)
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# 配置层单元测试
# ---------------------------------------------------------------------------


def test_load_config_merges_runtime_settings(monkeypatch, tmp_path):
    """settings.json 的非空字段并入 llm 节；resolve_endpoint 中 Web Key 优先于 env。"""
    sp = tmp_path / "settings.json"
    sp.write_text(
        json.dumps({"base_url": "https://web.example/v1", "model": "web-model", "api_key": _WEB_KEY}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_mod, "settings_path", lambda cfg: sp)
    cfg = cfg_mod.load_config()
    assert cfg["llm"]["base_url"] == "https://web.example/v1"
    assert cfg["llm"]["model"] == "web-model"
    assert cfg["llm"]["runtime_api_key"] == _WEB_KEY

    monkeypatch.setenv("PAPER_AGENT_TEST_KEY", _ENV_KEY)
    assert resolve_endpoint(cfg["llm"]).api_key == _WEB_KEY  # Web 覆盖层赢
    assert resolve_endpoint({"api_key_env": "PAPER_AGENT_TEST_KEY"}).api_key == _ENV_KEY  # 回退 env


def test_load_config_tolerates_broken_settings(monkeypatch, tmp_path):
    """settings.json 损坏 → 静默回退 config.yaml，不抛异常不阻断启动。"""
    sp = tmp_path / "settings.json"
    sp.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "settings_path", lambda cfg: sp)
    cfg = cfg_mod.load_config()
    assert "runtime_api_key" not in cfg.get("llm", {})


def test_mask_key():
    assert rt._mask_key("") == ""
    assert rt._mask_key("short") == "****"
    assert rt._mask_key(_WEB_KEY) == "web-****5432"


# ---------------------------------------------------------------------------
# API 测试
# ---------------------------------------------------------------------------


def test_settings_roundtrip_masked(monkeypatch, tmp_path):
    """保存 → GET 回读：只见脱敏 Key，明文不出现在任何响应里。"""
    client = _client(monkeypatch, tmp_path)

    r = client.put("/api/settings", json={"base_url": "https://web.example/v1", "model": "web-model", "api_key": _WEB_KEY})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "api_key_masked": "web-****5432", "has_key": True}

    data = client.get("/api/settings").json()
    assert data["base_url"] == "https://web.example/v1"
    assert data["model"] == "web-model"
    assert data["api_key_masked"] == "web-****5432"
    assert data["has_key"] is True
    assert data["theme"] == "dark"  # 缺省主题
    assert _WEB_KEY not in json.dumps(data)  # 明文永不回显

    # 落盘位置在 data_dir 下，且不含在 config.yaml 里
    saved = json.loads((tmp_path / "data" / "settings.json").read_text(encoding="utf-8"))
    assert saved["api_key"] == _WEB_KEY


def test_settings_theme_roundtrip(monkeypatch, tmp_path):
    """主题选择持久化：合法值落盘并可回读，非法值 422。"""
    client = _client(monkeypatch, tmp_path)

    r = client.put("/api/settings", json={"base_url": "https://a/v1", "model": "m", "theme": "light"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["theme"] == "light"

    r = client.put("/api/settings", json={"base_url": "https://a/v1", "model": "m", "theme": "pink"})
    assert r.status_code == 422  # pydantic pattern 拦截
    assert client.get("/api/settings").json()["theme"] == "light"  # 原值不被破坏


def test_settings_save_keeps_key_when_blank(monkeypatch, tmp_path):
    """api_key 留空/None = 保持已存 Key（表单回填场景不会误清空）。"""
    client = _client(monkeypatch, tmp_path)
    client.put("/api/settings", json={"base_url": "https://a/v1", "model": "m1", "api_key": _WEB_KEY})

    r = client.put("/api/settings", json={"base_url": "https://a/v1", "model": "m2", "api_key": None})
    assert r.json()["has_key"] is True
    r = client.put("/api/settings", json={"base_url": "https://a/v1", "model": "m2", "api_key": ""})
    assert r.json()["api_key_masked"] == "web-****5432"


def test_settings_test_prefers_form_values(monkeypatch, tmp_path):
    """测试连接：表单有值用表单（可未保存先试），否则回退当前生效配置。"""
    client = _client(monkeypatch, tmp_path)
    captured: dict = {}

    def fake_test(**kw):
        captured.update(kw)
        return True, "ok 42ms"

    monkeypatch.setattr(rt, "test_endpoint", fake_test)

    client.post("/api/settings/test", json={"base_url": "https://form/v1", "model": "form-model", "api_key": "form-key-11111111"})
    assert captured == {"base_url": "https://form/v1", "api_key": "form-key-11111111", "model": "form-model"}

    captured.clear()
    client.post("/api/settings/test", json={})
    assert captured == {"base_url": "https://cfg.example/v1", "api_key": _ENV_KEY, "model": "cfg-model"}


def test_settings_missing_payload_defaults(monkeypatch, tmp_path):
    """GET 在未配置任何 Key 时返回 has_key=False（顶栏齿轮据此亮提醒点）。"""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("PAPER_AGENT_TEST_KEY", raising=False)  # _client 会 setenv，需在其后删除
    data = client.get("/api/settings").json()
    assert data["has_key"] is False
    assert data["api_key_masked"] == ""
