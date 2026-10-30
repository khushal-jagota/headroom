"""Claude suggestions from the retained T3 recipe, scoped to the local CLI version.

These suggestions are advisory. Exact authored tokens do not require membership.
Recipe source: T3 dd148481531a0a090780a2ade20588566db099a2.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import anyio

from planner.conversation.backends.probe_lifetime import owned_probe

MODEL_CATALOG_TIMEOUT_SECONDS: Final = 30.0
_DATE_SUFFIX: Final = re.compile(r"^\d{8}$")


class ClaudeModelCatalogUnavailable(Exception):
    """Local Claude metadata could not be read."""


@dataclass(frozen=True, slots=True)
class ClaudeModel:
    """One model claude offers: the value ``--model`` takes, and what it reaches."""

    model_id: str
    display_name: str
    resolved_model_id: str | None
    reasoning_effort_options: tuple[str, ...] | None
    enabled: bool | None = None
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class ClaudeModelCatalog:
    """Everything claude offers, and the efforts a picker may show.

    ``reasoning_effort_options`` is the union across the models, kept in the order
    claude listed them, because a picker showing one list has to show something.
    """

    models: tuple[ClaudeModel, ...]
    reasoning_effort_options: tuple[str, ...]
    # Suggestions never select a default on the user's behalf.
    default_model_id: str | None = None
    complete: bool = True
    visibility_limited: bool = True
    provider_scope: str | None = None


def versioned_display_name(resolved_model_id: str) -> str:
    """The name a person reads for a concrete model id.

    ``claude-opus-5[1m]`` reads "Opus 5 (1M)"; ``claude-haiku-4-5-20251001`` reads
    "Haiku 4.5". An id this cannot make sense of is shown as itself — honest over
    pretty.
    """
    identifier = resolved_model_id
    large_context = identifier.endswith("[1m]")
    if large_context:
        identifier = identifier[: -len("[1m]")]
    parts = identifier.split("-")
    if not parts or parts[0] != "claude" or len(parts) < 2:
        return resolved_model_id
    family = parts[1].capitalize()
    version_parts = [part for part in parts[2:] if not _DATE_SUFFIX.match(part)]
    if not all(part.isdigit() for part in version_parts):
        return resolved_model_id
    shown = family if not version_parts else f"{family} {'.'.join(version_parts)}"
    return f"{shown} (1M)" if large_context else shown


@dataclass(frozen=True, slots=True)
class _Recipe:
    model: str
    minimum: tuple[int, ...] | None
    maximum_exclusive: tuple[int, ...] | None
    efforts: tuple[str, ...]
    aliases: tuple[str, ...]


# Only native effort values. T3 prompt-injected and workflow modes are excluded.
_RECIPES: tuple[_Recipe, ...] = (
    _Recipe('claude-opus-5-5', (2, 1, 280), None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('opus', 'opus-5.5', 'claude-opus-5.5')),
    _Recipe('claude-sonnet-5-5', (2, 1, 284), None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('sonnet', 'sonnet-5.5', 'claude-sonnet-5.5')),
    _Recipe('claude-fable-5-1', (2, 1, 257), None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('fable', 'fable-5.1', 'claude-fable-5.1')),
    _Recipe('claude-fable-5', (2, 1, 169), None,
            ('low', 'medium', 'high', 'xhigh', 'max'), ()),
    _Recipe('claude-opus-5', (2, 1, 219), None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('opus-5', 'claude-opus-5.0', 'claude-opus-5-0')),
    _Recipe('claude-opus-4-8', (2, 1, 154), None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('opus-4.8', 'claude-opus-4.8')),
    _Recipe('claude-opus-4-7', (2, 1, 111), None,
            ('low', 'medium', 'high', 'max'),
            ('opus-4.7', 'claude-opus-4.7')),
    _Recipe('claude-opus-4-6', None, None,
            ('low', 'medium', 'high', 'max'),
            ('opus-4.6', 'claude-opus-4.6', 'claude-opus-4-6-20251117')),
    _Recipe('claude-opus-4-5', None, None,
            ('low', 'medium', 'high', 'max'), ()),
    _Recipe('claude-sonnet-5', None, None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('sonnet-5', 'claude-sonnet-5.0', 'claude-sonnet-5-0')),
    _Recipe('claude-sonnet-4-6', None, None,
            ('low', 'medium', 'high'),
            ('sonnet-4.6', 'claude-sonnet-4.6', 'claude-sonnet-4-6-20251117')),
    _Recipe('claude-haiku-5-5', (2, 1, 293), None,
            ('low', 'medium', 'high', 'xhigh', 'max'),
            ('haiku', 'haiku-5.5', 'claude-haiku-5.5')),
    _Recipe('claude-haiku-4-5', None, None,
            (), ('haiku-4.5', 'claude-haiku-4.5', 'claude-haiku-4-5-20251001')),
)


def claude_models_for_version(version: str | None) -> ClaudeModelCatalog:
    """Omit bounded suggestions when the optional local version is unavailable."""
    match = re.search(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?![\d.])", version or "")
    parsed = tuple(map(int, match.groups())) if match else None
    models = tuple(
        ClaudeModel(row.model, versioned_display_name(row.model), row.model, row.efforts)
        for row in _RECIPES
        if (row.minimum is None or (parsed is not None and parsed >= row.minimum))
        and (row.maximum_exclusive is None or
             (parsed is not None and parsed < row.maximum_exclusive))
    )
    return ClaudeModelCatalog(
        models=models,
        reasoning_effort_options=tuple(dict.fromkeys(
            effort for row in models for effort in row.reasoning_effort_options or ()
        )),
    )


def claude_launch_model(model: str | None) -> str | None:
    """Map documented versioned aliases only; retain floating and custom intent."""
    if model is None:
        return None
    suffix = "[1m]" if model.endswith("[1m]") else ""
    token = model[:-4] if suffix else model
    for row in _RECIPES:
        if token in row.aliases and any(character.isdigit() for character in token):
            return row.model + suffix
    return model


async def probe_claude_model_catalog(
    claude_executable: str,
    *,
    environment: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
) -> ClaudeModelCatalog:
    """Read local version metadata without an SDK session or provider request."""
    async def operation() -> str:
        result = await anyio.run_process(
            [claude_executable, "--version"],
            env=environment,
            cwd=working_directory,
        )
        return result.stdout.decode("utf-8", errors="replace")

    try:
        version = await owned_probe(operation, timeout_seconds=MODEL_CATALOG_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - optional metadata never gates execution
        version = None
    return claude_models_for_version(version)
