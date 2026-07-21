from __future__ import annotations

import hashlib
import json
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from acp.schema import (
    AgentThoughtChunk,
    AllowedOutcome,
    DeniedOutcome,
    ImageContentBlock,
    PermissionOption,
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TerminalExitStatus,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCallUpdate,
)
from acp.utils import serialize_params
from pydantic import ValidationError
from tests.support.acp_fixture_writer import check_or_write_fixtures

from planner.conversation import (
    BROWSER_ACTION_ADAPTER,
    CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS,
    SERVER_ENVELOPE_ADAPTER,
    AcpSessionUpdateEnvelope,
    ActivityEnvelope,
    AgentBackendDefinition,
    AttachAction,
    BackendTurnCapabilities,
    CancelAction,
    ConnectionEnvelope,
    ConnectionPayload,
    ContextCompaction,
    ContextCompactionEnvelope,
    ConversationActivity,
    ConversationEmployee,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationSessionBinding,
    ConversationTerminalState,
    DeliveryReceiptEnvelope,
    HumanEcho,
    HumanEchoEnvelope,
    NewConversationAction,
    PermissionOutcomeEnvelope,
    PermissionRequestEnvelope,
    PermissionResponseAction,
    PromptAction,
    ProtocolUpdateRejectedEnvelope,
    QueuedPrompt,
    QueueSnapshot,
    QueueSnapshotEnvelope,
    ReverseServiceCapabilities,
    TerminalStateEnvelope,
    TurnDeliveryReceipt,
    session_update_envelope_or_rejection,
    validate_browser_action,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VENDOR_ROOT = REPOSITORY_ROOT / "web" / "src" / "vendor" / "acp-components-core"
SESSION_ID = "session-1"


def _text(text: str = "hello") -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def _prompt(session_id: str = SESSION_ID) -> PromptRequest:
    return PromptRequest(
        session_id=session_id,
        prompt=[
            _text(),
            ImageContentBlock(type="image", data="aW1hZ2U=", mime_type="image/png"),
        ],
    )


def _employee() -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="employee-1",
        entity_kind="ticket",
        entity_id="ticket-1",
        workspace_roots=(REPOSITORY_ROOT, REPOSITORY_ROOT / "web"),
        backend_key="scripted",
    )


def _permission_request() -> RequestPermissionRequest:
    return RequestPermissionRequest(
        session_id=SESSION_ID,
        tool_call=ToolCallUpdate(tool_call_id="tool-1", title="Read file", status="pending"),
        options=[
            PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
            PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
        ],
    )


def _common(sequence: int = 1) -> dict[str, object]:
    return {
        "wire_version": 1,
        "employee_id": "employee-1",
        "entity_kind": "ticket",
        "entity_id": "ticket-1",
        "acp_session_id": SESSION_ID,
        "binding_generation": 2,
        "sequence": sequence,
    }


@pytest.mark.parametrize("entity_kind", ["ticket", "agent"])
def test_conversation_employee_accepts_every_entity_kind_and_is_frozen(entity_kind: str) -> None:
    employee = ConversationEmployee(
        employee_id="employee-1",
        entity_kind=entity_kind,  # type: ignore[arg-type]
        entity_id="entity-1",
        workspace_roots=(REPOSITORY_ROOT,),
        backend_key="scripted",
    )
    assert employee.entity_kind == entity_kind
    with pytest.raises(ValidationError):
        employee.employee_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("employee_id", ""),
        ("employee_id", " employee"),
        ("entity_id", "ticket\n1"),
        ("backend_key", ""),
        ("entity_kind", "day"),
        ("workspace_roots", ()),
        ("workspace_roots", (Path("relative"),)),
        ("workspace_roots", (Path("/tmp/root"), Path("/tmp/root/../root"))),
    ],
)
def test_conversation_employee_rejects_invalid_identity_and_roots(
    field: str, value: object
) -> None:
    values = {
        "employee_id": "employee-1",
        "entity_kind": "ticket",
        "entity_id": "ticket-1",
        "workspace_roots": (REPOSITORY_ROOT,),
        "backend_key": "scripted",
    }
    values[field] = value
    with pytest.raises(ValidationError):
        ConversationEmployee.model_validate(values)


def test_session_binding_requires_positive_generation_and_non_empty_ids() -> None:
    binding = ConversationSessionBinding(
        employee_id="employee-1",
        acp_session_id=SESSION_ID,
        backend_key="scripted",
        binding_generation=1,
    )
    assert binding.binding_generation == 1
    for change in (
        {"employee_id": ""},
        {"acp_session_id": ""},
        {"backend_key": ""},
        {"binding_generation": 0},
    ):
        with pytest.raises(ValidationError):
            ConversationSessionBinding.model_validate(
                {
                    "employee_id": "employee-1",
                    "acp_session_id": SESSION_ID,
                    "backend_key": "scripted",
                    "binding_generation": 1,
                    **change,
                }
            )


@pytest.mark.parametrize(
    "state",
    [
        "connecting",
        "loading",
        "idle",
        "thinking",
        "working",
        "compacting",
        "waiting_for_permission",
        "interrupted",
        "failed",
    ],
)
def test_activity_accepts_every_state(state: str) -> None:
    activity = ConversationActivity(state=state, detail="Visible state", sequence=1)  # type: ignore[arg-type]
    assert activity.state == state
    with pytest.raises(ValidationError):
        ConversationActivity(state=state, detail="line\nbreak", sequence=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("choice", ["normal", "steer", "send_now", "queue"])
@pytest.mark.parametrize("state", ["accepted", "started"])
def test_delivery_receipt_accepts_choices_and_unadorned_states(choice: str, state: str) -> None:
    receipt = TurnDeliveryReceipt(
        client_message_id="client-1",
        choice=choice,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )
    assert receipt.choice == choice


def test_delivery_receipt_cross_field_invariants() -> None:
    assert (
        TurnDeliveryReceipt(
            client_message_id="client-1",
            choice="queue",
            state="queued",
            queue_position=1,
        ).queue_position
        == 1
    )
    assert (
        TurnDeliveryReceipt(
            client_message_id="client-1",
            choice="send_now",
            state="interrupted",
            reason="Superseded",
        ).reason
        == "Superseded"
    )
    assert (
        TurnDeliveryReceipt(
            client_message_id="client-1",
            choice="steer",
            state="rejected",
            reason="Steer unavailable",
        ).reason
        == "Steer unavailable"
    )
    invalid_values = [
        {"state": "queued"},
        {"state": "accepted", "queue_position": 1},
        {"state": "rejected"},
        {"state": "started", "reason": "not allowed"},
    ]
    for invalid in invalid_values:
        with pytest.raises(ValidationError):
            TurnDeliveryReceipt(
                client_message_id="client-1",
                choice="normal",
                **invalid,  # type: ignore[arg-type]
            )


@pytest.mark.parametrize("trigger", ["explicit", "automatic"])
def test_compaction_cross_field_invariants(trigger: str) -> None:
    compacting = ContextCompaction(
        boundary_id="boundary-1",
        state="compacting",
        trigger=trigger,  # type: ignore[arg-type]
    )
    compacted = ContextCompaction(
        boundary_id="boundary-1",
        state="compacted",
        trigger=trigger,  # type: ignore[arg-type]
    )
    failed = ContextCompaction(
        boundary_id="boundary-1",
        state="failed",
        trigger=trigger,  # type: ignore[arg-type]
        reason="Capture failed",
    )
    assert (compacting.state, compacted.state, failed.state) == (
        "compacting",
        "compacted",
        "failed",
    )
    for values in (
        {"state": "compacting", "summary": "too early"},
        {"state": "failed"},
        {"state": "compacted", "summary": "private context"},
        {"state": "compacted", "reason": "not failed"},
        {"state": "compacting", "reason": "not failed"},
    ):
        with pytest.raises(ValidationError):
            ContextCompaction(
                boundary_id="boundary-1",
                trigger="explicit",
                **values,  # type: ignore[arg-type]
            )


@pytest.mark.parametrize("lifecycle", ["pending", "answered", "cancelled"])
def test_permission_request_preserves_exact_sdk_request_and_option_order(lifecycle: str) -> None:
    sdk_request = _permission_request()
    request = ConversationPermissionRequest(
        request_id="permission-1",
        employee_id="employee-1",
        backend_key="scripted",
        request=sdk_request,
        lifecycle=lifecycle,  # type: ignore[arg-type]
        deadline_at=1_752_960_300,
        opened_sequence=4,
    )
    assert request.request is sdk_request
    serialized = serialize_params(request)
    assert [option["optionId"] for option in serialized["request"]["options"]] == [
        "allow-once",
        "reject-once",
    ]
    assert serialized["request"]["toolCall"]["toolCallId"] == "tool-1"
    assert "scope" not in json.dumps(serialized)


def test_permission_outcome_requires_cancellation_reason_iff_cancelled() -> None:
    selected_response = RequestPermissionResponse(
        outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
    )
    selected = ConversationPermissionOutcome(
        request_id="permission-1",
        response=selected_response,
        settled_sequence=5,
    )
    assert serialize_params(selected)["response"]["outcome"]["optionId"] == "allow-once"
    cancelled_response = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
    cancelled = ConversationPermissionOutcome(
        request_id="permission-1",
        response=cancelled_response,
        cancellation_reason="Browser disconnected",
        settled_sequence=5,
    )
    assert cancelled.cancellation_reason == "Browser disconnected"
    with pytest.raises(ValidationError):
        ConversationPermissionOutcome(
            request_id="permission-1",
            response=cancelled_response,
            settled_sequence=5,
        )
    with pytest.raises(ValidationError):
        ConversationPermissionOutcome(
            request_id="permission-1",
            response=selected_response,
            cancellation_reason="not cancelled",
            settled_sequence=5,
        )


def test_queued_prompt_embeds_exact_sdk_prompt_content_union() -> None:
    prompt = _prompt()
    queued = QueuedPrompt(
        client_message_id="client-1",
        prompt=prompt,
        enqueue_sequence=1,
        enqueued_at=0,
    )
    assert queued.prompt is prompt
    serialized = serialize_params(queued)
    assert serialized["prompt"]["prompt"] == [
        {"type": "text", "text": "hello"},
        {"type": "image", "data": "aW1hZ2U=", "mimeType": "image/png"},
    ]


def test_backend_definition_freezes_exact_argv_environment_and_cwd_policy() -> None:
    class Strategy:
        async def steer(
            self, session_binding: object, prompt: object, client_message_id: str
        ) -> None:
            del session_binding, prompt, client_message_id

        def observe_compaction(self, session_binding: object, notification: object) -> None:
            del session_binding, notification

        async def capture_compaction(self, session_binding: object) -> None:
            del session_binding

    definition = AgentBackendDefinition(
        backend_key="scripted",
        argv=("python", "agent.py"),
        inherited_environment_names=("PATH",),
        environment_overrides=(("MODE", "test"),),
        expected_agent_name="scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(supports_steer=False, observes_compaction=True),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False,
            terminal=False,
            permission=True,
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=Strategy(),  # type: ignore[arg-type]
    )
    assert definition.working_directory_for(_employee()) == REPOSITORY_ROOT
    with pytest.raises(ValueError):
        AgentBackendDefinition(
            backend_key="scripted",
            argv=(),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="scripted-agent",
            expected_agent_version="1.0.0",
            turn_capabilities=definition.turn_capabilities,
            reverse_service_capabilities=definition.reverse_service_capabilities,
            working_directory_resolver=lambda employee: employee.workspace_roots[0],
            turn_strategy=definition.turn_strategy,
        )


def test_server_envelope_union_covers_all_variants_and_sdk_alias_serialization() -> None:
    notification = SessionNotification(
        session_id=SESSION_ID,
        update=AgentThoughtChunk(
            session_update="agent_thought_chunk",
            message_id="thought-1",
            content=_text("typed thought"),
        ),
    )
    permission = ConversationPermissionRequest(
        request_id="permission-1",
        employee_id="employee-1",
        backend_key="scripted",
        request=_permission_request(),
        lifecycle="pending",
        deadline_at=300,
        opened_sequence=6,
    )
    outcome = ConversationPermissionOutcome(
        request_id="permission-1",
        response=RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
        ),
        settled_sequence=7,
    )
    envelopes = [
        AcpSessionUpdateEnvelope(**_common(1), type="acp_session_update", payload=notification),
        ActivityEnvelope(
            **_common(2),
            type="activity",
            payload=ConversationActivity(state="thinking", detail="Thinking", sequence=2),
        ),
        DeliveryReceiptEnvelope(
            **_common(3),
            type="delivery_receipt",
            payload=TurnDeliveryReceipt(
                client_message_id="client-1", choice="normal", state="accepted"
            ),
        ),
        QueueSnapshotEnvelope(
            **_common(4),
            type="queue_snapshot",
            payload=QueueSnapshot(
                items=(
                    QueuedPrompt(
                        client_message_id="client-1",
                        prompt=_prompt(),
                        enqueue_sequence=1,
                        enqueued_at=0,
                    ),
                )
            ),
        ),
        ContextCompactionEnvelope(
            **_common(5),
            type="context_compaction",
            payload=ContextCompaction(
                boundary_id="boundary-1",
                state="compacting",
                trigger="explicit",
            ),
        ),
        PermissionRequestEnvelope(**_common(6), type="permission_request", payload=permission),
        PermissionOutcomeEnvelope(**_common(7), type="permission_outcome", payload=outcome),
        ConnectionEnvelope(
            **_common(8),
            type="connection",
            payload=ConnectionPayload(state="ready", detail="Ready", supports_steer=True),
        ),
        ProtocolUpdateRejectedEnvelope.model_validate(
            {
                **_common(9),
                "type": "protocol_update_rejected",
                "payload": {
                    "rejected_session_update": "future_update",
                    "reason": "Unsupported",
                    "status": "Agent sent an unsupported update",
                },
            }
        ),
        HumanEchoEnvelope(
            **_common(10),
            type="human_echo",
            payload=HumanEcho(client_message_id="client-1", prompt=_prompt()),
        ),
        TerminalStateEnvelope(
            **_common(11),
            type="terminal_state",
            payload=ConversationTerminalState(
                terminal_id="terminal-1",
                lifecycle="active",
                terminal_output=TerminalOutputResponse(
                    output="completed\n",
                    truncated=True,
                    exit_status=TerminalExitStatus(exit_code=0),
                ),
            ),
        ),
    ]
    assert [SERVER_ENVELOPE_ADAPTER.validate_python(item).type for item in envelopes] == [
        "acp_session_update",
        "activity",
        "delivery_receipt",
        "queue_snapshot",
        "context_compaction",
        "permission_request",
        "permission_outcome",
        "connection",
        "protocol_update_rejected",
        "human_echo",
        "terminal_state",
    ]
    serialized = serialize_params(envelopes[0])
    assert serialized["wireVersion"] == 1
    assert serialized["type"] == "acp_session_update"
    assert serialized["payload"]["sessionId"] == SESSION_ID
    assert serialized["payload"]["update"]["sessionUpdate"] == "agent_thought_chunk"


def test_terminal_state_preserves_exact_sdk_output_variants() -> None:
    running_output = TerminalOutputResponse(output="still running\n", truncated=False)
    running = ConversationTerminalState(
        terminal_id="terminal-running",
        lifecycle="active",
        terminal_output=running_output,
    )
    exited = ConversationTerminalState(
        terminal_id="terminal-exited",
        lifecycle="active",
        terminal_output=TerminalOutputResponse(
            output="done\n",
            truncated=False,
            exit_status=TerminalExitStatus(exit_code=0),
        ),
    )
    truncated = ConversationTerminalState(
        terminal_id="terminal-truncated",
        lifecycle="active",
        terminal_output=TerminalOutputResponse(output="tail\n", truncated=True),
    )
    released = ConversationTerminalState(
        terminal_id="terminal-released",
        lifecycle="released",
        terminal_output=TerminalOutputResponse(
            output="released\n",
            truncated=False,
            exit_status=TerminalExitStatus(signal="SIGTERM"),
        ),
    )

    assert running.terminal_output is running_output
    assert running.terminal_output.exit_status is None
    assert exited.terminal_output.exit_status is not None
    assert exited.terminal_output.exit_status.exit_code == 0
    assert truncated.terminal_output.truncated is True
    assert released.lifecycle == "released"
    assert serialize_params(released) == {
        "terminalId": "terminal-released",
        "lifecycle": "released",
        "terminalOutput": {
            "output": "released\n",
            "truncated": False,
            "exitStatus": {"signal": "SIGTERM"},
        },
    }


@pytest.mark.parametrize(
    "raw_terminal_state",
    [
        {
            "terminalId": "terminal-1",
            "lifecycle": "unknown",
            "terminalOutput": {"output": "", "truncated": False},
        },
        {
            "terminalId": "",
            "lifecycle": "active",
            "terminalOutput": {"output": "", "truncated": False},
        },
        {
            "terminalId": "terminal-1",
            "lifecycle": "active",
            "terminalOutput": {"output": "missing truncated"},
        },
        {
            "terminalId": "terminal-1",
            "lifecycle": "active",
            "terminalOutput": {"output": "", "truncated": False},
            "extra": "not allowed",
        },
    ],
)
def test_terminal_state_rejects_invalid_or_partial_payloads(
    raw_terminal_state: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ConversationTerminalState.model_validate(
            raw_terminal_state,
            strict=True,
            by_alias=True,
            by_name=False,
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("wireVersion", 2),
        ("employeeId", ""),
        ("entityKind", "day"),
        ("entityId", ""),
        ("acpSessionId", ""),
        ("bindingGeneration", 0),
        ("sequence", 0),
    ],
)
def test_terminal_state_envelope_serializes_exact_payload_and_validates_common_identity(
    field: str,
    invalid_value: object,
) -> None:
    envelope = TerminalStateEnvelope(
        **_common(12),
        type="terminal_state",
        payload=ConversationTerminalState(
            terminal_id="terminal-1",
            lifecycle="active",
            terminal_output=TerminalOutputResponse(
                output="line one\nline two\n",
                truncated=True,
                exit_status=TerminalExitStatus(exit_code=7),
            ),
        ),
    )
    assert serialize_params(envelope) == {
        "wireVersion": 1,
        "type": "terminal_state",
        "employeeId": "employee-1",
        "entityKind": "ticket",
        "entityId": "ticket-1",
        "acpSessionId": SESSION_ID,
        "bindingGeneration": 2,
        "sequence": 12,
        "payload": {
            "terminalId": "terminal-1",
            "lifecycle": "active",
            "terminalOutput": {
                "output": "line one\nline two\n",
                "truncated": True,
                "exitStatus": {"exitCode": 7},
            },
        },
    }

    raw_envelope = serialize_params(envelope)
    raw_envelope[field] = invalid_value
    with pytest.raises(ValidationError):
        SERVER_ENVELOPE_ADAPTER.validate_python(
            raw_envelope,
            strict=True,
            by_alias=True,
            by_name=False,
        )


@pytest.mark.parametrize(
    "envelope_factory",
    [
        lambda: AcpSessionUpdateEnvelope(
            **_common(),
            type="acp_session_update",
            payload=SessionNotification(
                session_id="other-session",
                update=AgentThoughtChunk(
                    session_update="agent_thought_chunk", content=_text("thought")
                ),
            ),
        ),
        lambda: QueueSnapshotEnvelope(
            **_common(),
            type="queue_snapshot",
            payload=QueueSnapshot(
                items=(
                    QueuedPrompt(
                        client_message_id="client-1",
                        prompt=_prompt("other-session"),
                        enqueue_sequence=1,
                        enqueued_at=0,
                    ),
                )
            ),
        ),
        lambda: HumanEchoEnvelope(
            **_common(),
            type="human_echo",
            payload=HumanEcho(client_message_id="client-1", prompt=_prompt("other-session")),
        ),
    ],
)
def test_server_envelopes_reject_mismatched_inner_session_ids(envelope_factory: object) -> None:
    with pytest.raises(ValidationError):
        envelope_factory()  # type: ignore[operator]


def test_server_envelopes_reject_nested_sequence_and_reset_generation_mismatch() -> None:
    with pytest.raises(ValidationError):
        ActivityEnvelope(
            **_common(2),
            type="activity",
            payload=ConversationActivity(state="working", detail="Working", sequence=1),
        )
    with pytest.raises(ValidationError):
        ConnectionEnvelope(
            **_common(2),
            type="connection",
            payload=ConnectionPayload(
                state="reset",
                detail="Reset",
                supports_steer=True,
                reset_binding_generation=3,
            ),
        )
    permission = ConversationPermissionRequest(
        request_id="permission-1",
        employee_id="employee-1",
        backend_key="scripted",
        request=_permission_request(),
        lifecycle="pending",
        deadline_at=300,
        opened_sequence=1,
    )
    with pytest.raises(ValidationError):
        PermissionRequestEnvelope(**_common(2), type="permission_request", payload=permission)
    outcome = ConversationPermissionOutcome(
        request_id="permission-1",
        response=RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
        ),
        settled_sequence=1,
    )
    with pytest.raises(ValidationError):
        PermissionOutcomeEnvelope(**_common(2), type="permission_outcome", payload=outcome)


@pytest.mark.parametrize(
    ("state", "supports_steer"),
    [("reset", True), ("ready", True), ("closed", False), ("error", False)],
)
def test_connection_payload_accepts_every_state(
    state: str,
    supports_steer: bool,
) -> None:
    payload = ConnectionPayload(
        state=state,  # type: ignore[arg-type]
        detail="Visible connection state",
        supports_steer=supports_steer,
        reset_binding_generation=2 if state == "reset" else None,
    )
    envelope = ConnectionEnvelope(**_common(1), type="connection", payload=payload)
    assert envelope.payload.state == state
    assert envelope.payload.supports_steer is supports_steer
    with pytest.raises(ValidationError):
        ConnectionPayload(  # type: ignore[arg-type]
            state="future",
            detail="Unknown",
            supports_steer=supports_steer,
        )


@pytest.mark.parametrize(
    "raw_payload",
    [
        {"state": "ready", "detail": "Ready"},
        {"state": "ready", "detail": "Ready", "supportsSteer": None},
        {"state": "ready", "detail": "Ready", "supportsSteer": "true"},
        {"state": "ready", "detail": "Ready", "supportsSteer": 1},
        {
            "state": "ready",
            "detail": "Ready",
            "supportsSteer": True,
            "supportsQueue": True,
        },
    ],
)
def test_connection_payload_strictly_requires_only_a_boolean_steer_capability(
    raw_payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ConnectionPayload.model_validate(
            raw_payload,
            by_alias=True,
            by_name=False,
        )


def test_unknown_missing_and_partial_updates_fail_closed_without_raw_text() -> None:
    employee = _employee()
    raw_updates = [
        {
            "sessionId": SESSION_ID,
            "update": {
                "sessionUpdate": "future_update",
                "content": {"type": "text", "text": "raw secret"},
            },
        },
        {"sessionId": SESSION_ID, "update": {"sessionUpdate": "agent_thought_chunk"}},
        {"sessionId": SESSION_ID, "update": {}},
        {
            "session_id": SESSION_ID,
            "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "outer snake-case secret"},
            },
        },
        {
            "sessionId": SESSION_ID,
            "update": {
                "session_update": "agent_thought_chunk",
                "content": {"type": "text", "text": "inner snake-case secret"},
            },
        },
    ]
    envelopes = [
        session_update_envelope_or_rejection(
            raw_notification=raw,
            employee=employee,
            acp_session_id=SESSION_ID,
            binding_generation=1,
            sequence=index,
        )
        for index, raw in enumerate(raw_updates, start=1)
    ]
    assert all(isinstance(item, ProtocolUpdateRejectedEnvelope) for item in envelopes)
    assert [item.payload.rejected_session_update for item in envelopes] == [
        "future_update",
        "agent_thought_chunk",
        "missing",
        "agent_thought_chunk",
        "missing",
    ]
    assert all(item.payload.status == "Agent sent an unsupported update" for item in envelopes)
    assert "raw secret" not in json.dumps([serialize_params(item) for item in envelopes])
    serialized_envelopes = json.dumps([serialize_params(item) for item in envelopes])
    assert "outer snake-case secret" not in serialized_envelopes
    assert "inner snake-case secret" not in serialized_envelopes


def test_browser_action_union_covers_all_actions_and_invalid_action_is_not_agent_rejection() -> (
    None
):
    actions = [
        AttachAction(type="attach", employee_id="employee-1"),
        PromptAction(
            type="prompt",
            employee_id="employee-1",
            client_message_id="client-1",
            prompt=_prompt(),
            delivery_choice="normal",
        ),
        CancelAction(type="cancel", employee_id="employee-1"),
        NewConversationAction(type="new_conversation", employee_id="employee-1"),
        PermissionResponseAction(
            type="permission_response",
            employee_id="employee-1",
            request_id="permission-1",
            option_id="allow-once",
        ),
    ]
    assert [BROWSER_ACTION_ADAPTER.validate_python(action).type for action in actions] == [
        "attach",
        "prompt",
        "cancel",
        "new_conversation",
        "permission_response",
    ]
    with pytest.raises(ValidationError) as error:
        validate_browser_action({"type": "prompt", "employeeId": "employee-1"})
    assert "protocol_update_rejected" not in str(error.value)
    with pytest.raises(ValidationError):
        validate_browser_action({"type": "unknown_action", "employeeId": "employee-1", "data": {}})
    with pytest.raises(ValidationError):
        validate_browser_action({"type": "attach", "employee_id": "employee-1"})
    with pytest.raises(ValidationError):
        validate_browser_action(
            {
                "type": "attach",
                "employeeId": "employee-1",
                "lastSeenSequence": "2",
            }
        )


def test_permission_timeout_is_the_single_frozen_default() -> None:
    assert CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS == 300


def test_python_and_browser_dependency_pins_and_forbidden_absences() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    assert "agent-client-protocol==0.11.0" in pyproject["project"]["dependencies"]
    requirements = (REPOSITORY_ROOT / "requirements.txt").read_text().splitlines()
    assert requirements.count("agent-client-protocol==0.11.0") == 1
    assert version("agent-client-protocol") == "0.11.0"

    package = json.loads((REPOSITORY_ROOT / "web" / "package.json").read_text())
    package_lock = json.loads((REPOSITORY_ROOT / "web" / "package-lock.json").read_text())
    assert package["dependencies"]["@agentclientprotocol/sdk"] == "1.2.1"
    assert package["dependencies"]["zustand"] == "5.0.13"
    assert package["dependencies"]["zod"] == "4.4.3"
    assert package_lock["packages"]["node_modules/@agentclientprotocol/sdk"] == {
        "version": "1.2.1",
        "resolved": "https://registry.npmjs.org/@agentclientprotocol/sdk/-/sdk-1.2.1.tgz",
        "integrity": (
            "sha512-jwYUdOQR7tc+Zfch53VL4JJyUNK/46q03uUTYb+PjECsmnNl94XFXOfYLJ8RBpM"
            "NidXd1rpOAVgb0vqD98xImA=="
        ),
        "license": "Apache-2.0",
        "peerDependencies": {"zod": "^3.25.0 || ^4.0.0"},
    }
    for forbidden in ("@acp-components/core", "react", "react-dom", "acp-ui"):
        assert forbidden not in package["dependencies"]
        assert forbidden not in package["devDependencies"]
        assert f"node_modules/{forbidden}" not in package_lock["packages"]


def test_vendor_provenance_hashes_license_and_closed_import_graph() -> None:
    upstream_hashes = {
        "src/types/index.ts": "7e85ca21deb274ed4d28311e8249a34632f2a86d0cedace28cee379d27b8cfd4",
        "src/store/sessionStore.ts": (
            "6d89afc95fbc2758d00293feb6da2c234781403e49abb3dc1d54a87aa34d902f"
        ),
        "src/utils/id.ts": "74deabced24a6e5342c2aa43c0df6b515d45a0496d144ce792aa74051315a939",
        "src/transport/types.ts": (
            "c81be948d5dc9c458ad6fe06ae262154ee242e33f58cd6848e5bab79b9d22343"
        ),
    }
    local_hashes = {
        **upstream_hashes,
        "src/store/sessionStore.ts": (
            "bc6f745bdab898243aa5566e3333a968ba1c52b95156ff4b57a0a6e912203aaa"
        ),
    }
    source_files = sorted(
        path.relative_to(VENDOR_ROOT).as_posix() for path in VENDOR_ROOT.rglob("*.ts")
    )
    assert source_files == sorted(local_hashes)
    for relative_path, expected_hash in local_hashes.items():
        actual_hash = hashlib.sha256((VENDOR_ROOT / relative_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash

    provenance = (VENDOR_ROOT / "UPSTREAM.md").read_text()
    assert "https://github.com/zvzuola/acp-components" in provenance
    assert "525a9d83c5ace577ac0417bf82bf983da4042663" in provenance
    assert "2026-07-19" in provenance
    assert 'declares `"license": "MIT"`' in provenance
    assert re.search(r"did\s+not\s+contain a standalone license file", provenance)
    assert "Intentional local corrections" in provenance
    assert "ACP-03 modifies only `src/store/sessionStore.ts`" in provenance
    for expected_hash in upstream_hashes.values():
        assert expected_hash in provenance
    for expected_hash in local_hashes.values():
        assert expected_hash in provenance
    license_text = (VENDOR_ROOT / "LICENSE").read_text()
    assert "MIT License" in license_text
    assert "Permission is hereby granted, free of charge" in license_text

    allowed_packages = {"@agentclientprotocol/sdk", "zustand/vanilla"}
    for relative_path in source_files:
        source_path = VENDOR_ROOT / relative_path
        source = source_path.read_text()
        imports = re.findall(r"from ['\"]([^'\"]+)['\"]", source)
        for imported in imports:
            if imported.startswith("."):
                candidate = (source_path.parent / imported).resolve()
                resolved = candidate.with_suffix(".ts") if candidate.suffix == "" else candidate
                if candidate.is_dir():
                    resolved = candidate / "index.ts"
                assert resolved.is_relative_to(VENDOR_ROOT.resolve())
                assert resolved.exists()
            else:
                assert imported in allowed_packages


def test_canonical_python_fixtures_are_current() -> None:
    check_or_write_fixtures()
