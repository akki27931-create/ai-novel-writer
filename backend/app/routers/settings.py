"""路由：设置、提示词、模型列表、用量统计。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import env
from ..database import get_db
from ..prompts import BUILTIN_PROMPTS
from ..schemas import (
    ModelInfo,
    ProviderIn,
    ProviderListOut,
    ProviderOut,
    SettingsOut,
    SettingsUpdate,
    UsageSummary,
)
from ..services.indexing import reset_store_cache
from ..services.llm import LLMError, list_models
from ..services.usage import summarize_usage
from ..settings_store import (
    delete_provider,
    get_settings,
    list_providers,
    provider_to_dict,
    resolve_llm_config,
    set_active_provider,
    touch_provider_models,
    update_settings,
    upsert_provider,
)

router = APIRouter(prefix="/api", tags=["settings"])


def _settings_out(db: Session) -> SettingsOut:
    settings = get_settings(db)
    providers = list_providers(db)
    active = next((p for p in providers if p["is_active"]), None)
    return SettingsOut(
        settings=settings,
        candidate_models=env.candidate_model_list,
        providers=providers,
        active_provider=active,
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return _settings_out(db)


@router.put("/settings", response_model=SettingsOut)
def write_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
    before = get_settings(db, include_secrets=True)
    patch = payload.model_dump(exclude_unset=True)

    # active_provider_id 单独处理：它要同时维护 providers.is_active 标记
    active_id = patch.pop("active_provider_id", "__unset__")
    update_settings(db, patch)
    if active_id != "__unset__":
        try:
            set_active_provider(db, active_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    after = get_settings(db, include_secrets=True)
    if before.get("embedding_model") != after.get("embedding_model"):
        # 换了向量模型，旧的向量库维度和语义都不可用了
        reset_store_cache()

    return _settings_out(db)


# ----------------------------------------------------------------------
# LLM 网关（多网关可切换）
# ----------------------------------------------------------------------
@router.get("/providers", response_model=ProviderListOut)
def get_providers(db: Session = Depends(get_db)) -> ProviderListOut:
    """列出所有网关（API Key 只返回脱敏值）。"""
    providers = list_providers(db)
    config = resolve_llm_config(db)
    resolved = {
        "provider_id": config.get("provider_id"),
        "provider_name": config.get("provider_name"),
        "base_url": config.get("base_url"),
        "text_model": config.get("text_model"),
        "reasoning_model": config.get("reasoning_model"),
        "cheap_model": config.get("cheap_model"),
        "api_key_present": bool(config.get("api_key")),
    }
    return ProviderListOut(
        providers=[ProviderOut(**p) for p in providers],
        active_provider_id=next((p["id"] for p in providers if p["is_active"]), None),
        resolved=resolved,
    )


@router.post("/providers", response_model=ProviderOut)
def create_provider(payload: ProviderIn, db: Session = Depends(get_db)) -> ProviderOut:
    try:
        row = upsert_provider(db, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProviderOut(**provider_to_dict(row))


@router.put("/providers/{provider_id}", response_model=ProviderOut)
def edit_provider(
    provider_id: int, payload: ProviderIn, db: Session = Depends(get_db)
) -> ProviderOut:
    data = payload.model_dump(exclude_unset=True)
    data["id"] = provider_id
    try:
        row = upsert_provider(db, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProviderOut(**provider_to_dict(row))


@router.post("/providers/{provider_id}/activate", response_model=ProviderListOut)
def activate_provider(provider_id: int, db: Session = Depends(get_db)) -> ProviderListOut:
    try:
        set_active_provider(db, provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return get_providers(db)


@router.delete("/providers/{provider_id}", response_model=ProviderListOut)
def remove_provider(provider_id: int, db: Session = Depends(get_db)) -> ProviderListOut:
    delete_provider(db, provider_id)
    return get_providers(db)


@router.get("/settings/models", response_model=list[ModelInfo])
async def available_models(
    provider_id: int | None = None, db: Session = Depends(get_db)
) -> list[ModelInfo]:
    """调用网关的 /models 接口，列出该账号真实可用的模型名。"""
    try:
        models = await list_models(db, provider_id)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [ModelInfo(**item) for item in models]


@router.get("/settings/validate")
async def validate_models(
    provider_id: int | None = None, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """校验当前配置的三档模型名是否真的存在于网关里，并给出修正建议。"""
    config = resolve_llm_config(db, provider_id)
    try:
        models = await list_models(db, provider_id)
    except LLMError as exc:
        return {"ok": False, "error": str(exc), "models": [], "issues": [], "suggestion": {}}

    ids = [item["id"] for item in models]
    issues: list[dict[str, Any]] = []
    for key, label in (
        ("text_model", "正文模型"),
        ("reasoning_model", "复杂任务模型"),
        ("cheap_model", "廉价模型（状态抽取/摘要）"),
    ):
        configured = str(config.get(key) or "")
        if not configured:
            # 没配也要报出来，否则页面没有红色提示，用户不知道要去点「一键修正」
            issues.append({"field": key, "label": label, "configured": "", "missing": True})
        elif configured not in ids:
            issues.append({"field": key, "label": label, "configured": configured, "missing": False})

    # 建议：带 pro / reason / r1 的更适合做复杂任务模型；带 flash / mini / small 的更适合做廉价模型
    def looks_reasoning(name: str) -> bool:
        lowered = name.lower()
        return any(token in lowered for token in ("pro", "reason", "r1", "max", "ultra"))

    def looks_cheap(name: str) -> bool:
        lowered = name.lower()
        return any(token in lowered for token in ("flash", "mini", "small", "lite", "turbo"))

    reasoning_guess = next((m for m in ids if looks_reasoning(m)), ids[-1] if ids else "")
    cheap_guess = next((m for m in ids if looks_cheap(m)), reasoning_guess)
    text_guess = next((m for m in ids if m not in {reasoning_guess, cheap_guess}), reasoning_guess)

    return {
        "ok": not issues,
        "models": ids,
        "issues": issues,
        "suggestion": {
            "text_model": text_guess,
            "reasoning_model": reasoning_guess,
            "cheap_model": cheap_guess,
        },
    }


@router.post("/settings/autofix-models")
async def autofix_models(
    provider_id: int | None = None, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """把不存在的模型名一键改成网关真实可用的模型。"""
    result = await validate_models(provider_id, db)
    if not result.get("models"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "无法获取可用模型，请先选择网关并填写有效的 API Key",
        )
    suggested = result.get("suggestion") or {}
    wrong_fields = {issue["field"] for issue in result.get("issues", [])}
    patch: dict[str, Any] = {}
    for field in ("text_model", "reasoning_model", "cheap_model"):
        if field in wrong_fields and suggested.get(field):
            patch[field] = suggested[field]
    if patch:
        touch_provider_models(db, provider_id, patch)
    return {"applied": patch, "settings": get_settings(db), "providers": list_providers(db)}


# ----------------------------------------------------------------------
# 提示词
# ----------------------------------------------------------------------
@router.get("/prompts")
def read_prompts(db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings(db, include_secrets=True)
    overrides: dict[str, str] = settings.get("prompt_overrides") or {}
    items = []
    for key, meta in BUILTIN_PROMPTS.items():
        custom = overrides.get(key) or ""
        items.append(
            {
                "key": key,
                "name": meta["name"],
                "description": meta["description"],
                "template": custom or meta["template"],
                "builtin": meta["template"],
                "is_custom": bool(custom),
            }
        )
    return {"items": items}


@router.put("/prompts/{key}")
def update_prompt(key: str, payload: dict[str, str], db: Session = Depends(get_db)) -> dict[str, Any]:
    if key not in BUILTIN_PROMPTS:
        raise HTTPException(status_code=404, detail=f"未知提示词：{key}")
    template = str(payload.get("template") or "").strip()
    settings = get_settings(db, include_secrets=True)
    overrides = dict(settings.get("prompt_overrides") or {})
    if template and template != BUILTIN_PROMPTS[key]["template"]:
        overrides[key] = template
    else:
        overrides.pop(key, None)  # 与内置一致 / 传空 => 恢复内置
    update_settings(db, {"prompt_overrides": overrides})
    return {"key": key, "is_custom": overrides.get(key) is not None}


@router.delete("/prompts/{key}")
def reset_prompt(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if key not in BUILTIN_PROMPTS:
        raise HTTPException(status_code=404, detail=f"未知提示词：{key}")
    settings = get_settings(db, include_secrets=True)
    overrides = dict(settings.get("prompt_overrides") or {})
    overrides.pop(key, None)
    update_settings(db, {"prompt_overrides": overrides})
    return {"key": key, "is_custom": False}


# ----------------------------------------------------------------------
# 用量与费用
# ----------------------------------------------------------------------
@router.get("/usage", response_model=UsageSummary)
def usage(limit: int = 20, db: Session = Depends(get_db)) -> UsageSummary:
    return UsageSummary(**summarize_usage(db, limit=max(1, min(limit, 200))))
