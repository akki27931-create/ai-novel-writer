"""LLM 客户端封装（OpenAI 兼容协议，支持多网关切换）。

- 统一入口：chat / chat_stream / chat_json / list_models
- 连接参数来自 providers 表（可配多个网关，随时切换），旧版单网关配置仍兼容
- 模型路由分三档：complex（拆书/规划）> text（正文）> cheap（状态抽取/摘要）
- 自动统计 input / output token，并写入 usage_records
- 关键点：API Key 只从网关配置 / 环境变量读取，绝不硬编码
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from ..settings_store import (
    CHEAP_TASKS,
    COMPLEX_TASKS,
    LEGACY_PLACEHOLDER_MODELS,
    estimate_cost,
    get_settings,
    resolve_llm_config,
)
from .usage import record_usage

logger = logging.getLogger(__name__)


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


def _client_for(
    db: Session, provider_id: int | None = None
) -> tuple[AsyncOpenAI, dict[str, Any], dict[str, Any]]:
    """返回 (客户端, 网关配置, 全局设置)。"""
    config = resolve_llm_config(db, provider_id)
    api_key = str(config.get("api_key") or "")
    if not api_key:
        raise LLMError(
            "未配置 LLM 网关的 API Key。请在「设置 → LLM 网关」新建或选择一个网关并填入 Key，"
            "或设置环境变量 DEEPSEEK_API_KEY。"
        )
    settings = get_settings(db, include_secrets=True)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=config.get("base_url") or "https://api.deepseek.com",
        # 显式设超时与重试次数：默认 600s × 2 次重试意味着上游异常时
        # 后台任务最长能"假死"半小时，而用户完全看不到任何反馈。
        timeout=float(settings.get("llm_timeout") or 600),
        max_retries=int(settings.get("llm_max_retries") or 0),
    )
    return client, config, settings


def _pick_model(
    settings: dict[str, Any], config: dict[str, Any], task: str, override: str | None
) -> str:
    """模型路由策略（三档）。

    - cheap      : 分章摘要、剧情状态抽取 → cheap_model（省钱，量最大）
    - complex    : 拆书、大纲推演、伏笔/矛盾检查 → reasoning_model（默认 Pro）
    - 其余正文类 : 续写、润色、重写 → text_model（默认 Flash）
    网关没配某一档时，逐级回退到 text_model，保证任何配置都能跑起来。
    """
    if override:
        return override
    text = str(config.get("text_model") or settings.get("text_model") or "")
    reasoning = str(config.get("reasoning_model") or settings.get("reasoning_model") or "")
    cheap = str(config.get("cheap_model") or settings.get("state_model") or "")
    if task in CHEAP_TASKS and cheap:
        return cheap
    if task in COMPLEX_TASKS:
        return reasoning or text
    # 一个都没配就返回空串，由调用方给出明确指引。
    # 这里**不要**猜一个名字（比如 deepseek-chat）——猜错只会换来一个难懂的 403。
    return text or reasoning


# 「怎么把模型名弄对」的统一指引，缺模型名和没权限两种情况共用
MODEL_FIX_HINT = (
    "解决办法：到「设置 → 模型路由」点「拉取可用模型」，"
    "再点红色提示里的「一键修正」，三档模型名会自动填成你账号真实可用的名字，最后「保存设置」。"
)

MISSING_MODEL_HINT = "还没有配置模型名。" + MODEL_FIX_HINT


def resolve_model(
    db: Session, task: str, override: str | None = None, provider_id: int | None = None
) -> str:
    """对外暴露的模型路由查询（用于前端展示“本次将使用哪个模型”）。"""
    config = resolve_llm_config(db, provider_id)
    return _pick_model(get_settings(db, include_secrets=True), config, task, override)


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
    provider_id: int | None = None,
) -> ChatResult:
    """一次性返回的对话调用。"""
    client, config, settings = _client_for(db, provider_id)
    chosen = _pick_model(settings, config, task, model)
    if not chosen:
        raise LLMError(MISSING_MODEL_HINT)
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
            f"输出 {output_tokens} tokens）。若这是一款推理模型，思考过程也会占用 max_tokens，"
            f"请在「设置 → 生成参数」把 max_tokens 调大（建议 32768）；"
            f"若换了新网关，请确认该模型名在网关侧真实存在。"
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
    provider_id: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """流式输出。产出 {"type": "delta", "text": ...} 与最终 {"type": "done", ...}。"""
    client, config, settings = _client_for(db, provider_id)
    chosen = _pick_model(settings, config, task, model)
    if not chosen:
        raise LLMError(MISSING_MODEL_HINT)
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
    provider_id: int | None = None,
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
        provider_id=provider_id,
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


async def list_models(db: Session, provider_id: int | None = None) -> list[dict[str, str]]:
    """列出当前网关账号下真实可用的模型名。"""
    client, _config, _settings = _client_for(db, provider_id)
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

    # 超时：这是后台任务"看起来卡住"的头号原因，必须说清楚在等什么、该怎么办
    lowered = message.lower()
    if isinstance(exc, TimeoutError) or "timed out" in lowered or "timeout" in lowered:
        return (
            "等待模型返回超时（已主动中断，避免任务无声卡死）。\n"
            "常见原因与处理：\n"
            "  · 用的是推理模型，一次要求输出的内容太多 —— 把「每批章节数」调小（默认 5）；\n"
            "  · 网关本身很慢或被限流 —— 换一个网关；\n"
            "  · 中转站上游不稳定（日志里常见 524）—— 重试一次，或改用官方直连；\n"
            "  · 确实需要更长时间 —— 到「设置 → 生成参数」把 llm_timeout 调大。"
        )

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

    # 403 且明确说了「no access to model」：Key 有效，只是无权用这个模型名。
    # 最常见的场景就是还在用仓库里那两个占位模型名。
    no_access = re.search(r"no access to model[:\s]*([^\s'\"()]+)", message)
    if no_access:
        name = no_access.group(1)
        legacy = " / ".join(sorted(LEGACY_PLACEHOLDER_MODELS))
        extra = (
            f"\n提示：「{name}」看起来是旧版本留下来的占位模型名（{legacy}），真实账号里通常不存在。"
            if name.lower() in LEGACY_PLACEHOLDER_MODELS
            else ""
        )
        return f"你的 API Key 没有模型「{name}」的权限。{extra}\n{MODEL_FIX_HINT}"

    if "401" in message or "invalid_api_key" in message.lower():
        return "API Key 无效或已过期，请在「设置 → LLM 网关」重新填写。"
    if "402" in message or "insufficient" in message.lower():
        return "账户余额不足（402），请为该网关充值或换一个网关。"
    if "403" in message:
        return (
            f"网关返回 403（拒绝访问）。Key 本身可能是有效的，但权限不足或被风控拦截。\n"
            f"先到「设置 → 模型路由」点「拉取可用模型」确认模型名对不对；"
            f"如果连模型列表都拿不到，就检查该 Key 的权限范围。\n原始信息：{message}"
        )
    if "404" in message or "model_not_found" in message.lower():
        return f"模型名不存在或账号无权限：{message}"
    if "429" in message:
        return "请求过于频繁（429），请稍后重试或降低并发。"
    return f"调用 LLM 网关失败：{message}"


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
