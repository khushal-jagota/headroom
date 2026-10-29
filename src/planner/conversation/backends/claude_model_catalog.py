"""What claude says it can be run as, asked of the CLI itself and nothing else.

Claude's aliases move: "opus" names whichever model the installed CLI resolves it to
today, so a written-down catalog goes quietly stale. The CLI's own init handshake
reports the real list — each pickable value, the concrete model id it resolves to
right now, and the effort levels that model takes — so the only honest catalog is the
one read from there.

Asking costs one short-lived child reading its startup handshake. No prompt is ever
sent, so nothing here starts a turn.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from planner.conversation.backends.claude_settings import (
    ClaudeControlTransport,
    ClaudeSettingsScopeRegistry,
    read_claude_catalog_settings_snapshot,
)
from planner.conversation.backends.probe_lifetime import owned_probe
from planner.conversation.model_resolution.diagnostics import _ProviderOperation

# The whole probe — spawn, init handshake, stop — against a local child. Long enough
# for a cold start on a slow machine, short enough that a card is never left waiting.
MODEL_CATALOG_TIMEOUT_SECONDS: Final = 30.0

# The CLI lists a "default" pseudo-entry pointing at whichever real model it currently
# recommends. The picker already has its own way of saying "the backend's own model",
# and the real model appears in the list as itself, so the pseudo-entry is dropped
# rather than shown twice.
_DEFAULT_PSEUDO_MODEL_VALUE: Final = "default"

_DATE_SUFFIX: Final = re.compile(r"^\d{8}$")


class ClaudeModelCatalogUnavailable(Exception):
    """Claude could not be asked what it runs, and why."""


@dataclass(frozen=True, slots=True)
class ClaudeModel:
    """One model claude offers: the value ``--model`` takes, and what it reaches."""

    model_id: str
    display_name: str
    resolved_model_id: str
    reasoning_effort_options: tuple[str, ...] | None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class ClaudeModelCatalog:
    """Everything claude offers, and the efforts a picker may show.

    ``reasoning_effort_options`` is the union across the models, kept in the order
    claude listed them, because a picker showing one list has to show something.
    """

    models: tuple[ClaudeModel, ...]
    reasoning_effort_options: tuple[str, ...]
    # Which of the models above claude runs when nobody picks one. Claude says this as a
    # pseudo-entry called "default" that is not itself selectable, so what is kept is the
    # real entry it resolves to. Absent when nothing in the list matches it.
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


async def probe_claude_model_catalog(
    claude_executable: str,
    *,
    environment: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
    claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
    _diagnostic_provider_operation: _ProviderOperation | None = None,
) -> ClaudeModelCatalog:
    """Ask the installed claude what it can be run as. Raises when it will not say."""
    async def operation() -> tuple[list[object], str | None]:
        if _diagnostic_provider_operation is None:
            return await _read_models_from_init(
                claude_executable, environment=environment, working_directory=working_directory,
                claude_scope_registry=claude_scope_registry,
            )
        with _diagnostic_provider_operation.claim():
            return await _read_models_from_init(
                claude_executable, environment=environment, working_directory=working_directory,
                claude_scope_registry=claude_scope_registry,
            )

    try:
        reported, provider_scope = await owned_probe(
            operation,
            timeout_seconds=MODEL_CATALOG_TIMEOUT_SECONDS,
        )
    except TimeoutError as never_answered:
        raise ClaudeModelCatalogUnavailable(
            "claude did not finish its startup handshake in time"
        ) from never_answered
    except Exception:  # noqa: BLE001 - every SDK failure is one answer
        raise ClaudeModelCatalogUnavailable("Claude metadata response unavailable") from None

    models: list[ClaudeModel] = []
    efforts_in_listed_order: list[str] = []
    default_resolves_to: str | None = None
    for entry in reported:
        if not isinstance(entry, dict) or entry.get("hidden") is True:
            continue
        if entry.get("disabled") is not None and not isinstance(entry["disabled"], bool):
            raise ClaudeModelCatalogUnavailable("Claude model enablement is incompatible")
        value = entry.get("value")
        resolved = entry.get("resolvedModel")
        if not isinstance(value, str) or not value or not isinstance(resolved, str) or not resolved:
            continue
        if value == _DEFAULT_PSEUDO_MODEL_VALUE:
            # Not an option — "default" is not a model anybody can be shown as running.
            # What it resolves to is a model, and that is the part worth keeping.
            default_resolves_to = resolved
            continue
        raw_efforts = entry.get("supportedEffortLevels")
        efforts = (
            tuple(raw_efforts)
            if isinstance(raw_efforts, list)
            and all(isinstance(effort, str) and effort for effort in raw_efforts)
            else None
        )
        for effort in efforts or ():
            if effort not in efforts_in_listed_order:
                efforts_in_listed_order.append(effort)
        models.append(
            ClaudeModel(
                model_id=value,
                display_name=versioned_display_name(resolved),
                resolved_model_id=resolved,
                reasoning_effort_options=efforts,
                enabled=(False if entry.get("disabled") is True else
                         True if entry.get("disabled") is False else None),
            )
        )
    if not models:
        raise ClaudeModelCatalogUnavailable("claude's startup handshake named no models")
    return ClaudeModelCatalog(
        models=tuple(models),
        reasoning_effort_options=tuple(efforts_in_listed_order),
        default_model_id=_default_model_id(models, default_resolves_to),
        provider_scope=provider_scope,
    )


def _default_model_id(models: list[ClaudeModel], resolves_to: str | None) -> str | None:
    """The listed model claude's default reaches, named as the picker names it.

    Claude's default is stated as a resolved model id, and a picker holds the values
    ``--model`` accepts. So the two are joined here on the resolved id, and a default that
    matches nothing listed is reported as no default rather than as a value nobody could
    have selected.
    """
    if resolves_to is None:
        return None
    for model in models:
        if model.resolved_model_id == resolves_to:
            return model.model_id
    return None


async def _read_models_from_init(
    claude_executable: str,
    *,
    environment: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
    claude_scope_registry: ClaudeSettingsScopeRegistry | None = None,
) -> tuple[list[object], str | None]:
    options = ClaudeAgentOptions(
        cli_path=claude_executable,
        max_turns=1,
        cwd=working_directory,
        env={} if environment is None else dict(environment),
    )
    async def empty_prompt() -> AsyncIterator[dict[str, Any]]:
        return
        yield {}

    transport = ClaudeControlTransport(
        SubprocessCLITransport(prompt=empty_prompt(), options=options)
    )
    async with ClaudeSDKClient(options=options, transport=transport):
        sample = await read_claude_catalog_settings_snapshot(transport)
        scope = claude_scope_registry.retain(sample.settings) if claude_scope_registry else None
        return list(sample.models), scope
    raise ClaudeModelCatalogUnavailable("Claude metadata client closed without a snapshot")
