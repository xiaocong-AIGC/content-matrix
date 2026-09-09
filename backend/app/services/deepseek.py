"""Thin DeepSeek client (OpenAI-compatible REST). Used by the 内容引擎 to remix
our high-engagement posts into fresh, differentiated drafts.

The API key MUST come from the env var DEEPSEEK_API_KEY (loaded via Settings) —
it is never hardcoded. If unset, callers get a clear DeepSeekError.
"""

import json
import re

import httpx

from app.core.config import get_settings


class DeepSeekError(RuntimeError):
    """Raised for missing key / API / parse failures (surfaced as HTTP 400/502)."""


def is_configured() -> bool:
    return bool(get_settings().deepseek_api_key)


def _strip_fence(text: str) -> str:
    """去掉模型可能加的 markdown 代码块包裹（```json … ```），只留 JSON 本体。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    return t


def _extract_json(text: str) -> str:
    """从一段文字里抠出最外层的 JSON 对象（兜底：模型在 JSON 前后多写了话）。"""
    t = _strip_fence(text)
    if t.startswith("{"):
        return t
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if 0 <= start < end else t


def chat_json(
    system: str,
    user: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    temperature: float = 1.0,
    timeout: float = 300.0,
    max_tokens: int = 8192,
) -> tuple[dict, dict]:
    """Call an OpenAI-compatible chat completion forcing a JSON object response.
    Returns (parsed_dict, usage). `api_key`/`base_url`/`model` override the env
    (admin-saved config, e.g. an enterprise gateway). Raises DeepSeekError.

    `max_tokens` matters a lot for REASONING models (deepseek-v4-pro/R1 style):
    they burn output tokens on `reasoning_content` first, so a small cap (the
    gateway default was 2048) gets consumed by the thinking and leaves `content`
    EMPTY → json.loads("") → "Expecting value: line 1 column 1". Give the model
    room to think AND answer.
    """
    settings = get_settings()
    key = api_key or settings.deepseek_api_key
    if not key:
        raise DeepSeekError("未配置 API 密钥（请在 AI 页由管理员填入，或设环境变量 DEEPSEEK_API_KEY）")
    url = f"{(base_url or settings.deepseek_base_url).rstrip('/')}/chat/completions"
    payload = {
        "model": model or settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "stream": False,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise DeepSeekError(f"DeepSeek 请求失败：{exc}") from exc
    if resp.status_code != 200:
        raise DeepSeekError(f"DeepSeek 返回 HTTP {resp.status_code}：{resp.text[:300]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise DeepSeekError(f"AI 网关返回的不是 JSON：{resp.text[:200]}") from exc
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError) as exc:
        raise DeepSeekError(f"AI 返回结构异常：{str(body)[:300]}") from exc
    raw_usage = body.get("usage") or {}
    usage = {
        "prompt_tokens": int(raw_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(raw_usage.get("completion_tokens") or 0),
        "total_tokens": int(raw_usage.get("total_tokens") or 0),
    }
    content = message.get("content") or ""
    finish = choice.get("finish_reason")
    if not content.strip():
        # 推理模型把额度用在思考上，正文为空 —— 给出能照做的提示，别抛 json 解析错。
        if finish == "length" or message.get("reasoning_content"):
            raise DeepSeekError(
                f"模型「{payload['model']}」是推理模型，本次输出在思考阶段就被截断"
                f"（finish_reason={finish}，已用 {usage['completion_tokens']} tokens），"
                "正文为空。请减少本次生成数量，或在 AI 接口配置里换用非推理模型"
                "（如 deepseek-chat / deepseek-v4-flash）。"
            )
        raise DeepSeekError(f"AI 返回了空内容（finish_reason={finish}）")
    try:
        return json.loads(_extract_json(content)), usage
    except ValueError as exc:
        raise DeepSeekError(
            f"AI 响应不是合法 JSON（{exc}）。开头内容：{content[:200]}"
        ) from exc
