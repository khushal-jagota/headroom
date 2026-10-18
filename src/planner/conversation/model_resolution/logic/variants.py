"""Explicit provider families and numerical version ordering, without provider I/O."""

from __future__ import annotations

import re

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import CachedModelResolution, ModelVariant
from planner.core.contracts import ErrorCode, PlannerError

_CODEX_VARIANTS = frozenset({"sol", "astra", "luna", "codex", "mini", "nano", "pro"})
_CODEX_VERSION = re.compile(r"^gpt-(\d+(?:\.\d+)*)(?:-(sol|astra|luna|codex|mini|nano|pro))?$")
_CLAUDE_VERSION = re.compile(r"^claude-(opus|sonnet|haiku)-(\d+(?:[-.]\d+)*)(\[1m\])?$")


def recognize_variant(backend_key: ConversationBackendKey, model: str) -> ModelVariant | None:
    """Recognize a family without treating absent catalogue evidence as invalidity."""
    if backend_key is ConversationBackendKey.hermes:
        return ModelVariant(backend_key, model)
    if backend_key is ConversationBackendKey.codex:
        if model in _CODEX_VARIANTS or model == "gpt":
            return ModelVariant(backend_key, model)
        match = _CODEX_VERSION.fullmatch(model)
        if match:
            return ModelVariant(backend_key, match.group(2) or "gpt")
    if backend_key is ConversationBackendKey.claude:
        for alias in ("opus", "sonnet", "haiku"):
            if model in (alias, alias + "[1m]"):
                return ModelVariant(backend_key, model)
        match = _CLAUDE_VERSION.fullmatch(model)
        if match:
            return ModelVariant(backend_key, match.group(1) + (match.group(3) or ""))
    return None


def identify_variant(backend_key: ConversationBackendKey, model: str) -> ModelVariant:
    """Reject unfamiliar families instead of guessing from provider defaults or hints."""
    variant = recognize_variant(backend_key, model)
    if variant is not None:
        return variant
    raise PlannerError(
        ErrorCode.validation,
        "unknown model variant",
        {"backend_key": str(backend_key), "model": model},
    )


def numerical_version(model: str) -> tuple[int, ...]:
    match = _CODEX_VERSION.fullmatch(model)
    if match:
        return tuple(int(part) for part in match.group(1).split("."))
    match = _CLAUDE_VERSION.fullmatch(model)
    if match:
        return tuple(int(part) for part in re.split(r"[-.]", match.group(2)))
    return ()


def select_catalogue_model(
    variant: ModelVariant,
    models: tuple[CachedModelResolution, ...],
) -> CachedModelResolution | None:
    if not models:
        return None
    if variant.backend_key is ConversationBackendKey.claude:
        alias = next((model for model in models if model.model == variant.name), None)
        if alias is not None:
            return alias
    return max(models, key=lambda value: numerical_version(value.model))
