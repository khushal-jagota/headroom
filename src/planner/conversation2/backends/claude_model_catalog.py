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

import asyncio
import re
from dataclasses import dataclass
from typing import Final

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

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
    reasoning_effort_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClaudeModelCatalog:
    """Everything claude offers, and the efforts a picker may show.

    ``reasoning_effort_options`` is the union across the models, kept in the order
    claude listed them, because a picker showing one list has to show something.
    """

    models: tuple[ClaudeModel, ...]
    reasoning_effort_options: tuple[str, ...]


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


async def probe_claude_model_catalog(claude_executable: str) -> ClaudeModelCatalog:
    """Ask the installed claude what it can be run as. Raises when it will not say."""
    try:
        reported = await asyncio.wait_for(
            _read_models_from_init(claude_executable), MODEL_CATALOG_TIMEOUT_SECONDS
        )
    except TimeoutError as never_answered:
        raise ClaudeModelCatalogUnavailable(
            "claude did not finish its startup handshake in time"
        ) from never_answered
    except Exception as would_not_start:  # noqa: BLE001 - every SDK failure is one answer
        raise ClaudeModelCatalogUnavailable(str(would_not_start)) from would_not_start

    models: list[ClaudeModel] = []
    efforts_in_listed_order: list[str] = []
    for entry in reported:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        resolved = entry.get("resolvedModel")
        if not isinstance(value, str) or not isinstance(resolved, str):
            continue
        if value == _DEFAULT_PSEUDO_MODEL_VALUE:
            continue
        raw_efforts = entry.get("supportedEffortLevels")
        efforts = tuple(
            effort for effort in (raw_efforts or ()) if isinstance(effort, str)
        )
        for effort in efforts:
            if effort not in efforts_in_listed_order:
                efforts_in_listed_order.append(effort)
        models.append(
            ClaudeModel(
                model_id=value,
                display_name=versioned_display_name(resolved),
                resolved_model_id=resolved,
                reasoning_effort_options=efforts,
            )
        )
    if not models:
        raise ClaudeModelCatalogUnavailable("claude's startup handshake named no models")
    return ClaudeModelCatalog(
        models=tuple(models), reasoning_effort_options=tuple(efforts_in_listed_order)
    )


async def _read_models_from_init(claude_executable: str) -> list[object]:
    options = ClaudeAgentOptions(cli_path=claude_executable, max_turns=1)
    async with ClaudeSDKClient(options=options) as client:
        info = await client.get_server_info()
    if not isinstance(info, dict):
        raise ClaudeModelCatalogUnavailable(
            "this claude's startup handshake does not report models"
        )
    reported = info.get("models")
    if not isinstance(reported, list):
        raise ClaudeModelCatalogUnavailable(
            "this claude's startup handshake does not report models"
        )
    return reported
