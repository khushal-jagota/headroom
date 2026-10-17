"""A chunk sender advances only on a correlated, strictly typed protocol-3 ack."""

from __future__ import annotations

import asyncio
import base64
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION, ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    ConnectionState,
    EnvelopeKind,
    ExecutorHealth,
    ExecutorIdentity,
    FileChunk,
    FileChunkAcknowledgment,
    JsonObject,
    QueuedEnvelope,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor
from planner.executor.vps import ExecutorEndpoint
from planner.executor.wire import (
    FileChunkAcknowledgmentRefused,
    validate_file_chunk_ack,
    write_frame,
)

CHUNK = FileChunk("publication", 4096, base64.b64encode(b"\x00\xff\x10").decode())
VALID: JsonObject = {"publication_id": "publication", "offset": 4096, "byte_count": 3}


def test_valid_chunk_ack_confirms_exact_binary_write() -> None:
    acknowledgment = validate_file_chunk_ack(
        WireMessage(WireMessageKind.file_chunk_ack, "request", VALID),
        CHUNK,
    )
    assert acknowledgment == FileChunkAcknowledgment("publication", 4096, 3)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"publication_id": "publication"},
        {**VALID, "extra": True},
        {**VALID, "publication_id": "another"},
        {**VALID, "publication_id": ""},
        {**VALID, "publication_id": None},
        {**VALID, "offset": 0},
        {**VALID, "offset": -1},
        {**VALID, "offset": True},
        {**VALID, "offset": "4096"},
        {**VALID, "offset": 4096.0},
        {**VALID, "byte_count": 0},
        {**VALID, "byte_count": -1},
        {**VALID, "byte_count": 4},
        {**VALID, "byte_count": True},
        {**VALID, "byte_count": "3"},
        {**VALID, "byte_count": 3.0},
    ],
)
def test_empty_malformed_or_mismatched_chunk_ack_never_advances(payload: JsonObject) -> None:
    with pytest.raises(FileChunkAcknowledgmentRefused):
        validate_file_chunk_ack(
            WireMessage(WireMessageKind.file_chunk_ack, "request", payload), CHUNK
        )


@pytest.mark.parametrize(
    "kind",
    [
        WireMessageKind.file_chunk,
        WireMessageKind.file_accepted,
        WireMessageKind.receipt,
        WireMessageKind.error,
    ],
)
def test_other_success_or_error_reply_is_not_a_chunk_ack(kind: WireMessageKind) -> None:
    with pytest.raises(FileChunkAcknowledgmentRefused, match="file_chunk_ack"):
        validate_file_chunk_ack(WireMessage(kind, "request", VALID), CHUNK)


@pytest.mark.parametrize(
    "chunk",
    [
        replace(CHUNK, content_base64="malformed!"),
        replace(CHUNK, offset=-1),
    ],
)
def test_invalid_expected_chunk_cannot_receive_a_valid_ack(chunk: FileChunk) -> None:
    with pytest.raises(FileChunkAcknowledgmentRefused):
        validate_file_chunk_ack(
            WireMessage(WireMessageKind.file_chunk_ack, "request", VALID), chunk
        )


def test_old_protocol_two_mac_is_refused_by_protocol_three_vps_before_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WorkObservingEndpoint(ExecutorEndpoint):
        received_work = 0

        async def accept_envelope(
            self,
            envelope: QueuedEnvelope,
            identity: ExecutorIdentity,
        ) -> AcceptanceReceipt:
            self.received_work += 1
            return await super().accept_envelope(envelope, identity)

    async def run() -> None:
        assert PROTOCOL_VERSION == 3 and SPOOL_FORMAT_VERSION == 1
        database_path = str(tmp_path / "vps.sqlite")
        connection = connect(database_path)
        create_schema(connection)
        socket_path = tmp_path.parent / ("ack_" + uuid4().hex[:12] + ".sock")
        monkeypatch.setenv("PLAN_EXECUTOR_VPS_SOCKET", str(socket_path))
        endpoint = WorkObservingEndpoint(database_path, RealClock())
        await endpoint.start()
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            identity = ExecutorIdentity(2, "a" * 40, 1, str(uuid4()), 1)
            health = ExecutorHealth(identity, 123, ConnectionState.reconciling, False, 1000, None)
            payload = encode_value(health)
            assert isinstance(payload, dict)
            await write_frame(writer, WireMessage(WireMessageKind.hello, "hello", payload))
            # Pipeline an application frame after the incompatible hello. No
            # application dispatcher or journal replay may follow that handshake.
            try:
                await write_frame(writer, WireMessage(WireMessageKind.envelope, "work", {}))
            except (ConnectionError, OSError):
                pass
            assert await asyncio.wait_for(reader.read(), 2) == b""
            assert endpoint.received_work == 0
            assert endpoint.identity is None and not endpoint.ready
            assert (
                connection.execute("SELECT count(*) FROM executor_connections").fetchone()[0] == 0
            )
            assert connection.execute("SELECT count(*) FROM executor_receipts").fetchone()[0] == 0
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await endpoint.close()
            connection.close()

    asyncio.run(run())


def test_new_protocol_three_mac_is_refused_by_simulated_protocol_two_peer_before_replay(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        assert PROTOCOL_VERSION == 3
        observation = tmp_path / "old_peer_observation"
        peer = r"""
import json, struct, sys
from pathlib import Path
from planner.executor import wire
header = sys.stdin.buffer.read(4)
body = sys.stdin.buffer.read(struct.unpack("!I", header)[0])
hello = json.loads(body)
identity = hello["payload"]["identity"]
wire.PROTOCOL_VERSION = 2
try:
    wire.parse_identity(identity)
except wire.ProtocolRefused:
    Path(sys.argv[1]).write_text(str(identity["protocol_version"]) + ":refused-before-work")
    response = {"kind":"protocol_refused","request_id":hello["request_id"],
                "payload":{"error":"protocol 2 required"}}
    encoded = json.dumps(response).encode()
    sys.stdout.buffer.write(struct.pack("!I",len(encoded)) + encoded)
    sys.stdout.buffer.flush()
else:
    Path(sys.argv[1]).write_text("incorrectly-admitted-new-peer")
"""
        mac = MacExecutor(
            paths=ExecutorPaths(tmp_path / "mac"),
            revision="a" * 40,
            ssh_argv=(sys.executable, "-c", peer, str(observation)),
            panels_server_url="http://unused",
        )
        try:
            pending = mac.spool.mint_envelope(
                conversation_id="conversation",
                kind=EnvelopeKind.cli_write,
                payload={"body": "must not replay"},
            )
            before = {
                path.relative_to(mac.paths.spool): path.read_bytes()
                for path in mac.paths.spool.rglob("*")
                if path.is_file()
            }
            with pytest.raises((ValueError, asyncio.IncompleteReadError, ConnectionError)):
                await asyncio.wait_for(mac._connected(), 3)
            assert observation.read_text() == "3:refused-before-work"
            assert mac.spool.pending() == (pending,)
            assert {
                path.relative_to(mac.paths.spool): path.read_bytes()
                for path in mac.paths.spool.rglob("*")
                if path.is_file()
            } == before
            assert mac.registry.children == {} and mac.registry.outcomes == {}
        finally:
            mac.spool.close()

    asyncio.run(run())
