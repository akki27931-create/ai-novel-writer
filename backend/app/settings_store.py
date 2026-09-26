"""应用设置读写（持久化在 app_settings 表中）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from .config import env
from .models import AppSetting, Provider

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

# 旧版本曾经把这两个名字当默认值写进设置和网关。
# 它们是需求文档里的**占位名**，真实账号里通常不存在，照抄会在生成时收到
# 「403 no access to model」。升级上来的老库可能还存着它们，所以要能识别出来提醒。
LEGACY_PLACEHOLDER_MODELS = {"deepseek-v4.1-flash", "deepseek-v4.1-pro"}

DEFAULT_SETTINGS: dict[str, Any] = {
    # 兼容用的旧字段。真实生效的连接参数在 providers 表里（见 resolve_llm_config）。
    # 只保留 base_url / text_model / reasoning_model 作为「一个网关都没有」时的兜底。
    "api_key": "",                       # 留空则回退到环境变量 DEEPSEEK_API_KEY
    "base_url": "https://api.deepseek.com",
    # 模型名**故意留空**：各家网关的模型名都不一样，猜一个必然出错，
    # 不如空着让前端明确提示「去点拉取可用模型 + 一键修正」。
    "text_model": "",
    "reasoning_model": "",
    # 当前启用哪个网关（providers.id）；为 None 时用上面的旧字段
    "active_provider_id": None,
    # 生成参数
    "temperature": 0.85,
    "top_p": 0.95,
    # 注意：DeepSeek 的 flash / pro 都是推理模型，思考 token 也算在 max_tokens 里，
    # 所以这里要给足预算，否则输出会被截断（表现为“摘要失败 / JSON 解析失败”）。
    "max_tokens": 32768,
    # 单次 LLM 请求超时（秒）。不设上限的话，上游异常时后台任务会静默挂几十分钟，
    # 用户只看到进度条不动。宁可明确报超时，也不要无声卡死。
    "llm_timeout": 600,
    # 失败自动重试次数（不含首次）。SDK 默认 2 次，叠加长超时很容易变成"假死"。
    "llm_max_retries": 1,
    # 排章节计划时每次让模型输出几章：越小越快、越不容易被 max_tokens 截断
    "plan_batch_size": 5,
    # 检索参数
    "retrieval_top_k": 8,
    # 关键词必召回：把本场景涉及的人物名/地点名做字面召回，兜住语义检索漏掉的关键伏笔
    "keyword_recall_enabled": True,
    "keyword_recall_limit": 4,
    "embedding_model": "BAAI/bge-small-zh-v1.5",
    # ------------------------- 剧情状态（一致性核心） -------------------------
    # 保存章节后自动抽取本章剧情状态（人物在哪/在做什么/知道什么），走廉价模型
    "auto_extract_state": True,
    # 状态抽取使用的模型：留空 = 用当前网关的 cheap_model
    "state_model": "",
    # 上一章是否整章送入上下文（False 时只送末尾 previous_tail_chars 字）
    "previous_full_chapter": True,
    "previous_tail_chars": 3000,
    # 状态快照的字符预算
    "state_snapshot_budget": 6000,
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

# 模型路由的三档任务类型（与 llm._pick_model 约定一致）
COMPLEX_TASKS = {"analyze", "outline", "consistency", "plan"}
CHEAP_TASKS = {"summarize", "extract_state"}


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
    """真实 API Key：当前网关优先，其次环境变量，最后旧版 app_settings。"""
    config = resolve_llm_config(db)
    if config.get("api_key"):
        return str(config["api_key"])
    if env.deepseek_api_key:
        return env.deepseek_api_key
    row = db.get(AppSetting, "api_key")
    return str(row.value) if row and row.value else ""


# ----------------------------------------------------------------------
# LLM 网关（providers）
# ----------------------------------------------------------------------
def _mask_key(raw: str) -> str:
    raw = str(raw or "")
    if not raw:
        return ""
    return f"{raw[:6]}...{raw[-4:]}" if len(raw) > 12 else "***"


def provider_to_dict(row: Provider, *, include_secret: bool = False) -> dict[str, Any]:
    """序列化网关。默认脱敏，绝不把明文 key 发给前端。"""
    data: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "base_url": row.base_url,
        "text_model": row.text_model,
        "reasoning_model": row.reasoning_model,
        "cheap_model": row.cheap_model,
        "pricing": row.pricing or {},
        "note": row.note,
        "is_active": bool(row.is_active),
        "api_key_present": bool(row.api_key),
        "api_key_masked": _mask_key(row.api_key),
    }
    if include_secret:
        data["api_key"] = row.api_key
    return data


def list_providers(db: Session, *, include_secrets: bool = False) -> list[dict[str, Any]]:
    rows = db.query(Provider).order_by(Provider.id).all()
    return [provider_to_dict(row, include_secret=include_secrets) for row in rows]


def get_active_provider(db: Session) -> Provider | None:
    """优先取 app_settings.active_provider_id 指向的网关，其次取标记 is_active 的。"""
    active_id = _all_rows(db).get("active_provider_id")
    if active_id:
        row = db.get(Provider, int(active_id)) if str(active_id).isdigit() else None
        if row is not None:
            return row
    return db.query(Provider).filter(Provider.is_active.is_(True)).order_by(Provider.id).first()


def set_active_provider(db: Session, provider_id: int | None) -> dict[str, Any]:
    """切换当前网关，同时维护 is_active 标记，保证两者永远一致。"""
    if provider_id is not None:
        target = db.get(Provider, int(provider_id))
        if target is None:
            raise ValueError(f"网关 {provider_id} 不存在")
        for row in db.query(Provider).all():
            row.is_active = row.id == target.id
    else:
        for row in db.query(Provider).all():
            row.is_active = False
    _write_row(db, "active_provider_id", provider_id)
    db.commit()
    return get_settings(db)


def upsert_provider(db: Session, payload: dict[str, Any]) -> Provider:
    """新建或更新网关。api_key 传空字符串 = 不改动，传 None = 清空。"""
    provider_id = payload.get("id")
    row = db.get(Provider, int(provider_id)) if provider_id else None
    if row is None:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("请填写网关名称")
        if db.query(Provider).filter(Provider.name == name).first() is not None:
            raise ValueError(f"网关名称「{name}」已存在")
        row = Provider(name=name)
        db.add(row)
    elif payload.get("name"):
        row.name = str(payload["name"]).strip()

    for field in ("base_url", "text_model", "reasoning_model", "cheap_model", "note"):
        if field in payload and payload[field] is not None:
            setattr(row, field, str(payload[field]).strip())

    if "pricing" in payload and payload["pricing"] is not None:
        row.pricing = payload["pricing"]

    if "api_key" in payload:
        value = payload["api_key"]
        if value is None:
            row.api_key = ""
        elif str(value).strip():
            row.api_key = str(value).strip()   # 空字符串 = 前端未修改

    db.flush()

    # 第一个网关自动激活，避免用户还要多点一次
    if not row.is_active and db.query(Provider).filter(Provider.is_active.is_(True)).count() == 0:
        row.is_active = True
        _write_row(db, "active_provider_id", row.id)

    if payload.get("is_active"):
        set_active_provider(db, row.id)

    db.commit()
    db.refresh(row)
    return row


def create_default_provider(db: Session) -> Provider | None:
    """一个网关都没有时，按环境变量 / 旧版配置建一个，保证开箱即用。

    启动时和迁移脚本都会调用它：这样新用户只要在 .env 里填了 DEEPSEEK_API_KEY，
    设置页第一眼就能看到可用网关，不用先去理解「旧版单网关兜底」是什么。
    """
    if db.query(Provider).count() > 0:
        return None
    settings = get_settings(db, include_secrets=True)
    base_url = env.deepseek_base_url or str(settings.get("base_url") or DEFAULT_SETTINGS["base_url"])
    api_key = env.deepseek_api_key or str(settings.get("api_key") or "")

    def _clean(key: str) -> str:
        """旧库里可能存着占位模型名，别把它抄进新网关，否则一上手就 403。"""
        name = str(settings.get(key) or "")
        return "" if name.lower() in LEGACY_PLACEHOLDER_MODELS else name

    text_model = _clean("text_model")
    host = base_url.replace("https://", "").replace("http://", "").split("/")[0]
    row = Provider(
        name=host or "default",
        base_url=base_url,
        api_key=api_key,
        text_model=text_model,
        reasoning_model=_clean("reasoning_model"),
        cheap_model=text_model,
        note="由环境变量 / 旧版配置自动创建；模型名请用「拉取可用模型」+「一键修正」补全",
        is_active=True,
    )
    db.add(row)
    db.flush()
    _write_row(db, "active_provider_id", row.id)
    db.commit()
    db.refresh(row)
    return row


def delete_provider(db: Session, provider_id: int) -> None:
    row = db.get(Provider, provider_id)
    if row is None:
        return
    was_active = bool(row.is_active)
    db.delete(row)
    db.flush()
    if was_active:
        replacement = db.query(Provider).order_by(Provider.id).first()
        set_active_provider(db, replacement.id if replacement else None)
    else:
        db.commit()


def resolve_llm_config(db: Session, provider_id: int | None = None) -> dict[str, Any]:
    """解析出本次调用真正要用的连接参数（含明文 key），供 llm.py 使用。

    优先级：指定 gateway > 当前激活 gateway > 旧版单网关设置 > 环境变量。
    """
    row = db.get(Provider, int(provider_id)) if provider_id else get_active_provider(db)
    if row is not None:
        config = {
            "provider_id": row.id,
            "provider_name": row.name,
            "base_url": row.base_url or DEFAULT_SETTINGS["base_url"],
            "api_key": row.api_key,
            "text_model": row.text_model,
            "reasoning_model": row.reasoning_model,
            "cheap_model": row.cheap_model,
            "pricing": row.pricing or {},
        }
        # 网关缺项时逐级兜底：环境变量 → 旧版单网关设置。
        # 这条兜底很关键：用户很可能把 Key 填在「旧版单网关」那一栏，
        # 而自动创建的网关里是空的，不兜底就会莫名其妙报「未配置 API Key」。
        legacy = get_settings(db, include_secrets=True)
        if not config["api_key"]:
            config["api_key"] = env.deepseek_api_key or str(legacy.get("api_key") or "")
        if not row.base_url:
            config["base_url"] = env.deepseek_base_url or str(
                legacy.get("base_url") or DEFAULT_SETTINGS["base_url"]
            )
        for key in ("text_model", "reasoning_model", "cheap_model"):
            if not config.get(key):
                config[key] = str(legacy.get(key) or "")
        if not config["cheap_model"]:
            config["cheap_model"] = config["text_model"]
        return config

    settings = get_settings(db, include_secrets=True)
    return {
        "provider_id": None,
        "provider_name": "（旧版单网关）",
        "base_url": env.deepseek_base_url or str(settings.get("base_url") or DEFAULT_SETTINGS["base_url"]),
        "api_key": env.deepseek_api_key or str(settings.get("api_key") or ""),
        "text_model": str(settings.get("text_model") or ""),
        "reasoning_model": str(settings.get("reasoning_model") or ""),
        "cheap_model": "",
        "pricing": {},
    }


def touch_provider_models(db: Session, provider_id: int | None, patch: dict[str, str]) -> None:
    """把「一键修正模型名」的结果写回当前网关。"""
    row = db.get(Provider, int(provider_id)) if provider_id else get_active_provider(db)
    if row is None:
        update_settings(db, patch)
        return
    # 三档都要写：漏掉 cheap_model 会导致「一键修正」之后状态抽取仍然报模型不存在
    for key in ("text_model", "reasoning_model", "cheap_model"):
        if patch.get(key):
            setattr(row, key, patch[key])
    db.commit()


def placeholder_model_warnings(config: dict[str, Any]) -> list[str]:
    """找出还在用仓库默认「占位」模型名的档位。

    DEFAULT_SETTINGS 里的模型名是按需求文档写的占位值，真实账号里通常不存在，
    照抄就会在生成时收到 403 / 模型不存在。启动时提醒一次，比等到报错强。
    """
    labels = {
        "text_model": "正文模型",
        "reasoning_model": "复杂任务模型",
        "cheap_model": "廉价模型",
    }
    found: list[str] = []
    for key, label in labels.items():
        name = str(config.get(key) or "")
        if name and name.lower() in LEGACY_PLACEHOLDER_MODELS:
            found.append(f"{label}={name}")
    return found


def missing_model_warnings(config: dict[str, Any]) -> list[str]:
    """哪些档位一个模型名都没有。这三档都空就没法生成，得让用户去配。"""
    labels = {
        "text_model": "正文模型",
        "reasoning_model": "复杂任务模型",
        "cheap_model": "廉价模型",
    }
    return [label for key, label in labels.items() if not str(config.get(key) or "")]


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
        _write_row(db, key, value)
    db.commit()
    return get_settings(db)


def _write_row(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.flush()


def _delete_row(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row is not None:
        db.delete(row)
        db.flush()


def estimate_cost(model: str, input_tokens: int, output_tokens: int, db: Session) -> tuple[float, float]:
    """返回 (预估美元, 预估人民币)。按非缓存输入价估算（偏保守）。"""
    settings = get_settings(db, include_secrets=True)
    pricing: dict[str, dict[str, float]] = settings.get("pricing") or DEFAULT_PRICING
    # 当前网关自己配了价格就优先用它（换非 DeepSeek 供应商时价格差异很大）
    provider_pricing = resolve_llm_config(db).get("pricing") or {}
    if provider_pricing:
        pricing = {**pricing, **provider_pricing}
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
