"""配置加载：config.yaml 提供平台与流水线参数，API Key 从 .env 读取。

配置分三层，优先级从低到高（M17 起）：
  config.yaml   代码默认值，进 git，人工维护；
  .env          本地密钥（api_key_env 指向的环境变量），进 .gitignore；
  data/settings.json  Web 设置页写入的运行时覆盖（LLM 端点三项），
                只覆盖非空字段——load_config 每请求执行，改完即生效，
                CLI 与 Web 共用同一套生效链路，无需重启。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class EndpointConfig:
    """一个 OpenAI 兼容服务端点（LLM 与 embedding 共用结构）。"""

    base_url: str
    model: str
    api_key: str = ""


def settings_path(cfg: dict) -> Path:
    """运行时设置文件：随 data_dir 走（data/ 已在 .gitignore，Key 不会入库）。"""
    return PROJECT_ROOT / cfg.get("data_dir", "data") / "settings.json"


def load_runtime_settings(cfg: dict) -> dict:
    """读运行时覆盖；文件缺失/损坏一律静默回退 config.yaml，不阻断启动。"""
    try:
        return json.loads(settings_path(cfg).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}


def load_config() -> dict:
    """加载 config.yaml + .env，再叠加 data/settings.json 的非空覆盖项。"""
    load_dotenv(ENV_PATH)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    rt = load_runtime_settings(cfg)
    if rt:
        llm = cfg.setdefault("llm", {})
        if rt.get("base_url"):
            llm["base_url"] = rt["base_url"]
        if rt.get("model"):
            llm["model"] = rt["model"]
        if rt.get("api_key"):
            llm["runtime_api_key"] = rt["api_key"]
    return cfg


def resolve_endpoint(raw: dict) -> EndpointConfig:
    """从配置节解析端点；Web 运行时 Key 优先，其次 api_key_env 指向的环境变量。

    runtime_api_key 仅在 llm 节出现（load_config 注入），embedding 等节不受影响。
    """
    env_name = raw.get("api_key_env", "LLM_API_KEY")
    return EndpointConfig(
        base_url=raw.get("base_url", ""),
        model=raw.get("model", ""),
        api_key=raw.get("runtime_api_key") or os.environ.get(env_name, ""),
    )
