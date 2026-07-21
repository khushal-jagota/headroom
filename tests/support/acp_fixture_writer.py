"""Generate deterministic Python-to-TypeScript ACP contract fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommand,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    ContentToolCallContent,
    Cost,
    CurrentModeUpdate,
    FileEditToolCallContent,
    ImageContentBlock,
    PermissionOption,
    PlanEntry,
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfoUpdate,
    SessionNotification,
    TerminalExitStatus,
    TerminalOutputResponse,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
)
from acp.utils import serialize_params

from planner.conversation import (
    AcpSessionUpdateEnvelope,
    ActivityEnvelope,
    AttachAction,
    CancelAction,
    ConnectionEnvelope,
    ConnectionPayload,
    ContextCompaction,
    ContextCompactionEnvelope,
    ConversationActivity,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationTerminalState,
    DeliveryReceiptEnvelope,
    HumanEcho,
    HumanEchoEnvelope,
    NewConversationAction,
    PermissionOutcomeEnvelope,
    PermissionRequestEnvelope,
    PermissionResponseAction,
    ProgrammaticPrompt,
    ProgrammaticPromptEnvelope,
    PromptAction,
    ProtocolUpdateRejectedEnvelope,
    ProtocolUpdateRejectedPayload,
    QueuedPrompt,
    QueueSnapshot,
    QueueSnapshotEnvelope,
    TerminalStateEnvelope,
    TurnDeliveryReceipt,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIRECTORY = REPOSITORY_ROOT / "tests" / "fixtures" / "acp"
SERVER_FIXTURE_PATH = FIXTURE_DIRECTORY / "server-envelopes-v1.json"
BROWSER_FIXTURE_PATH = FIXTURE_DIRECTORY / "browser-actions-v1.json"
BROWSER_LIVE_REPLAY_FIXTURE_PATH = FIXTURE_DIRECTORY / "browser-live-replay-v1.json"
BROWSER_ENVELOPE_STATES_FIXTURE_PATH = FIXTURE_DIRECTORY / "browser-envelope-states-v1.json"
BROWSER_CONTROLLER_CASES_FIXTURE_PATH = FIXTURE_DIRECTORY / "browser-controller-cases-v1.json"

SESSION_ID = "session-acp-00"
EMPLOYEE_ID = "employee-acp-00"
ENTITY_ID = "ticket-acp-00"


def _text(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def _prompt() -> PromptRequest:
    return PromptRequest(
        session_id=SESSION_ID,
        prompt=[
            _text("Please inspect the typed transcript."),
            ImageContentBlock(type="image", data="aW1hZ2U=", mime_type="image/png"),
        ],
    )


def _common(sequence: int) -> dict[str, Any]:
    return {
        "wire_version": 1,
        "employee_id": EMPLOYEE_ID,
        "entity_kind": "ticket",
        "entity_id": ENTITY_ID,
        "acp_session_id": SESSION_ID,
        "binding_generation": 3,
        "sequence": sequence,
    }


def _update_envelope(sequence: int, update: object) -> AcpSessionUpdateEnvelope:
    return AcpSessionUpdateEnvelope(
        **_common(sequence),
        type="acp_session_update",
        payload=SessionNotification(session_id=SESSION_ID, update=update),
    )


def build_server_envelopes() -> list[object]:
    permission_options = [
        PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
        PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
    ]
    permission_request = RequestPermissionRequest(
        session_id=SESSION_ID,
        tool_call=ToolCallUpdate(
            tool_call_id="tool-1",
            title="Read contract",
            kind="read",
            status="pending",
        ),
        options=permission_options,
    )
    prompt = _prompt()
    envelopes: list[object] = [
        _update_envelope(
            1,
            UserMessageChunk(
                session_update="user_message_chunk",
                message_id="message-user-1",
                content=_text("Please inspect the typed transcript."),
            ),
        ),
        _update_envelope(
            2,
            UserMessageChunk(
                session_update="user_message_chunk",
                message_id="message-user-1",
                content=ImageContentBlock(
                    type="image",
                    data="aW1hZ2U=",
                    mime_type="image/png",
                ),
            ),
        ),
        _update_envelope(
            3,
            AgentThoughtChunk(
                session_update="agent_thought_chunk",
                message_id="message-thought-1",
                content=_text("This stays typed as thought."),
            ),
        ),
        _update_envelope(
            4,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="message-agent-1",
                content=_text("The transcript is typed."),
            ),
        ),
        _update_envelope(
            5,
            ToolCallStart(
                session_update="tool_call",
                tool_call_id="tool-1",
                title="Read contract",
                kind="read",
                status="in_progress",
                content=[
                    ContentToolCallContent(type="content", content=_text("Opening contract.md"))
                ],
            ),
        ),
        _update_envelope(
            6,
            AgentPlanUpdate(
                session_update="plan",
                entries=[
                    PlanEntry(content="Freeze the wire", priority="high", status="in_progress")
                ],
            ),
        ),
        _update_envelope(
            7,
            AvailableCommandsUpdate(
                session_update="available_commands_update",
                available_commands=[
                    AvailableCommand(name="compact", description="Compact the active context")
                ],
            ),
        ),
        _update_envelope(
            8,
            UsageUpdate(session_update="usage_update", used=120, size=1_000),
        ),
        ActivityEnvelope(
            **_common(9),
            type="activity",
            payload=ConversationActivity(state="thinking", detail="Thinking", sequence=9),
        ),
        DeliveryReceiptEnvelope(
            **_common(10),
            type="delivery_receipt",
            payload=TurnDeliveryReceipt(
                client_message_id="client-message-1",
                choice="queue",
                state="queued",
                queue_position=1,
            ),
        ),
        QueueSnapshotEnvelope(
            **_common(11),
            type="queue_snapshot",
            payload=QueueSnapshot(
                items=(
                    QueuedPrompt(
                        client_message_id="client-message-1",
                        prompt=prompt,
                        enqueue_sequence=1,
                        enqueued_at=1_752_960_000,
                    ),
                )
            ),
        ),
        ContextCompactionEnvelope(
            **_common(12),
            type="context_compaction",
            payload=ContextCompaction(
                boundary_id="compaction-1",
                state="compacted",
                trigger="automatic",
            ),
        ),
        PermissionRequestEnvelope(
            **_common(13),
            type="permission_request",
            payload=ConversationPermissionRequest(
                request_id="permission-1",
                employee_id=EMPLOYEE_ID,
                backend_key="scripted",
                request=permission_request,
                lifecycle="pending",
                deadline_at=1_752_960_300,
                opened_sequence=13,
            ),
        ),
        PermissionOutcomeEnvelope(
            **_common(14),
            type="permission_outcome",
            payload=ConversationPermissionOutcome(
                request_id="permission-1",
                response=RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
                ),
                settled_sequence=14,
            ),
        ),
        ConnectionEnvelope(
            **_common(15),
            type="connection",
            payload=ConnectionPayload(
                state="reset",
                detail="Conversation reloaded",
                supports_steer=True,
                reset_binding_generation=3,
            ),
        ),
        ProtocolUpdateRejectedEnvelope(
            **_common(16),
            type="protocol_update_rejected",
            payload=ProtocolUpdateRejectedPayload(
                rejected_session_update="future_update",
                reason="The agent update did not match the pinned ACP schema.",
                status="Agent sent an unsupported update",
            ),
        ),
        HumanEchoEnvelope(
            **_common(17),
            type="human_echo",
            payload=HumanEcho(client_message_id="client-message-1", prompt=prompt),
        ),
        ProgrammaticPromptEnvelope(
            **_common(18),
            type="programmatic_prompt",
            payload=ProgrammaticPrompt(
                prompt_id="worker-prompt-1",
                prompt=prompt,
                source="worker",
            ),
        ),
        TerminalStateEnvelope(
            **_common(19),
            type="terminal_state",
            payload=ConversationTerminalState(
                terminal_id="terminal-1",
                lifecycle="active",
                terminal_output=TerminalOutputResponse(
                    output="line one\nline two\n",
                    truncated=True,
                    exit_status=TerminalExitStatus(exit_code=0),
                ),
            ),
        ),
        ConnectionEnvelope(
            **_common(19),
            type="connection",
            payload=ConnectionPayload(
                state="ready",
                detail="Conversation ready",
                supports_steer=True,
            ),
        ),
        ConnectionEnvelope(
            **_common(20),
            type="connection",
            payload=ConnectionPayload(
                state="closed",
                detail="Conversation closed",
                supports_steer=False,
            ),
        ),
        ConnectionEnvelope(
            **_common(21),
            type="connection",
            payload=ConnectionPayload(
                state="error",
                detail="Conversation unavailable",
                supports_steer=False,
            ),
        ),
    ]
    return envelopes


def build_browser_actions() -> list[object]:
    return [
        AttachAction(
            type="attach",
            employee_id=EMPLOYEE_ID,
            last_seen_binding_generation=3,
            last_seen_sequence=17,
        ),
        PromptAction(
            type="prompt",
            employee_id=EMPLOYEE_ID,
            client_message_id="client-message-2",
            prompt=_prompt(),
            delivery_choice="normal",
        ),
        CancelAction(
            type="cancel",
            employee_id=EMPLOYEE_ID,
            queued_client_message_id="client-message-1",
        ),
        NewConversationAction(type="new_conversation", employee_id=EMPLOYEE_ID),
        PermissionResponseAction(
            type="permission_response",
            employee_id=EMPLOYEE_ID,
            request_id="permission-1",
            option_id="allow-once",
        ),
    ]


def _browser_common(sequence: int, *, generation: int = 1) -> dict[str, Any]:
    return {
        "wire_version": 1,
        "employee_id": "employee-browser",
        "entity_kind": "ticket",
        "entity_id": "ticket-browser",
        "acp_session_id": "session-browser",
        "binding_generation": generation,
        "sequence": sequence,
    }


def _browser_update(sequence: int, update: object) -> AcpSessionUpdateEnvelope:
    return AcpSessionUpdateEnvelope(
        **_browser_common(sequence),
        type="acp_session_update",
        payload=SessionNotification(session_id="session-browser", update=update),
    )


def build_browser_live_replay_stream() -> list[object]:
    stream: list[object] = [
        ConnectionEnvelope(
            **_browser_common(1),
            type="connection",
            payload=ConnectionPayload(
                state="reset",
                detail="Conversation replay starting",
                supports_steer=True,
                reset_binding_generation=1,
            ),
        ),
        _browser_update(
            2,
            UserMessageChunk(
                session_update="user_message_chunk",
                content=_text("Inspect the browser subject."),
            ),
        ),
        _browser_update(
            3,
            AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=_text("This thought remains typed."),
            ),
        ),
        _browser_update(
            4,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=_text("The answer remains public."),
            ),
        ),
        _browser_update(
            5,
            ToolCallStart(
                session_update="tool_call",
                tool_call_id="tool-1",
                title="Edit fixture",
                kind="edit",
                status="in_progress",
                content=[
                    FileEditToolCallContent(
                        type="diff",
                        path="/workspace/fixture.txt",
                        old_text="before\n",
                        new_text="after\n",
                    ),
                    TerminalToolCallContent(type="terminal", terminal_id="terminal-browser"),
                ],
                raw_input={"path": "/workspace/fixture.txt"},
            ),
        ),
        _browser_update(
            6,
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id="tool-1",
                status="completed",
                raw_output={"changed": True},
            ),
        ),
        _browser_update(
            7,
            AgentPlanUpdate(
                session_update="plan",
                entries=[
                    PlanEntry(content="First snapshot", priority="high", status="in_progress")
                ],
            ),
        ),
        _browser_update(
            8,
            AgentPlanUpdate(
                session_update="plan",
                entries=[
                    PlanEntry(content="Replacement snapshot", priority="low", status="completed")
                ],
            ),
        ),
        _browser_update(
            9,
            CurrentModeUpdate(session_update="current_mode_update", current_mode_id="focus"),
        ),
        _browser_update(
            10,
            ConfigOptionUpdate(
                session_update="config_option_update",
                config_options=[
                    SessionConfigOptionSelect(
                        id="model",
                        name="Model",
                        type="select",
                        current_value="one",
                        options=[SessionConfigSelectOption(value="one", name="One")],
                    )
                ],
            ),
        ),
        _browser_update(
            11,
            SessionInfoUpdate(session_update="session_info_update", title="Hermes fixture"),
        ),
        _browser_update(
            12,
            AvailableCommandsUpdate(
                session_update="available_commands_update",
                available_commands=[
                    AvailableCommand(name="compact", description="Compact this conversation")
                ],
            ),
        ),
        _browser_update(
            13,
            UsageUpdate(
                session_update="usage_update",
                used=240,
                size=2_000,
                cost=Cost(amount=0.04, currency="USD"),
            ),
        ),
        TerminalStateEnvelope(
            **_browser_common(14),
            type="terminal_state",
            payload=ConversationTerminalState(
                terminal_id="terminal-browser",
                lifecycle="active",
                terminal_output=TerminalOutputResponse(
                    output="fixture output\n", truncated=False
                ),
            ),
        ),
        ConnectionEnvelope(
            **_browser_common(15),
            type="connection",
            payload=ConnectionPayload(
                state="ready", detail="Conversation ready", supports_steer=True
            ),
        ),
    ]
    return stream


def build_browser_envelope_states() -> list[object]:
    prompt = PromptRequest(session_id="session-browser", prompt=[_text("Queued prompt")])
    permission_options = [
        PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
        PermissionOption(option_id="allow-always", name="Allow always", kind="allow_always"),
        PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
    ]
    permission_request = RequestPermissionRequest(
        session_id="session-browser",
        tool_call=ToolCallUpdate(tool_call_id="tool-permission", title="Run command"),
        options=permission_options,
    )
    values: list[object] = [
        ConnectionEnvelope(
            **_browser_common(1),
            type="connection",
            payload=ConnectionPayload(
                state="reset",
                detail="Conversation reset",
                supports_steer=False,
                reset_binding_generation=1,
            ),
        ),
        _browser_update(
            2,
            AgentThoughtChunk(
                session_update="agent_thought_chunk",
                message_id="agent-1",
                content=_text("Typed thought"),
            ),
        ),
        ActivityEnvelope(
            **_browser_common(3),
            type="activity",
            payload=ConversationActivity(state="working", detail="Working", sequence=3),
        ),
    ]
    for sequence, receipt_state in enumerate(
        ("accepted", "queued", "started", "interrupted", "rejected"), start=4
    ):
        values.append(
            DeliveryReceiptEnvelope(
                **_browser_common(sequence),
                type="delivery_receipt",
                payload=TurnDeliveryReceipt(
                    client_message_id="client-browser",
                    choice="queue" if receipt_state == "queued" else "send_now",
                    state=receipt_state,
                    queue_position=1 if receipt_state == "queued" else None,
                    reason="Agent rejected prompt" if receipt_state == "rejected" else None,
                ),
            )
        )
    values.extend(
        [
            QueueSnapshotEnvelope(
                **_browser_common(9),
                type="queue_snapshot",
                payload=QueueSnapshot(
                    items=(
                        QueuedPrompt(
                            client_message_id="queue-1",
                            prompt=prompt,
                            enqueue_sequence=1,
                            enqueued_at=1_752_960_000,
                        ),
                        QueuedPrompt(
                            client_message_id="queue-2",
                            prompt=prompt,
                            enqueue_sequence=2,
                            enqueued_at=1_752_960_001,
                        ),
                    )
                ),
            ),
            QueueSnapshotEnvelope(
                **_browser_common(10),
                type="queue_snapshot",
                payload=QueueSnapshot(items=()),
            ),
            ContextCompactionEnvelope(
                **_browser_common(11),
                type="context_compaction",
                payload=ContextCompaction(
                    boundary_id="explicit-boundary", state="compacting", trigger="explicit"
                ),
            ),
            ContextCompactionEnvelope(
                **_browser_common(12),
                type="context_compaction",
                payload=ContextCompaction(
                    boundary_id="explicit-boundary",
                    state="compacted",
                    trigger="explicit",
                ),
            ),
            ContextCompactionEnvelope(
                **_browser_common(13),
                type="context_compaction",
                payload=ContextCompaction(
                    boundary_id="automatic-boundary", state="compacting", trigger="automatic"
                ),
            ),
            ContextCompactionEnvelope(
                **_browser_common(14),
                type="context_compaction",
                payload=ContextCompaction(
                    boundary_id="automatic-boundary",
                    state="failed",
                    trigger="automatic",
                    reason="Summary capture failed",
                ),
            ),
            HumanEchoEnvelope(
                **_browser_common(15),
                type="human_echo",
                payload=HumanEcho(client_message_id="client-browser", prompt=prompt),
            ),
            PermissionRequestEnvelope(
                **_browser_common(16),
                type="permission_request",
                payload=ConversationPermissionRequest(
                    request_id="permission-browser",
                    employee_id="employee-browser",
                    backend_key="scripted",
                    request=permission_request,
                    lifecycle="pending",
                    deadline_at=1_752_960_300,
                    opened_sequence=16,
                ),
            ),
            PermissionOutcomeEnvelope(
                **_browser_common(17),
                type="permission_outcome",
                payload=ConversationPermissionOutcome(
                    request_id="permission-browser",
                    response=RequestPermissionResponse(
                        outcome=AllowedOutcome(outcome="selected", option_id="allow-once")
                    ),
                    settled_sequence=17,
                ),
            ),
            TerminalStateEnvelope(
                **_browser_common(18),
                type="terminal_state",
                payload=ConversationTerminalState(
                    terminal_id="terminal-browser",
                    lifecycle="active",
                    terminal_output=TerminalOutputResponse(
                        output="partial output\n", truncated=True
                    ),
                ),
            ),
            TerminalStateEnvelope(
                **_browser_common(19),
                type="terminal_state",
                payload=ConversationTerminalState(
                    terminal_id="terminal-browser",
                    lifecycle="released",
                    terminal_output=TerminalOutputResponse(
                        output="complete output\n",
                        truncated=False,
                        exit_status=TerminalExitStatus(signal="TERM"),
                    ),
                ),
            ),
            ProtocolUpdateRejectedEnvelope(
                **_browser_common(20),
                type="protocol_update_rejected",
                payload=ProtocolUpdateRejectedPayload(
                    rejected_session_update="future_update",
                    reason="Unknown update",
                    status="Agent sent an unsupported update",
                ),
            ),
            ConnectionEnvelope(
                **_browser_common(21),
                type="connection",
                payload=ConnectionPayload(
                    state="ready", detail="Conversation ready", supports_steer=False
                ),
            ),
        ]
    )
    return values


def build_browser_controller_cases() -> dict[str, object]:
    reset = ConnectionEnvelope(
        **_browser_common(1),
        type="connection",
        payload=ConnectionPayload(
            state="reset",
            detail="Conversation reset",
            supports_steer=True,
            reset_binding_generation=1,
        ),
    )
    exact = ActivityEnvelope(
        **_browser_common(2),
        type="activity",
        payload=ConversationActivity(state="idle", detail="Idle", sequence=2),
    )
    higher_reset = ConnectionEnvelope(
        **_browser_common(1, generation=2),
        type="connection",
        payload=ConnectionPayload(
            state="reset",
            detail="New conversation",
            supports_steer=False,
            reset_binding_generation=2,
        ),
    )
    return {
        "valid": {
            "reset": serialize_params(reset),
            "exactNext": serialize_params(exact),
            "higherGenerationReset": serialize_params(higher_reset),
            "humanEchoGrouping": [
                serialize_params(reset),
                serialize_params(
                    _browser_update(
                        2,
                        AgentMessageChunk(
                            session_update="agent_message_chunk",
                            content=_text("before"),
                        ),
                    )
                ),
                serialize_params(
                    HumanEchoEnvelope(
                        **_browser_common(3),
                        type="human_echo",
                        payload=HumanEcho(
                            client_message_id="server-human",
                            prompt=PromptRequest(
                                session_id="session-browser", prompt=[_text("human echo")]
                            ),
                        ),
                    )
                ),
                serialize_params(
                    _browser_update(
                        4,
                        AgentMessageChunk(
                            session_update="agent_message_chunk",
                            content=_text("after"),
                        ),
                    )
                ),
            ],
        },
        "rawInvalid": {
            "duplicate": serialize_params(exact),
            "lower": {
                **serialize_params(exact),
                "sequence": 1,
                "payload": {**serialize_params(exact)["payload"], "sequence": 1},
            },
            "forwardGap": {
                **serialize_params(exact),
                "sequence": 4,
                "payload": {**serialize_params(exact)["payload"], "sequence": 4},
            },
            "wrongIdentity": {**serialize_params(exact), "entityId": "ticket-wrong"},
            "staleGeneration": {**serialize_params(exact), "bindingGeneration": 1},
            "higherGenerationNonReset": {
                **serialize_params(exact),
                "bindingGeneration": 2,
            },
            "higherGenerationWrongEntityReset": {
                **serialize_params(higher_reset),
                "entityId": "ticket-wrong",
            },
            "invalidEnvelope": {"wireVersion": 1, "type": "activity"},
        },
    }


def canonical_fixture_bytes(values: list[object]) -> bytes:
    serialized = [serialize_params(value) for value in values]
    return (json.dumps(serialized, indent=2, sort_keys=True) + "\n").encode()


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def check_or_write_fixtures(*, write: bool = False) -> None:
    expected = {
        SERVER_FIXTURE_PATH: canonical_fixture_bytes(build_server_envelopes()),
        BROWSER_FIXTURE_PATH: canonical_fixture_bytes(build_browser_actions()),
        BROWSER_LIVE_REPLAY_FIXTURE_PATH: canonical_json_bytes(
            {
                "live": [serialize_params(value) for value in build_browser_live_replay_stream()],
                "replay": [serialize_params(value) for value in build_browser_live_replay_stream()],
            }
        ),
        BROWSER_ENVELOPE_STATES_FIXTURE_PATH: canonical_fixture_bytes(
            build_browser_envelope_states()
        ),
        BROWSER_CONTROLLER_CASES_FIXTURE_PATH: canonical_json_bytes(
            build_browser_controller_cases()
        ),
    }
    if write:
        FIXTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        for path, content in expected.items():
            path.write_bytes(content)
        return
    for path, content in expected.items():
        if not path.exists():
            raise AssertionError(f"missing canonical ACP fixture: {path}")
        if path.read_bytes() != content:
            raise AssertionError(f"canonical ACP fixture is stale: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="rewrite the committed fixtures")
    arguments = parser.parse_args()
    check_or_write_fixtures(write=arguments.write)


if __name__ == "__main__":
    main()
