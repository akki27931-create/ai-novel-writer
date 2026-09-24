"""路由：设置、提示词、模型列表、用量统计。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import env
from ..database import get_db
from ..prompts import BUILTIN_PROMPTS
from ..schemas import ModelInfo, SettingsOut, SettingsUpdate, UsageSummary
from ..services.indexing import reset_store_cache
from ..services.llm import LLMError, list_models
from ..services.usage import summarize_usage
from ..settings_store import get_settings, update_settings

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return SettingsOut(
        settings=get_settings(db),
        candidate_models=env.candidate_model_list,
    )


@router.put("/settings", response_model=SettingsOut)
def write_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
    before = get_settings(db, include_secrets=True)
    patch = payload.model_dump(exclude_unset=True)
    update_settings(db, patch)

    after = get_settings(db, include_secrets=True)
    if before.get("embedding_model") != after.get("embedding_model"):
        # 换了向量模型，旧的向量库维度和语义都不可用了
        reset_store_cache()

    return SettingsOut(settings=get_settings(db), candidate_models=env.candidate_model_list)


@router.get("/settings/models", response_model=list[ModelInfo])
async def available_models(db: Session = Depends(get_db)) -> list[ModelInfo]:
    """调用 DeepSeek /models 接口，列出账号真实可用的模型名。"""
    try:
        models = await list_models(db)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [ModelInfo(**item) for item in models]


@router.get("/settings/validate")
async def validate_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    """校验当前配置的两个模型名是否真的存在于账号里，并给出修正建议。"""
    settings = get_settings(db, include_secrets=True)
    try:
        models = await list_models(db)
    except LLMError as exc:
        return {"ok": False, "error": str(exc), "models": [], "issues": [], "suggestion": {}}

    ids = [item["id"] for item in models]
    issues: list[dict[str, str]] = []
    for key, label in (("text_model", "正文模型"), ("reasoning_model", "复杂任务模型")):
        configured = str(settings.get(key) or "")
        if configured and configured not in ids:
            issues.append({"field": key, "label": label, "configured": configured})

    # 建议：带 pro / reason / r1 的更适合做复杂任务模型
    def looks_reasoning(name: str) -> bool:
        lowered = name.lower()
        return any(token in lowered for token in ("pro", "reason", "r1", "max"))

    reasoning_guess = next((m for m in ids if looks_reasoning(m)), ids[-1] if ids else "")
    text_guess = next((m for m in ids if m != reasoning_guess), reasoning_guess)

    return {
        "ok": not issues,
        "models": ids,
        "issues": issues,
        "suggestion": {"text_model": text_guess, "reasoning_model": reasoning_guess},
    }


@router.post("/settings/autofix-models")
async def autofix_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    """把不存在的模型名一键改成账号真实可用的模型。"""
    result = await validate_models(db)
    if not result.get("models"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "无法获取可用模型，请先在设置页填写有效的 API Key",
        )
    suggested = result.get("suggestion") or {}
    wrong_fields = {issue["field"] for issue in result.get("issues", [])}
    patch: dict[str, Any] = {}
    if "text_model" in wrong_fields and suggested.get("text_model"):
        patch["text_model"] = suggested["text_model"]
    if "reasoning_model" in wrong_fields and suggested.get("reasoning_model"):
        patch["reasoning_model"] = suggested["reasoning_model"]
    if patch:
        update_settings(db, patch)
    return {"applied": patch, "settings": get_settings(db)}


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
