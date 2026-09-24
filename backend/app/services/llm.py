"""DeepSeek（OpenAI 兼容）客户端封装。

- 统一入口：chat / chat_stream / chat_json / list_models
- 自动统计 input / output token，并写入 usage_records
- 关键点：API Key 从环境变量或设置表读取，绝不硬编码
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from ..settings_store import estimate_cost, get_api_key, get_settings
from .usage import record_usage


class LLMError(RuntimeError):
    """LLM 调用相关错误。"""


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    truncated: bool = False
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _client_for(db: Session) -> tuple[AsyncOpenAI, dict[str, Any]]:
    settings = get_settings(db, include_secrets=True)
    api_key = get_api_key(db)
    if not api_key:
        raise LLMError(
            "未配置 DeepSeek API Key。请在“设置页”填写，或设置环境变量 DEEPSEEK_API_KEY。"
        )
    client = AsyncOpenAI(api_key=api_key, base_url=settings.get("base_url") or "https://api.deepseek.com")
    return client, settings


def _pick_model(settings: dict[str, Any], task: str, override: str | None) -> str:
    """模型路由策略。

    - 复杂任务（拆书、大纲推演、伏笔/矛盾检查）→ reasoning_model（默认 Pro）
    - 正文类任务（续写、扩写、润色、摘要）→ text_model（默认 Flash）
    """
    if override:
        return override
    complex_tasks = {"analyze", "outline", "consistency"}
    if task in complex_tasks:
        return str(settings.get("reasoning_model") or settings.get("text_model") or "deepseek-chat")
    return str(settings.get("text_model") or "deepseek-chat")


def resolve_model(db: Session, task: str, override: str | None = None) -> str:
    """对外暴露的模型路由查询（用于前端展示“本次将使用哪个模型”）。"""
    settings = get_settings(db, include_secrets=True)
    return _pick_model(settings, task, override)


def _estimate_tokens(text: str) -> int:
    """无 usage 返回时的粗略估算：中文约 1.5 字/token。"""
    return max(1, int(len(text or "") / 1.5))


async def chat(
    db: Session,
    *,
    messages: list[dict[str, str]],
    task: str,
    model: str | None = None,
    book_id: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> ChatResult:
    """一次性返回的对话调用。"""
    client, settings = _client_for(db)
    chosen = _pick_model(settings, task, model)
    started = time.perf_counter()
    kwargs: dict[str, Any] = {
        "model": chosen,
        "messages": messages,
        "temperature": settings["temperature"] if temperature is None else temperature,
        "max_tokens": int(settings["max_tokens"] if max_tokens is None else max_tokens),
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LLMError(_friendly_error(exc)) from exc

    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "") if choice else ""
    finish_reason = str(getattr(choice, "finish_reason", "") or "")
    usage = response.usage
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) or _estimate_tokens(
        "".join(m.get("content", "") for m in messages)
    )
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) or _estimate_tokens(text)

    # 先记账：无论结果是否可用，这些 token 都已经消耗了
    _record(db, book_id, task, chosen, input_tokens, output_tokens)

    truncated = finish_reason == "length"
    if truncated:
        logger.warning(
            "模型 %s 输出被 max_tokens=%s 截断（task=%s, reasoning 也占用输出预算）",
            chosen,
            kwargs["max_tokens"],
            task,
        )
    if not text.strip():
        # 典型情况：输出预算被思考过程吃光，正文为空
        raise LLMError(
            f"模型 {chosen} 没有返回正文内容（finish_reason={finish_reason or '未知'}，"
            f"输出 {output_tokens} tokens）。该模型是推理模型，思考过程也占用 max_tokens，"
            f"请在设置页把 max_tokens 调大（建议 32768）。"
        )

    return ChatResult(
        text=text,
        model=chosen,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=int((time.perf_counter() - started) * 1000),
        truncated=truncated,
        finish_reason=finish_reason,
    )


async def chat_stream(
    db: Session,
    *,
    messages: list[dict[str, str]],
    task: str,
    model: str | None = None,
    book_id: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """流式输出。产出 {"type": "delta", "text": ...} 与最终 {"type": "done", ...}。"""
    client, settings = _client_for(db)
    chosen = _pick_model(settings, task, model)
    started = time.perf_counter()

    base_kwargs: dict[str, Any] = {
        "model": chosen,
        "messages": messages,
        "temperature": settings["temperature"] if temperature is None else temperature,
        "max_tokens": int(settings["max_tokens"] if max_tokens is None else max_tokens),
        "stream": True,
    }

    # DeepSeek 支持 stream_options.include_usage；个别兼容网关不支持，失败时自动降级重试
    try:
        stream = await client.chat.completions.create(
            **base_kwargs, stream_options={"include_usage": True}
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "stream_options" not in message and "unknown" not in message and "invalid" not in message:
            raise LLMError(_friendly_error(exc)) from exc
        logger.warning("stream_options 不被支持，改为不带 usage 的流式请求：%s", exc)
        try:
            stream = await client.chat.completions.create(**base_kwargs)
        except Exception as retry_exc:  # noqa: BLE001
            raise LLMError(_friendly_error(retry_exc)) from retry_exc

    buffer: list[str] = []
    input_tokens = output_tokens = 0
    finish_reason = ""
    try:
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            if chunk.choices:
                if getattr(chunk.choices[0], "finish_reason", None):
                    finish_reason = str(chunk.choices[0].finish_reason)
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    buffer.append(piece)
                    yield {"type": "delta", "text": piece}
    except Exception as exc:  # noqa: BLE001
        raise LLMError(_friendly_error(exc)) from exc

    text = "".join(buffer)
    if not input_tokens:
        input_tokens = _estimate_tokens("".join(m.get("content", "") for m in messages))
    if not output_tokens:
        output_tokens = _estimate_tokens(text)

    _record(db, book_id, task, chosen, input_tokens, output_tokens)
    yield {
        "type": "done",
        "text": text,
        "model": chosen,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "truncated": finish_reason == "length",
        "finish_reason": finish_reason,
    }


async def chat_json(
    db: Session,
    *,
    messages: list[dict[str, str]],
    task: str,
    model: str | None = None,
    book_id: int | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> tuple[Any, ChatResult]:
    """要求模型输出 JSON，并尽最大努力解析。"""
    result = await chat(
        db,
        messages=messages,
        task=task,
        model=model,
        book_id=book_id,
        max_tokens=max_tokens,
        temperature=0.3 if temperature is None else temperature,
        json_mode=True,
    )
    if result.truncated:
        settings = get_settings(db, include_secrets=True)
        raise LLMError(
            f"模型输出达到 max_tokens={settings.get('max_tokens')} 被截断，JSON 不完整，无法解析。"
            f"请到「设置 → 生成参数」把 max_tokens 调大（建议 32768），"
            f"或拆书时把「每批章节数」改小（建议 3~5）。"
            f"（本次已消耗 input={result.input_tokens} / output={result.output_tokens} tokens）"
        )
    return parse_json(result.text), result


async def list_models(db: Session) -> list[dict[str, str]]:
    client, _ = _client_for(db)
    try:
        response = await client.models.list()
    except Exception as exc:  # noqa: BLE001
        raise LLMError(_friendly_error(exc)) from exc
    return [
        {"id": item.id, "owned_by": getattr(item, "owned_by", "") or ""}
        for item in getattr(response, "data", [])
    ]


# ----------------------------------------------------------------------
def parse_json(text: str) -> Any:
    """从模型输出中提取 JSON：兼容 ```json 代码块、前后废话、尾随逗号。"""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    for candidate in (text, _slice_braces(text), _slice_brackets(text)):
        if not candidate:
            continue
        for attempt in (candidate, _repair(candidate)):
            if attempt is None:
                continue
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise LLMError(f"模型未返回合法 JSON，原始输出片段：{text[:300]}")


def _slice_braces(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else None


def _slice_brackets(text: str) -> str | None:
    start, end = text.find("["), text.rfind("]")
    return text[start : end + 1] if start != -1 and end > start else None


def _repair(text: str) -> str | None:
    """修复常见 JSON 瑕疵：尾随逗号、中文引号。"""
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    fixed = fixed.replace("“", '"').replace("”", '"').replace("，\n", ",\n")
    return fixed if fixed != text else None


def _friendly_error(exc: Exception) -> str:
    message = str(exc)

    # 模型名不存在：把账号真实可用的模型名提取出来，直接告诉用户怎么改
    match = re.search(
        r"The supported API model names are (.+?),\s*but you passed (.+?)[\.\n]", message
    )
    if match:
        return (
            f"模型名不存在。你填的是「{match.group(2).strip()}」，"
            f"但你的账号只支持：{match.group(1).strip()}。\n"
            f"请到「设置 → 模型路由」改成上述可用模型名，再点「拉取可用模型」确认。"
        )

    if "401" in message or "invalid_api_key" in message.lower():
        return "API Key 无效或已过期，请在设置页重新填写。"
    if "402" in message or "insufficient" in message.lower():
        return "DeepSeek 账户余额不足。"
    if "404" in message or "model_not_found" in message.lower():
        return f"模型名不存在或账号无权限：{message}"
    if "429" in message:
        return "请求过于频繁（429），请稍后重试。"
    return f"调用 DeepSeek 失败：{message}"


def _record(db: Session, book_id: int | None, task: str, model: str, i: int, o: int) -> None:
    cost_usd, cost_cny = estimate_cost(model, i, o, db)
    record_usage(
        db,
        book_id=book_id,
        task_type=task,
        model=model,
        input_tokens=i,
        output_tokens=o,
        cost_usd=cost_usd,
        cost_cny=cost_cny,
    )
