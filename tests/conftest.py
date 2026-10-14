"""单测不能因开发机 .env 中存在真密钥而意外调用付费模型。"""
from dataclasses import replace
import pytest


@pytest.fixture(autouse=True)
def offline_llm_endpoint(monkeypatch):
    from paper_agent import llm
    resolve = llm.resolve_endpoint
    monkeypatch.setattr(llm, "resolve_endpoint", lambda raw: replace(resolve(raw), api_key=""))
