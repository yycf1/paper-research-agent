"""LLM 客户端：OpenAI 兼容封装，读 config.yaml 的 llm 节与 .env 的 Key。

extraction 与 rag/qa 共用；返回 (文本, token用量)，方便记账。
免费档限流（如智谱 429/1305）是常态，chat 内置指数退避重试。

M14 起 agent loop 需要原生 function calling：chat_with_tools 返回完整的
AssistantMessage（含 tool_calls），chat 退化为它的一层文本包装——两者共享
同一套限流重试核心 _complete。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from openai import OpenAI, RateLimitError

from paper_agent.config import load_config, resolve_endpoint
from paper_agent.observability import task_config, normalize_usage
from paper_agent.provider_calls import provider_call

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = (10, 20, 40)


@dataclass
class ToolCall:
    """模型发起的一次工具调用（OpenAI tool_calls 的扁平化）。

    arguments 保留模型返回的原始 JSON 字符串：解析交给执行器，
    解析失败也能作为 fatal 反馈回模型让它自行纠正。
    """

    id: str
    name: str
    arguments: str


@dataclass
class ChatResponse:
    """一次补全的完整结果：文本、工具调用与用量。"""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def _complete(
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float,
    cfg: dict | None,
    thinking: bool | None,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> ChatResponse:
    """限流退避重试的补全核心（chat / chat_with_tools 共用）。"""
    cfg = cfg or task_config() or load_config()
    if thinking is None:
        thinking = cfg.get("llm", {}).get("thinking")
    ep = resolve_endpoint(cfg["llm"])
    if not ep.api_key:
        raise RuntimeError("未配置 LLM API Key：请复制 .env.example 为 .env 并填入 Key")
    client = OpenAI(base_url=ep.base_url, api_key=ep.api_key, timeout=180, max_retries=0)
    extra: dict = {}
    if thinking is not None:
        extra["extra_body"] = (
            {"enable_thinking": thinking} if any(host in ep.base_url for host in ('siliconflow', 'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'))
            else {"thinking": {"type": "enabled" if thinking else "disabled"}}
        )
    if tools:
        extra["tools"] = tools
        if tool_choice:
            extra["tool_choice"] = tool_choice
    last_exc: RateLimitError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = provider_call(lambda: client.chat.completions.create(
                model=ep.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **extra,
            ), model=ep.model, retry_rate_limit=False)
            usage = resp.usage
            usage_dict = {k: v for k, v in normalize_usage(usage).items() if k != 'total'}
            usage_dict['model'] = ep.model
            msg = resp.choices[0].message
            raw_calls = getattr(msg, "tool_calls", None) or []
            tool_calls = [
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "")
                for tc in raw_calls
            ]
            return ChatResponse(content=getattr(msg, "content", None), tool_calls=tool_calls, usage=usage_dict)
        except RateLimitError as exc:
            body = exc.body if isinstance(exc.body, dict) else {}
            error = body.get("error", body)
            error = error if isinstance(error, dict) else {}
            code = str(error.get("code", ""))
            message = str(error.get("message", ""))
            if code == "1113" or "余额不足" in message or "insufficient_quota" in (code, error.get("type")):
                raise RuntimeError("模型账户余额不足或无可用额度：请在设置中切换可用模型或补充额度。") from exc
            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                time.sleep(wait)
    raise RuntimeError(
        f"LLM 连续 {_MAX_ATTEMPTS} 次被限流（免费档调用频率限制），已退避重试无效：{last_exc}"
    ) from last_exc


def chat(
    messages: list[dict],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    cfg: dict | None = None,
    thinking: bool | None = None,
) -> tuple[str, dict]:
    """一次对话补全，限流时自动退避重试。返回 (回复文本, {"prompt": n, "completion": n, "model": name})。

    thinking：GLM 等混合推理模型的思考开关。None=平台默认（适合提取/报告等复杂任务）；
    False=禁用思考——意图分类、查询优化等简单任务必须禁用，否则思考 tokens 会
    吃光 max_tokens 导致 content 为空（M5 实测踩坑）。
    """
    resp = _complete(
        messages, max_tokens=max_tokens, temperature=temperature, cfg=cfg, thinking=thinking
    )
    return (resp.content or "", resp.usage)


def chat_with_tools(
    messages: list[dict],
    *,
    tools: list[dict],
    tool_choice: str = "auto",
    max_tokens: int = 4096,
    temperature: float = 0.2,
    cfg: dict | None = None,
    thinking: bool | None = False,
) -> ChatResponse:
    """带工具注册表的补全：返回完整 ChatResponse（含 tool_calls）。

    thinking 默认 False：循环中的「选工具、填参数」是简单任务，M5 实测
    思考模式会吃光 max_tokens 导致 tool_calls/content 为空。
    """
    return _complete(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        cfg=cfg,
        thinking=thinking,
        tools=tools,
        tool_choice=tool_choice,
    )


def test_endpoint(*, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """设置页「测试连接」：单次试探性补全，不走限流重试核心。

    chat/_complete 的 4 次退避重试要等 70 秒，不适合交互式检测；这里
    一次请求、20 秒超时、max_retries=0，把任何异常转成可读的失败原因。
    """
    if not (base_url and api_key and model):
        return False, "Base URL、API Key、模型名均不能为空"
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=20, max_retries=0)
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
            temperature=0,
        )
        ms = int((time.monotonic() - t0) * 1000)
        name = getattr(resp, "model", None) or model
        return True, f"连通正常（{ms} ms，模型 {name}）"
    except Exception as exc:  # 网络/鉴权/模型名错误都要给用户可读信息
        return False, f"{type(exc).__name__}: {exc}"[:300]


def assistant_message(content: str | None, tool_calls: list[ToolCall]) -> dict:
    """构造可回传的 assistant 消息：声明了 tool_calls 的轮次必须原样进上下文，
    后续 tool 结果消息才能通过 tool_call_id 与调用一一配对。"""
    msg: dict = {"role": "assistant", "content": content or ""}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in tool_calls
        ]
    return msg


def tool_message(call_id: str, payload: dict, *, max_chars: int = 4000) -> dict:
    """工具结果 → role=tool 消息；序列化失败兜底 repr，超长截断保护上下文。"""
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(payload)
    if len(text) > max_chars:
        text = text[:max_chars] + f"…（已截断，原始长度 {len(text)} 字符）"
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def parse_json_reply(text: str) -> dict:
    """解析 LLM 回复中的 JSON 对象：剥代码围栏，兜底截取首个 { 到最后一个 }。"""
    fenced = _JSON_FENCE_RE.match(text.strip())
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"回复中不含 JSON 对象：{text[:200]!r}")
        return json.loads(text[start : end + 1])
