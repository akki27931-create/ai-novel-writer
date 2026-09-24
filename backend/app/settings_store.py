"""应用设置读写（持久化在 app_settings 表中）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from .config import env
from .models import AppSetting

# 每百万 token 的美元单价（估算用，用户可在设置页修改）
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # 当前账号实际可用的模型（2026-09 实测）
    "deepseek-flash": {"input": 0.27, "cached_input": 0.07, "output": 1.10},
    "deepseek-v4-pro": {"input": 0.55, "cached_input": 0.14, "output": 2.19},
    # 常见别名 / 历史名称
    "deepseek-v4.1-flash": {"input": 0.27, "cached_input": 0.07, "output": 1.10},
    "deepseek-v4.1-pro": {"input": 0.55, "cached_input": 0.14, "output": 2.19},
    "deepseek-chat": {"input": 0.27, "cached_input": 0.07, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "cached_input": 0.14, "output": 2.19},
    # 兜底：名字里含 pro/reason/r1 的按“强模型”估价，其余按“普通模型”估价
    "default": {"input": 0.27, "cached_input": 0.07, "output": 1.10},
    "default_pro": {"input": 0.55, "cached_input": 0.14, "output": 2.19},
}

# 判断一个模型名是否属于“更贵的强模型”
_STRONG_MODEL_TOKENS = ("pro", "reason", "r1", "max", "ultra")

DEFAULT_SETTINGS: dict[str, Any] = {
    # DeepSeek 连接
    "api_key": "",                       # 留空则回退到环境变量 DEEPSEEK_API_KEY
    "base_url": "https://api.deepseek.com",
    # 模型路由：正文类任务默认用 Flash，复杂任务默认用 Pro
    "text_model": "deepseek-v4.1-flash",
    "reasoning_model": "deepseek-v4.1-pro",
    # 生成参数
    "temperature": 0.85,
    "top_p": 0.95,
    # 注意：DeepSeek 的 flash / pro 都是推理模型，思考 token 也算在 max_tokens 里，
    # 所以这里要给足预算，否则输出会被截断（表现为“摘要失败 / JSON 解析失败”）。
    "max_tokens": 32768,
    # 检索参数
    "retrieval_top_k": 8,
    "embedding_model": "BAAI/bge-small-zh-v1.5",
    # 费用换算
    "usd_to_cny": 7.2,
    "pricing": DEFAULT_PRICING,
    # 番茄 MCP（留空则用环境变量 FANQIE_MCP_COMMAND）
    "fanqie_mcp_command": "",
    # 番茄 MCP 依赖的第三方数据接口地址（留空则用 MCP 内置默认值）
    "fanqie_api_base": "",
    # 提示词覆盖：{prompt_key: "自定义内容"}，为空表示使用项目内置模板
    "prompt_overrides": {},
}

_SECRET_KEYS = {"api_key"}


def _all_rows(db: Session) -> dict[str, Any]:
    return {row.key: row.value for row in db.query(AppSetting).all()}


def get_settings(db: Session, *, include_secrets: bool = False) -> dict[str, Any]:
    """读取设置：默认值 <- 数据库 <- 环境变量（环境变量优先级最高）。"""
    merged = deepcopy(DEFAULT_SETTINGS)
    for key, value in _all_rows(db).items():
        if key in merged and value is not None:
            if isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value

    # 环境变量覆盖
    if env.deepseek_base_url:
        merged["base_url"] = env.deepseek_base_url
    if env.deepseek_api_key:
        merged["api_key"] = env.deepseek_api_key
    if env.fanqie_mcp_command:
        merged["fanqie_mcp_command"] = env.fanqie_mcp_command
    if env.fanqie_api_base:
        merged["fanqie_api_base"] = env.fanqie_api_base

    if not include_secrets:
        merged = _mask_secrets(merged)
    return merged


def _mask_secrets(data: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(data)
    for key in _SECRET_KEYS:
        raw = str(data.get(key) or "")
        if raw:
            data[key] = f"{raw[:6]}...{raw[-4:]}" if len(raw) > 12 else "***"
        data[f"{key}_present"] = bool(raw)
        data[f"{key}_source"] = "env" if env.deepseek_api_key else ("db" if raw else "none")
        data.pop(key, None)
    return data


def get_api_key(db: Session) -> str:
    """真实 API Key：环境变量优先，其次数据库。"""
    if env.deepseek_api_key:
        return env.deepseek_api_key
    row = db.get(AppSetting, "api_key")
    return str(row.value) if row and row.value else ""


def update_settings(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    """局部更新设置。api_key 传空字符串表示“不改动”，传 null 表示“清空”。"""
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "api_key":
            if value is None:
                _delete_row(db, key)
                continue
            value = str(value).strip()
            if not value:
                continue  # 空字符串 = 前端未修改
        if key == "pricing" and isinstance(value, dict):
            merged = deepcopy(DEFAULT_PRICING)
            for model, price in value.items():
                merged.setdefault(model, {})
                merged[model].update(price or {})
            value = merged
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()
    return get_settings(db)


def _delete_row(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row is not None:
        db.delete(row)
        db.flush()


def estimate_cost(model: str, input_tokens: int, output_tokens: int, db: Session) -> tuple[float, float]:
    """返回 (预估美元, 预估人民币)。按非缓存输入价估算（偏保守）。"""
    settings = get_settings(db, include_secrets=True)
    pricing: dict[str, dict[str, float]] = settings.get("pricing") or DEFAULT_PRICING
    price = pricing.get(model)
    if not price:
        # 未知模型名：按名字特征猜一档，避免费用显示成 0
        lowered = (model or "").lower()
        strong = any(token in lowered for token in _STRONG_MODEL_TOKENS)
        price = pricing.get("default_pro" if strong else "default") or DEFAULT_PRICING["default"]
    cost_usd = (
        input_tokens / 1_000_000 * float(price.get("input", 0.27))
        + output_tokens / 1_000_000 * float(price.get("output", 1.10))
    )
    rate = float(settings.get("usd_to_cny") or 7.2)
    return round(cost_usd, 6), round(cost_usd * rate, 6)
