"""Protocol 22's identity, version admission, mailbox wire values and manifest rule.

Risk: a Mac and the VPS disagree about who may speak (versions, identity shape) or what
a body means, and work is refused or misread with no error until both halves run. The
identity and manifest cases move here from test_executor_control_messages.py, which
dies with the WebSocket; the mailbox shapes had no coverage. Layer: pure values, with
the real codecs (type_adapter, encode_bounded, decode_bounded) and the recorded manifest.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.message_content import MessageText
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import (
    MAC_NEEDS_UPDATE,
    MAC_VERSION_INCOMPATIBLE,
    ChildReference,
    ExecutorIdentity,
    ProtocolRefused,
    TurnToken,
    parse_identity,
    refusal_reason_for_versions,
    supports_executor_versions,
    validate_identity,
)
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReport,
    CommandResult,
    IpcWritePayload,
    LeaseRequest,
    MailboxCommand,
    MailboxOperation,
    MailboxPollReply,
    ModelListRequest,
    WaitingConversation,
    WorkingTurn,
    WritePrompt,
    as_object,
    decode_bounded,
    encode_bounded,
    type_adapter,
)
from planner.executor.protocol_schema import (
    ProtocolManifest,
    check_protocol_manifest,
    protocol_manifest,
)

INCARNATION = str(uuid4())
CHILD = ChildReference(INCARNATION, str(uuid4()), "conversation")
TURN = TurnToken("conversation", 3)


def _identity_payload(**changes: object) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "application_revision": "revision",
        "spool_format_version": SPOOL_FORMAT_VERSION,
        "executor_incarnation": INCARNATION,
        **changes,
    }


def test_identity_parses_with_and_without_the_rollback_generation() -> None:
    """A handoff.json from the previous runtime carries a generation; this one need not."""
    without = parse_identity(_identity_payload())
    assert without.connection_generation == 0
    with_generation = parse_identity(_identity_payload(connection_generation=7, future=True))
    assert with_generation.connection_generation == 7
    assert with_generation.executor_incarnation == INCARNATION
    assert as_object(without)["connection_generation"] == 0
    for bad in (
        _identity_payload(connection_generation=True),
        _identity_payload(connection_generation="1"),
        _identity_payload(executor_incarnation="not a uuid"),
        _identity_payload(protocol_version=True),
        {key: value for key, value in _identity_payload().items() if key != "application_revision"},
    ):
        with pytest.raises(ProtocolRefused):
            parse_identity(bad)


@pytest.mark.parametrize(
    "protocol_version,spool_format_version,reason",
    [
        (21, 5, MAC_NEEDS_UPDATE),
        (20, 5, MAC_NEEDS_UPDATE),
        (22, 4, MAC_NEEDS_UPDATE),
        (23, 5, MAC_VERSION_INCOMPATIBLE),
        (22, 6, MAC_VERSION_INCOMPATIBLE),
    ],
)
def test_only_protocol_22_on_spool_5_is_admitted(
    protocol_version: int, spool_format_version: int, reason: str
) -> None:
    assert supports_executor_versions(22, 5)
    validate_identity(ExecutorIdentity(22, "revision", 5, INCARNATION))
    assert not supports_executor_versions(protocol_version, spool_format_version)
    assert refusal_reason_for_versions(protocol_version, spool_format_version) == reason
    identity = ExecutorIdentity(protocol_version, "revision", spool_format_version, INCARNATION)
    with pytest.raises(ProtocolRefused):
        validate_identity(identity)
    validate_identity(identity, check_versions=False)
    assert not supports_executor_versions(True, 5)
    assert not supports_executor_versions(22, True)


def _prompt_command() -> ChildCommand:
    return ChildCommand(
        str(uuid4()),
        CHILD,
        ChildOperation.write_prompt,
        TURN,
        WritePrompt(
            content=(MessageText("hello"),),
            sender_content=(MessageText("hello"),),
            sender_label="owner",
            mode=PromptDeliveryMode.queue,
        ),
    )


def test_mailbox_values_round_trip_and_ignore_unknown_fields() -> None:
    """The next protocol can add a field without a lockstep release of both halves."""
    command = _prompt_command()
    poll = MailboxPollReply(
        (
            MailboxCommand(1, command.command_id, 100, child_command=command),
            MailboxCommand(2, "list", 101, model_list_request=ModelListRequest("codex", True)),
        )
    )
    assert [entry.operation for entry in poll.commands] == [
        MailboxOperation.write_prompt,
        MailboxOperation.read_model_list,
    ]
    raw = json.loads(encode_bounded(poll))
    raw["future"] = 1
    raw["commands"][0]["future"] = {"nested": True}
    raw["commands"][0]["child_command"]["arguments"]["future_argument"] = True
    assert decode_bounded(json.dumps(raw).encode(), MailboxPollReply) == poll

    lease = LeaseRequest(
        identity=ExecutorIdentity(22, "revision", 5, INCARNATION),
        process_id=42,
        mac_wall_clock_ms=1,
        queue_depth=2,
        oldest_item_age_ms=5,
        waiting_conversations=(WaitingConversation("conversation", 9),),
        waiting_conversations_truncated=False,
        working_turns=(WorkingTurn(CHILD, TURN, tool_running=True),),
        admission_stopped=False,
    )
    raw = json.loads(encode_bounded(lease))
    assert raw["going_to_sleep"] is False
    del raw["going_to_sleep"]
    raw["identity"].pop("connection_generation")
    assert decode_bounded(json.dumps(raw).encode(), LeaseRequest) == lease

    outcome = CommandOutcome(command.command_id, CHILD, CommandDisposition.refused, {"reason": "x"})
    report = CommandOutcomeReport((CommandResult(command.command_id, child_outcome=outcome),))
    assert decode_bounded(encode_bounded(report), CommandOutcomeReport) == report


def test_a_start_ignores_unknown_fields_at_every_level_but_local_ipc_stays_strict() -> None:
    """A start nests its resolved start and role materials, each once a strict level.
    Unknown fields are ignored there too, while a malformed command and an unknown IPC
    field are still refused."""
    command = type_adapter(ChildCommand).validate_python(
        {
            "command_id": "start",
            "child": as_object(CHILD),
            "operation": "start",
            "turn": None,
            "arguments": {
                "resolved_start": {
                    "conversation_id": "conversation",
                    "backend_key": "codex",
                    "model": "fixture",
                    "reasoning_effort": None,
                    "role_materials": {"role_text": "Worker", "identity_environment_variables": []},
                    "workspace_folder": "/tmp/fixture",
                    "access": "full",
                },
                "canonical_skill_snapshot": {"sources": [], "required_skill_names": []},
            },
        }
    )
    raw = as_object(command)
    raw["future_envelope"] = {"anything": True}
    arguments = cast(dict[str, Any], raw["arguments"])
    arguments["future_argument"] = True
    arguments["resolved_start"]["future_start"] = True
    arguments["resolved_start"]["role_materials"]["future_material"] = True
    assert type_adapter(ChildCommand).validate_python(raw) == command
    for change in ({"operation": "stop"}, {"child": {"child_id": "missing original identity"}}):
        with pytest.raises(ValidationError):
            type_adapter(ChildCommand).validate_python({**deepcopy(raw), **change})
    with pytest.raises(ValidationError):
        type_adapter(IpcWritePayload).validate_python(
            {"operation": "ticket.edit", "arguments": {}, "unrecognized": True}
        )


def test_a_mailbox_command_carries_exactly_one_payload_under_its_own_id() -> None:
    command = _prompt_command()
    for raw in (
        {"command_sequence": 1, "command_id": "x", "created_at_vps_ms": 1},
        {
            "command_sequence": 1,
            "command_id": command.command_id,
            "created_at_vps_ms": 1,
            "child_command": as_object(command),
            "model_list_request": {"backend_key": "codex", "refresh": False},
        },
        {
            "command_sequence": 1,
            "command_id": "another id",
            "created_at_vps_ms": 1,
            "child_command": as_object(command),
        },
    ):
        with pytest.raises(ValidationError):
            type_adapter(MailboxCommand).validate_python(raw)


def test_bounded_codec_refuses_empty_and_oversized_bodies() -> None:
    large = ModelListRequest("x" * MAX_ENVELOPE_BYTES, False)
    with pytest.raises(ProtocolRefused):
        encode_bounded(large)
    body = encode_bounded(large, limit=2 * MAX_ENVELOPE_BYTES)
    with pytest.raises(ProtocolRefused):
        decode_bounded(body, ModelListRequest)
    assert decode_bounded(body, ModelListRequest, limit=len(body)) == large
    with pytest.raises(ProtocolRefused):
        decode_bounded(b"", ModelListRequest)


def test_protocol_manifest_detects_schema_edits_and_cannot_hide_them_by_updating_snapshot() -> None:
    fixture = Path(__file__).parents[1] / "fixtures/executor_protocol/manifest.json"
    recorded = cast(ProtocolManifest, json.loads(fixture.read_text()))
    actual = protocol_manifest()
    check_protocol_manifest(actual, recorded)
    changed: ProtocolManifest = {**actual, "schema_sha256": "changed nested event field"}
    with pytest.raises(ValueError, match="schema changed"):
        check_protocol_manifest(changed, recorded)
    with pytest.raises(ValueError, match="without an increase of protocol_version"):
        check_protocol_manifest(changed, changed, recorded)
    increased: ProtocolManifest = {**changed, "protocol_version": actual["protocol_version"] + 1}
    check_protocol_manifest(increased, increased, recorded)
    spool_changed: ProtocolManifest = {**actual, "spool_schema_sha256": "changed table"}
    with pytest.raises(ValueError, match="spool_format_version"):
        check_protocol_manifest(spool_changed, spool_changed, recorded)
    journal_changed: ProtocolManifest = {**actual, "command_journal_schema_sha256": "changed"}
    with pytest.raises(ValueError, match="command_journal_format_version"):
        check_protocol_manifest(journal_changed, journal_changed, recorded)
    # A base manifest from before the journal existed is the journal's starting point.
    before_journal = cast(
        ProtocolManifest,
        {key: value for key, value in recorded.items() if not key.startswith("command_journal_")},
    )
    check_protocol_manifest(journal_changed, journal_changed, before_journal)
