"""Normalize accepted Hermes gateway observations into display-safe chat activity.

One canonical meaning, shared by the human-chat stream path and the worker
gateway event path, so both surfaces read the same timeline. A normalized entry
carries only a display category, a safe label, its lifecycle state, and a stable
action identity — never raw payload (no reasoning/chain-of-thought text, tool
arguments, tool results, or command output). Pure: no framework or IO imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from planner.chat.contracts import ChatActivityObservation

# Hermes streams the model's private reasoning as these deltas. We record only
# that thinking is happening under one stable identity so repeats collapse to a
# single row; the reasoning text itself is never read.
_THINKING_EVENT_TYPES = frozenset({"reasoning.delta", "thinking.delta"})

# tool.start begins a tool call; tool.complete/tool.end both end one — Hermes has
# used both spellings for the same lifecycle boundary. tool.delta (mid-call
# streaming) is intentionally unmapped: the start observation already carries the
# running state, so a delta cannot add display information, only spam.
_TOOL_START_EVENT_TYPES = frozenset({"tool.start"})
_TOOL_END_EVENT_TYPES = frozenset({"tool.complete", "tool.end"})
_COMMAND_START_EVENT_TYPES = frozenset({"command.start"})

# The Hermes shell tool. Its runs read as "commands" in the timeline rather than
# generic tool use; every other tool falls under the generic "tool" category.
_COMMAND_TOOL_NAMES = frozenset({"terminal"})

# Identity field names vary across Hermes payload shapes; try each in order.
_IDENTITY_KEYS = ("tool_id", "call_id", "id")


def _safe_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) else ""


def _action_identity(payload: Mapping[str, Any], prefix: str) -> str | None:
    for key in _IDENTITY_KEYS:
        value = _safe_string(payload, key)
        if value:
            return f"{prefix}:{value}"
    return None


def _display_name(payload: Mapping[str, Any], fallback: str) -> str:
    for key in ("label", "name", "tool_name"):
        value = _safe_string(payload, key)
        if value:
            return value
    raw_tool = payload.get("tool")
    if isinstance(raw_tool, Mapping):
        for key in ("label", "name"):
            value = _safe_string(raw_tool, key)
            if value:
                return value
    return fallback


def normalize_gateway_activity(
    event_type: str, payload: Mapping[str, Any]
) -> ChatActivityObservation | None:
    """Map one gateway observation to a display-safe activity, or None to ignore.

    Only thinking, tool, and command observations produce activity; visible
    message deltas, completions, and lifecycle frames return None.
    """
    if event_type in _THINKING_EVENT_TYPES:
        return ChatActivityObservation(
            category="thinking",
            label="Thinking",
            lifecycle_state="running",
            action_identity="thinking",
        )
    if event_type in _COMMAND_START_EVENT_TYPES:
        display = _display_name(payload, "command")
        return ChatActivityObservation(
            category="command",
            label=f"Running {display}",
            lifecycle_state="running",
            action_identity=_action_identity(payload, "command"),
        )
    if event_type in _TOOL_START_EVENT_TYPES or event_type in _TOOL_END_EVENT_TYPES:
        name = _display_name(payload, "tool")
        action_identity = _action_identity(payload, "tool")
        complete = event_type in _TOOL_END_EVENT_TYPES
        if name in _COMMAND_TOOL_NAMES:
            return ChatActivityObservation(
                category="command",
                label=f"Ran {name}" if complete else f"Running {name}",
                lifecycle_state="complete" if complete else "running",
                action_identity=action_identity,
            )
        display = name or "tool"
        return ChatActivityObservation(
            category="tool",
            label=f"Used {display}" if complete else f"Using {display}",
            lifecycle_state="complete" if complete else "running",
            action_identity=action_identity,
        )
    return None
