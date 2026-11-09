"""File transfers as their own requests: uploads into staging, and input downloads.

Risk: unverified or foreign bytes reaching staging (or the served tree before an item
names them), a temporary file left behind by a short, corrupt, stalled or dropped upload,
an upload that waits forever on a client that stopped sending, and an input download
that serves a file the command does not carry, or a command that is finished, withdrawn
or another process's. Gap: the old transfer tests drove WebSocket transfer frames; route
5 and 6's owners (VpsFiles behind endpoint.upload and endpoint.input_file) are new.
Layer: unit, the real VpsExecutor over a real SQLite database and the real staging and
message-file directories, without HTTP (the route adapter is in the integration test).
Setup as in test_executor_command_mailbox.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.requests import ClientDisconnect
from tests.support.executor_presence import lease_request, mac_identity

import planner.executor.vps_files as vps_files
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.message_content import MessageContent, MessageFile, MessageText
from planner.core.clock import RealClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.contracts import CommandSettlementReason
from planner.executor.protocol import (
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReport,
    CommandResult,
    EnvelopeKind,
    FileManifest,
    FileScope,
    FileUploadRequest,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
)
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor

BYTES = b"result bytes"
SETTLES = 5.0


@dataclass
class _World:
    endpoint: VpsExecutor
    identity: ExecutorIdentity
    child: ChildReference
    proxy: MacRemoteChild

    @property
    def staging(self) -> Path:
        return self.endpoint.files.staging

    def staged(self) -> list[str]:
        return sorted(path.name for path in self.staging.iterdir()) if self.staging.exists() else []

    async def upload(
        self,
        chunks: AsyncIterator[bytes],
        manifest: FileManifest | None = None,
        child: ChildReference | None = None,
    ) -> None:
        manifest = manifest or _manifest()
        await self.endpoint.upload(
            self.identity,
            manifest.sha256,
            FileUploadRequest(manifest, child or self.child),
            manifest.byte_count,
            chunks,
        )

    async def settled(self) -> None:
        await asyncio.gather(*self.endpoint._owned, return_exceptions=True)


def _manifest(owner: str = "ticket-1", content: bytes = BYTES) -> FileManifest:
    return FileManifest(
        FileScope.ticket, owner, "out/result.bin", hashlib.sha256(content).hexdigest(), len(content)
    )


async def _chunks(*pieces: bytes) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


@asynccontextmanager
async def _world(tmp_path: Path) -> AsyncIterator[_World]:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    create_schema(connection)
    connection.close()
    endpoint = VpsExecutor(path, RealClock())
    endpoint.presence.returned = None
    identity = mac_identity()
    await endpoint.lease(lease_request(identity))
    child = ChildReference(identity.executor_incarnation, "child", "c")
    endpoint.runs.store.register_child(child, "ticket", "ticket-1", 1)
    proxy = endpoint.runs.recover(child)
    assert proxy is not None
    try:
        yield _World(endpoint, identity, child, proxy)
    finally:
        await endpoint.close()


async def _refused(work: Awaitable[object]) -> ErrorCode:
    with pytest.raises(PlannerError) as raised:
        await work
    return raised.value.code


def test_an_upload_lands_verified_in_staging_and_the_item_naming_it_installs_it(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            manifest = _manifest()
            await world.upload(_chunks(BYTES[:5], BYTES[5:]))
            (staged,) = world.staged()
            assert (world.staging / staged).read_bytes() == BYTES
            store = world.endpoint.inbox.store
            target = store.file_target(manifest)
            assert not target.exists()  # Not served until an item names it.

            item = QueuedEnvelope(
                "mac",
                "c",
                1,
                "item-1",
                EnvelopeKind.backend_event,
                {"child": as_object(world.child)},
                (manifest,),
            )

            def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                assert target.read_bytes() == BYTES  # Installed before the effect.
                return {}

            receipt = await asyncio.to_thread(
                store.accept, item, effect, now_ms(), identity=world.identity
            )
            assert receipt.disposition is ReceiptDisposition.accepted
            assert target.read_bytes() == BYTES and world.staged() == []

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_short_corrupt_or_foreign_upload_is_refused_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            short = await _refused(world.upload(_chunks(BYTES[:-1])))
            corrupt = await _refused(world.upload(_chunks(b"X" * len(BYTES))))
            assert (short, corrupt) == (ErrorCode.validation, ErrorCode.validation)
            other_ticket = await _refused(world.upload(_chunks(BYTES), _manifest("ticket-2")))
            stranger = ChildReference(world.identity.executor_incarnation, "stranger", "c")
            unassigned = await _refused(world.upload(_chunks(BYTES), child=stranger))
            assert (other_ticket, unassigned) == (
                ErrorCode.agent_forbidden,
                ErrorCode.agent_forbidden,
            )
            assert world.staged() == []

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_stalled_or_dropped_upload_discards_its_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vps_files, "FILE_TRANSFER_STALL_SECONDS", 0.2)

    async def exercise() -> None:
        async with _world(tmp_path) as world:
            stopped = asyncio.Event()

            async def stalls() -> AsyncIterator[bytes]:
                yield BYTES[:5]
                await stopped.wait()  # The client stops sending and never ends.
                yield BYTES[5:]

            assert await _refused(world.upload(stalls())) is ErrorCode.retry_later
            assert world.staged() == []

            disconnected = asyncio.Event()

            async def drops() -> AsyncIterator[bytes]:
                yield BYTES[:5]
                await disconnected.wait()
                raise ClientDisconnect()

            caller = asyncio.create_task(world.upload(drops()))
            for _ in range(int(SETTLES / 0.01)):
                if world.staged():
                    break
                await asyncio.sleep(0.01)
            (incoming,) = world.staged()
            assert incoming.startswith(".incoming-")
            caller.cancel()  # The request went away; the owned upload goes on.
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert world.staged() == [incoming]
            disconnected.set()
            await world.settled()
            assert world.staged() == []

    asyncio.run(asyncio.wait_for(exercise(), 20))


async def _prompt(proxy: MacRemoteChild, content: MessageContent, turn: int) -> None:
    await proxy.write_prompt(
        TurnToken("c", turn),
        content,
        sender_label="owner",
        sender_content=content,
        mode=PromptDeliveryMode.queue,
        model_change=None,
        reasoning_effort_change=None,
    )


def test_an_input_file_is_served_only_for_an_open_command_of_the_current_process(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _world(tmp_path) as world:
            endpoint = world.endpoint
            kept = await endpoint.message_files.keep("c", BYTES, media_type="text/plain")
            other = await endpoint.message_files.keep("c", b"other", media_type="text/plain")
            content: MessageContent = (
                MessageText("see attached"),
                MessageFile(kept.stored_file_id, "text/plain", "notes.txt", kept.byte_count),
            )

            def command_ids() -> list[str]:
                connection = connect(endpoint.db_path)
                try:
                    return [
                        row[0]
                        for row in connection.execute(
                            "SELECT command_id FROM executor_commands ORDER BY command_sequence"
                        )
                    ]
                finally:
                    connection.close()

            def missing(
                command_id: str, stored_file_id: str, identity: ExecutorIdentity | None = None
            ) -> Awaitable[ErrorCode]:
                return _refused(
                    endpoint.input_file(identity or world.identity, command_id, stored_file_id)
                )

            await _prompt(world.proxy, content, 1)
            (command_id,) = command_ids()
            served = await endpoint.input_file(world.identity, command_id, kept.stored_file_id)
            assert served.path.read_bytes() == BYTES
            assert (served.sha256, served.byte_count) == (
                hashlib.sha256(BYTES).hexdigest(),
                len(BYTES),
            )
            await endpoint.poll(world.identity, 0, 0)  # Delivered: still served.
            assert (await endpoint.input_file(world.identity, command_id, kept.stored_file_id)) == (
                served
            )
            assert await missing(command_id, other.stored_file_id) is ErrorCode.not_found
            assert (
                await missing(command_id, kept.stored_file_id, mac_identity())
                is ErrorCode.not_found
            )

            outcome = CommandOutcome(
                command_id, world.child, CommandDisposition.succeeded, {"kind": "prompt"}
            )
            await endpoint.record_outcomes(
                world.identity,
                CommandOutcomeReport((CommandResult(command_id, child_outcome=outcome),)),
            )
            assert await missing(command_id, kept.stored_file_id) is ErrorCode.not_found

            await _prompt(world.proxy, content, 2)
            withdrawn = command_ids()[1]
            assert await endpoint.mailbox.withdraw_waiting(
                withdrawn, CommandSettlementReason.delivery_withdrawn
            )
            assert await missing(withdrawn, kept.stored_file_id) is ErrorCode.not_found

    asyncio.run(asyncio.wait_for(exercise(), 20))
