"""WebSocket boundaries replace stream framing; IPC retains its separate framing proof."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from tests.support.executor_control import MessageStream

from planner.execution_identity import ProtocolRefused
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.protocol import (
    ChildCommand,
    WireMessage,
    WireMessageKind,
    decode_message,
    encode_message,
)


def test_plain_control_messages_validate_and_bound_the_whole_payload() -> None:
    message = WireMessage(WireMessageKind.health, "request", {})
    body = encode_message(message)
    assert body.startswith(b"{")
    assert decode_message(body) == message
    assert decode_message(body + b" " * (MAX_ENVELOPE_BYTES - len(body))) == message
    for oversized in (body + b" " * (MAX_ENVELOPE_BYTES + 1 - len(body)), b""):
        with pytest.raises(ProtocolRefused):
            decode_message(oversized)
    with pytest.raises(ProtocolRefused):
        encode_message(WireMessage(WireMessageKind.health, "x" * MAX_ENVELOPE_BYTES, {}))
    for invalid in (
        body + body,
        b"\0\0\0\x01" + body,
        b'{"kind":"unknown"}',
        b'{"kind":"health","request_id":1,"payload":{}}',
    ):
        with pytest.raises(ValidationError):
            decode_message(invalid)


def test_shared_link_matches_nested_replies_refuses_items_and_fails_waiters_on_loss() -> None:
    """No network host: both roles use the same Link under nested bidirectional work."""
    from tests.unit.test_executor_request_deadlines import IDENTITY

    from planner.executor.link import Link

    async def run() -> None:
        class PairedPeer(MessageStream):
            other: PairedPeer

            async def send_bytes(self, data: bytes) -> None:
                await super().send_bytes(data)
                self.other.incoming.put_nowait(data)

            async def aclose(self) -> None:
                await super().aclose()
                self.other.incoming.put_nowait(b"")

        first_peer, second_peer = PairedPeer(), PairedPeer()
        first_peer.other, second_peer.other = second_peer, first_peer
        first = Link(IDENTITY, first_peer)
        second = Link(IDENTITY, second_peer)
        entered = asyncio.Event()

        async def heartbeat(message: WireMessage) -> WireMessage:
            return WireMessage(message.kind, message.request_id, {"alive": True})

        async def work(message: WireMessage) -> WireMessage:
            if message.payload["block"]:
                entered.set()
                await second.request(WireMessageKind.get_file, {})
            response = await second.request(WireMessageKind.health, {})
            return WireMessage(WireMessageKind.read_response, message.request_id, response.payload)

        first.on(WireMessageKind.health, heartbeat)
        second.on(WireMessageKind.read_request, work)
        reading = asyncio.create_task(second.closed())
        try:
            with pytest.raises(ValueError):
                await first.request(WireMessageKind.read_request, {})
            assert not first.is_closing() and not second.is_closing()
            response = await first.request(WireMessageKind.read_request, {"block": False})
            assert response.kind is WireMessageKind.read_response
            assert response.payload == {"alive": True}
            pending = asyncio.create_task(
                first.request(WireMessageKind.read_request, {"block": True})
            )
            await asyncio.wait_for(entered.wait(), 1)
            second.close()
            with pytest.raises(ConnectionError):
                await pending
            await asyncio.gather(*tuple(second._work))
            assert not first._pending and not second._pending
            sent = len(second_peer.sent)
            assert not await second.tell(WireMessageKind.command, {})
            assert len(second_peer.sent) == sent
        finally:
            first.close()
            second.close()
            await asyncio.gather(reading, return_exceptions=True)
            await first.wait_closed()
            await second.wait_closed()

    asyncio.run(run())


@pytest.mark.usefixtures("executor_transport_home")
@pytest.mark.parametrize("reply", ["wrong_kind", "changed_identity", "version_refused"])
def test_mac_refuses_invalid_hello_reply_before_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    """The service must validate hello before exposing the connection to any owner."""
    from planner.executor import mac_service
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.link import Link
    from planner.executor.mac_service import MacService
    from planner.executor.protocol import as_object

    async def run() -> None:
        service = MacService(
            paths=ExecutorPaths(tmp_path / "executor"),
            revision="a" * 40,
            executor_id="test-executor",
            panels_server_url="https://fixture.invalid",
        )
        disconnected = asyncio.Event()

        class Peer(MessageStream):
            async def send(self, message: WireMessage) -> None:
                await super().send(message)
                identity = as_object(service.identity)
                kind = WireMessageKind.hello_accepted
                if reply == "wrong_kind":
                    kind = WireMessageKind.health
                elif reply == "version_refused":
                    kind = WireMessageKind.protocol_refused
                    identity = {"reason": "Mac needs update"}
                else:
                    identity["connection_generation"] = 999
                self.feed_data(encode_message(WireMessage(kind, message.request_id, identity)))

        stream = Peer()

        @asynccontextmanager
        async def dial(*_: object) -> AsyncIterator[Link]:
            link = Link(service.identity, stream)
            try:
                yield link
            finally:
                await link.wait_closed()
                disconnected.set()

        monkeypatch.setattr(mac_service, "dial_link", dial)
        task = asyncio.create_task(service.run())
        try:
            await asyncio.wait_for(disconnected.wait(), 1)
            assert service.connection.link is None
            if reply == "version_refused":
                from planner.executor.protocol import IpcOperation, IpcRequest

                response = await service.gateway.handle(
                    IpcRequest("status", IpcOperation.health, None, None, {})
                )
                assert response.result is not None
                assert response.result["refusal"] == {"reason": "Mac needs update"}
            assert stream.is_closing()
            assert [decode_message(body).kind for body in stream.sent] == [WireMessageKind.hello]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def _compatibility_commands() -> tuple[ChildCommand, ...]:
    from planner.executor.protocol import ChildCommand, type_adapter

    child = {
        "executor_incarnation": "aa09f291-3892-4677-b563-5936017e6c1d",
        "child_id": "native-child",
        "conversation_id": "c_one",
    }
    variants: tuple[tuple[str, dict[str, Any]], ...] = (
        ("start", {
            "resolved_start": {
                "conversation_id": "c_one", "backend_key": "codex", "model": "fixture",
                "reasoning_effort": None, "role_materials": {
                    "role_text": "Worker", "identity_environment_variables": [],
                },
                "workspace_folder": "/tmp/fixture", "access": "full",
            },
            "canonical_skill_snapshot": {"sources": [], "required_skill_names": []},
        }),
        ("write_prompt", {
            "content": [{"text": "Hello"}], "sender_content": [{"text": "Hello"}],
            "sender_label": "Owner", "mode": "queue",
        }),
        ("steer", {"content": [{"text": "Guide"}], "sender_label": "Owner"}),
        ("cancel_running_turn", {}),
        ("answer_permission_ask", {"ask_id": "permission", "option_id": "allow"}),
        ("answer_user_input", {"request_id": "question", "answers": []}),
        ("stop", {}),
    )
    return tuple(
        type_adapter(ChildCommand).validate_python({
            "command_id": operation, "child": child, "operation": operation,
            "turn": {"conversation_id": "c_one", "turn_number": 1}, "arguments": arguments,
        })
        for operation, arguments in variants
    )


def test_previous_peer_accepts_every_current_command_against_frozen_protocol_19() -> None:
    import json

    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

    from planner.executor.protocol import ChildCommand, encode_command_for_peer

    fixture = Path(__file__).parents[1] / "fixtures/executor_protocol/protocol_19_commands.json"
    old = json.loads(fixture.read_text())
    validator = Draft202012Validator(old["command_schema"])
    for command in _compatibility_commands():
        assert isinstance(command, ChildCommand)
        encoded = encode_command_for_peer(command, old["protocol_version"])
        validator.validate(encoded)
        arguments = encoded["arguments"]
        assert isinstance(arguments, dict)
        arguments["future_argument"] = True
        from planner.executor.protocol import type_adapter

        assert type_adapter(ChildCommand).validate_python(encoded) == command
    with pytest.raises(ProtocolRefused):
        encode_command_for_peer(command, 18)


def test_wire_ignores_nested_unknown_fields_without_relaxing_identity_or_local_ipc() -> None:
    from copy import deepcopy
    from uuid import uuid4

    from planner.execution_identity import parse_identity
    from planner.executor.protocol import (
        ChildCommand,
        IpcWritePayload,
        as_object,
        type_adapter,
    )

    command = _compatibility_commands()[0]
    raw = as_object(command)
    # Exercise real wire decoding at every formerly strict argument level.
    raw["future_envelope"] = {"anything": True}
    arguments = raw["arguments"]
    assert isinstance(arguments, dict)
    arguments["future_argument"] = True
    resolved = arguments["resolved_start"]
    assert isinstance(resolved, dict)
    resolved["future_start"] = True
    materials = resolved["role_materials"]
    assert isinstance(materials, dict)
    materials["future_material"] = True
    assert type_adapter(ChildCommand).validate_python(raw) == command
    malformed = deepcopy(raw)
    malformed["operation"] = "stop"
    with pytest.raises(ValidationError):
        type_adapter(ChildCommand).validate_python(malformed)
    malformed = deepcopy(raw)
    malformed["child"] = {"child_id": "missing original identity"}
    with pytest.raises(ValidationError):
        type_adapter(ChildCommand).validate_python(malformed)
    identity = {
        "protocol_version": 19, "application_revision": "old", "spool_format_version": 5,
        "executor_incarnation": str(uuid4()), "connection_generation": 1, "future": True,
    }
    assert parse_identity(identity).protocol_version == 19
    identity["connection_generation"] = True
    with pytest.raises(ProtocolRefused):
        parse_identity(identity)
    with pytest.raises(ValidationError):
        type_adapter(IpcWritePayload).validate_python({
            "operation": "ticket.edit", "arguments": {}, "unrecognized": True,
        })


def test_protocol_manifest_detects_schema_edits_and_cannot_hide_them_by_updating_snapshot() -> None:
    import json
    from typing import cast

    from planner.executor.protocol_schema import (
        ProtocolManifest,
        check_protocol_manifest,
        protocol_manifest,
    )

    fixture = Path(__file__).parents[1] / "fixtures/executor_protocol/manifest.json"
    recorded = cast(ProtocolManifest, json.loads(fixture.read_text()))
    actual = protocol_manifest()
    check_protocol_manifest(actual, recorded)
    changed: ProtocolManifest = {**actual, "schema_sha256": "changed nested event field"}
    with pytest.raises(ValueError, match="schema changed"):
        check_protocol_manifest(changed, recorded)
    with pytest.raises(ValueError, match="without an increase"):
        check_protocol_manifest(changed, changed, recorded)
    increased: ProtocolManifest = {**changed, "protocol_version": actual["protocol_version"] + 1}
    check_protocol_manifest(increased, increased, recorded)
    spool_changed: ProtocolManifest = {**actual, "spool_schema_sha256": "changed table"}
    with pytest.raises(ValueError, match="spool_format_version"):
        check_protocol_manifest(spool_changed, spool_changed, recorded)
