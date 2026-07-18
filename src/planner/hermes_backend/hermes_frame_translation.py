"""Pure Hermes<->neutral translation functions (plan §3).

Two pure functions with no I/O, no relay, no sockets:
- `native_frame_to_neutral_event(employee_entity_id, frame) -> NeutralEvent`
- `neutral_request_to_native_frames(request, session_id) -> list[NativeRequestPlan]`

plus `native_error_to_turn_failed` (correlated native errors -> neutral failure). Under
D-only-free-hermes-features there is no model/catalog NORMALIZATION here: the `commands.catalog`
result is delivered payload-as-is by the session, and model selection is cut.

Imports the neutral vocabulary and defines the Hermes method/type/params string constants
HERE (never in the framework-free vocabulary). The truncation bound comes from the isolated
config module (PRINCIPLES: tunables live there, not inline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from planner.hermes_backend import neutral_vocabulary as nv
from planner.hermes_backend.neutral_relay_config import TOOL_PREVIEW_MAX_CHARS

# --- Hermes native method / event-type constants (Hermes-specific, defined here) ------

# Native EVENT params.type values (server.py _emit frames).
NATIVE_MESSAGE_START = "message.start"
NATIVE_MESSAGE_DELTA = "message.delta"
NATIVE_THINKING_DELTA = "thinking.delta"
NATIVE_REASONING_DELTA = "reasoning.delta"
NATIVE_REASONING_AVAILABLE = "reasoning.available"
NATIVE_TOOL_START = "tool.start"
NATIVE_TOOL_GENERATING = "tool.generating"
NATIVE_TOOL_COMPLETE = "tool.complete"
NATIVE_CLARIFY_REQUEST = "clarify.request"
NATIVE_APPROVAL_REQUEST = "approval.request"
NATIVE_MESSAGE_COMPLETE = "message.complete"
NATIVE_ERROR = "error"
NATIVE_SESSION_TITLE = "session.title"
NATIVE_SESSION_INFO = "session.info"
NATIVE_STATUS_UPDATE = "status.update"

# Native RPC methods the translator issues (all off the S1 denylist).
NATIVE_SESSION_ACTIVE_LIST = "session.active_list"
NATIVE_SESSION_HISTORY = "session.history"
NATIVE_SESSION_INTERRUPT = "session.interrupt"
NATIVE_SESSION_COMPRESS = "session.compress"
NATIVE_COMMANDS_CATALOG = "commands.catalog"
NATIVE_PROMPT_SUBMIT = "prompt.submit"
NATIVE_IMAGE_ATTACH = "image.attach"
NATIVE_CLARIFY_RESPOND = "clarify.respond"
NATIVE_APPROVAL_RESPOND = "approval.respond"

# Native busy/error codes surfaced as distinct neutral failure reasons.
NATIVE_BUSY_ALREADY_RUNNING_CODE = 4009

# The relay's synthesized child-reset frame (employee_child_relay.deliver_child_death).
RELAY_EVENT_CHILD_RESET_TYPE = "child_reset"


@dataclass(frozen=True)
class NativeRequestPlan:
    """One native JSON-RPC frame to inject through the S1 `relay:"request"` seam.

    The session assigns the downstream id; here we carry only method + params. `is_rpc`
    marks a frame whose correlated result the translator consumes (versus a fire-and-forget
    forward whose child events flow back through the normal event stream)."""

    method: str
    params: dict[str, Any]
    kind: str  # a stable tag for correlation bookkeeping (RpcKind value); "" if none


# --- frame -> neutral event --------------------------------------------------


def _payload(frame: dict[str, Any]) -> dict[str, Any]:
    params = frame.get("params")
    if not isinstance(params, dict):
        return {}
    payload = params.get("payload")
    return payload if isinstance(payload, dict) else {}


def _truncate(text: str) -> str:
    return text[:TOOL_PREVIEW_MAX_CHARS]


def native_frame_to_neutral_event(
    employee_entity_id: str, frame: dict[str, Any]
) -> nv.NeutralEvent:
    """Translate one native child->downstream frame into a neutral event.

    Employee identity rides EVERY event from the relay's per-frame label, not the payload.
    Unknown native event kinds map to `PassthroughEvent` — nothing is silently dropped."""
    # S1 synthesized child-reset frame (not a native JSON-RPC event frame).
    if frame.get("relay") == "event" and frame.get("type") == RELAY_EVENT_CHILD_RESET_TYPE:
        return nv.ChildResetEvent(employee_entity_id=employee_entity_id)

    params = frame.get("params")
    native_type = params.get("type") if isinstance(params, dict) else None
    payload = _payload(frame)

    if native_type == NATIVE_MESSAGE_START:
        return nv.TurnStartedEvent(employee_entity_id=employee_entity_id)
    if native_type == NATIVE_MESSAGE_DELTA:
        return nv.AssistantTextDeltaEvent(
            employee_entity_id=employee_entity_id, text=str(payload.get("text", ""))
        )
    if native_type in (NATIVE_THINKING_DELTA, NATIVE_REASONING_DELTA, NATIVE_REASONING_AVAILABLE):
        return nv.ThinkingDeltaEvent(
            employee_entity_id=employee_entity_id, text=str(payload.get("text", ""))
        )
    if native_type == NATIVE_TOOL_START:
        return nv.ToolActivityEvent(
            employee_entity_id=employee_entity_id,
            tool_id=str(payload.get("tool_id", "")),
            tool_name=str(payload.get("name", "")),
            phase=nv.ToolPhase.started,
            preview=_truncate(str(payload.get("context", ""))),
        )
    if native_type == NATIVE_TOOL_GENERATING:
        return nv.ToolActivityEvent(
            employee_entity_id=employee_entity_id,
            tool_id="",
            tool_name=str(payload.get("name", "")),
            phase=nv.ToolPhase.progress,
            preview="",
        )
    if native_type == NATIVE_TOOL_COMPLETE:
        summary = payload.get("summary")
        preview_source = summary if isinstance(summary, str) else _render_result(payload)
        return nv.ToolActivityEvent(
            employee_entity_id=employee_entity_id,
            tool_id=str(payload.get("tool_id", "")),
            tool_name=str(payload.get("name", "")),
            phase=nv.ToolPhase.completed,
            preview=_truncate(preview_source),
        )
    if native_type == NATIVE_CLARIFY_REQUEST:
        raw_choices = payload.get("choices")
        choices = (
            tuple(str(c) for c in raw_choices) if isinstance(raw_choices, list) else ()
        )
        return nv.AgentQuestionEvent(
            employee_entity_id=employee_entity_id,
            request_id=str(payload.get("request_id", "")),
            prompt_text=str(payload.get("question", "")),
            choices=choices,
        )
    if native_type == NATIVE_APPROVAL_REQUEST:
        return nv.ToolApprovalRequestEvent(
            employee_entity_id=employee_entity_id,
            request_id=str(payload.get("request_id", "")),
            summary=_approval_summary(payload),
        )
    if native_type == NATIVE_MESSAGE_COMPLETE:
        status = payload.get("status")
        if status in ("error", "interrupted"):
            reason = (
                nv.TurnFailureReason.interrupted
                if status == "interrupted"
                else nv.TurnFailureReason.agent_error
            )
            return nv.TurnFailedEvent(
                employee_entity_id=employee_entity_id,
                reason=reason,
                detail=str(payload.get("text", "")),
            )
        return nv.TurnCompletedEvent(
            employee_entity_id=employee_entity_id, final_text=str(payload.get("text", ""))
        )
    if native_type == NATIVE_ERROR:
        return nv.TurnFailedEvent(
            employee_entity_id=employee_entity_id,
            reason=nv.TurnFailureReason.agent_error,
            detail=str(payload.get("message", "")),
        )
    if native_type == NATIVE_SESSION_TITLE:
        return nv.SessionTitledEvent(
            employee_entity_id=employee_entity_id, title=str(payload.get("title", ""))
        )
    # Explicit S0-inventoried passthrough rows + the general unknown fallthrough all
    # serialize the whole payload opaque.
    return nv.PassthroughEvent(
        employee_entity_id=employee_entity_id,
        native_type=str(native_type) if native_type is not None else "",
        payload_json=json.dumps(payload),
    )


def _approval_summary(payload: dict[str, Any]) -> str:
    command = payload.get("command")
    if isinstance(command, str) and command:
        return _truncate(command)
    summary = payload.get("summary")
    if isinstance(summary, str):
        return _truncate(summary)
    return ""


def _render_result(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    return json.dumps(result)


# --- neutral request -> native RPC sequence ----------------------------------


def neutral_request_to_native_frames(
    request: nv.NeutralRequest, *, session_id: str
) -> list[NativeRequestPlan]:
    """Translate one neutral request into its ordered native frame plan (plan §3).

    `session_id` is the child's live session id the session has bootstrapped. `list catalog`
    produces the RPC whose correlated result the session delivers PAYLOAD AS-IS; that
    correlation tag rides `NativeRequestPlan.kind`."""
    if isinstance(request, nv.SendMessageRequest):
        plans: list[NativeRequestPlan] = [
            NativeRequestPlan(
                NATIVE_IMAGE_ATTACH, {"session_id": session_id, "path": ref}, RpcKind.ACK
            )
            for ref in request.image_refs
        ]
        plans.append(
            NativeRequestPlan(
                NATIVE_PROMPT_SUBMIT,
                {"session_id": session_id, "text": request.text},
                RpcKind.ACK,
            )
        )
        return plans
    if isinstance(request, nv.AnswerQuestionRequest):
        return [
            NativeRequestPlan(
                NATIVE_CLARIFY_RESPOND,
                {
                    "session_id": session_id,
                    "request_id": request.request_id,
                    "answer": request.answer,
                },
                RpcKind.ACK,
            )
        ]
    if isinstance(request, nv.RespondToApprovalRequest):
        return [
            NativeRequestPlan(
                NATIVE_APPROVAL_RESPOND,
                {
                    "session_id": session_id,
                    "request_id": request.request_id,
                    "choice": request.decision,
                    "all": request.apply_to_all,
                },
                RpcKind.ACK,
            )
        ]
    if isinstance(request, nv.InterruptRequest):
        return [
            NativeRequestPlan(NATIVE_SESSION_INTERRUPT, {"session_id": session_id}, RpcKind.ACK)
        ]
    if isinstance(request, nv.CompactRequest):
        return [
            NativeRequestPlan(
                NATIVE_SESSION_COMPRESS, {"session_id": session_id}, RpcKind.ACK
            )
        ]
    if isinstance(request, nv.ListCatalogRequest):
        return [
            NativeRequestPlan(
                NATIVE_COMMANDS_CATALOG, {"session_id": session_id}, RpcKind.CATALOG
            )
        ]
    raise ValueError(f"request has no direct native RPC plan: {type(request)!r}")


# --- correlation kinds -------------------------------------------------------


class RpcKind:
    """String tags for correlated translator-issued RPCs whose result the session
    consumes (not forwarded raw).

    `ACK` is the generic case for a native request (prompt.submit, image.attach,
    clarify.respond, approval.respond, session.interrupt, session.compress): the correlated
    result carries nothing the pane needs on success (the real turn output flows back as
    event frames), so a success is dropped; an error surfaces as a `TurnFailedEvent` so a
    native rejection (e.g. 4009 busy) is never masked. `ACTIVE_LIST`/`HISTORY`/`CATALOG`
    carry a payload the session turns into internal state or a neutral event."""

    ACK = "ack"
    ACTIVE_LIST = "active_list"
    HISTORY = "history"
    CATALOG = "catalog"


def native_error_to_turn_failed(
    employee_entity_id: str, *, code: int, message: str
) -> nv.TurnFailedEvent:
    """Map a native RPC error (from a correlated result) to a neutral turn-failed event.

    Code 4009 -> `busy_already_running` (a completeness mapping; stock relay children
    queue-not-reject on a mid-turn send). Every other code -> `agent_error` with the native
    message surfaced verbatim (the native rejection is not masked)."""
    reason = (
        nv.TurnFailureReason.busy_already_running
        if code == NATIVE_BUSY_ALREADY_RUNNING_CODE
        else nv.TurnFailureReason.agent_error
    )
    return nv.TurnFailedEvent(
        employee_entity_id=employee_entity_id, reason=reason, detail=message
    )
