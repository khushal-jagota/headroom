"""Native HTTP/WS boundaries through the isolated trusted TLS proxy."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from starlette.responses import StreamingResponse
from tests.integration.test_executor_end_to_end import boot, eventually

from planner.conversation.backends.contracts import TurnEnded
from planner.executor import app_transport
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.link import ExecutorAppClient
from planner.executor.link_files import get_file, put_file
from planner.executor.protocol import FileManifest, FileScope, FileSnapshot, encode_message

pytestmark = pytest.mark.usefixtures("executor_transport_home")


def test_native_admission_and_generation_file_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            identity = harness.endpoint.runs.identity
            assert identity is not None
            client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            # The production session never trusts the fixture certificate.
            production_client = ExecutorAppClient(harness.proxy.origin, "test-executor", identity)
            try:
                with pytest.raises(aiohttp.ClientConnectorCertificateError):
                    async with production_client.websocket(
                        "/api/executor/connect", MAX_ENVELOPE_BYTES
                    ):
                        pytest.fail("untrusted TLS certificate was accepted")
            finally:
                await production_client.close()
            assert harness.mac.connection.link is not None
            service = harness.endpoint.transfers[identity]

            # This case isolates HTTP body validation; connected item acceptance
            # exercises the real store_upload path in the file-producing event case.
            async def keep_transfer(identity: object, transfer_id: str) -> None:
                pass

            monkeypatch.setattr(harness.endpoint, "store_upload", keep_transfer)
            data = bytes(range(256)) * 1000
            manifest = FileManifest(
                FileScope.ticket,
                "ticket",
                "binary.dat",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
            slot = service.create_upload(manifest)
            url = harness.proxy.origin + "/api/executor/files/" + slot.transfer_id
            try:
                for headers in (
                    {},
                    client.headers() | {"X-Panels-Executor-ID": "other"},
                    client.headers() | {"Origin": harness.proxy.origin},
                ):
                    async with client.session.put(url, headers=headers, data=data) as response:
                        assert response.status == 403
                duplicate = list(client.headers().items()) + [
                    ("X-Panels-Executor-ID", "test-executor")
                ]
                async with client.session.put(url, headers=duplicate, data=data) as response:
                    assert response.status == 403
                stale = client.headers() | {"X-Panels-Executor-Generation": "999"}
                async with client.session.put(url, headers=stale, data=data) as response:
                    assert response.status == 409
                encoded = client.headers() | {"Content-Encoding": "gzip"}
                async with client.session.put(
                    url, headers=encoded, data=gzip.compress(data)
                ) as response:
                    assert response.status == 409
                async with client.session.get(url, headers=client.headers()) as response:
                    assert response.status == 409
                async with client.session.put(url, headers=client.headers(), data=data) as response:
                    assert response.status == 204
                assert service.upload_path(slot.transfer_id).read_bytes() == data
                async with client.session.put(url, headers=client.headers(), data=data) as response:
                    assert response.status == 409
                service.release(slot.transfer_id)
                for body in (data[:-1], data + b"extra", b"x" * len(data)):
                    bad = service.create_upload(manifest)
                    async with client.session.put(
                        harness.proxy.origin + "/api/executor/files/" + bad.transfer_id,
                        headers=client.headers(),
                        data=body,
                    ) as response:
                        assert response.status == 409
                    assert bad.transfer_id not in service.transfers
                source = tmp_path / "input"
                source.write_bytes(data)
                download = await service.create_download_async(FileSnapshot(manifest, source))
                download_url = harness.proxy.origin + "/api/executor/files/" + download.transfer_id
                destination = tmp_path / "download"
                await get_file(harness.mac.connection.link, download, destination)
                assert destination.read_bytes() == data
                async with client.session.get(download_url, headers=client.headers()) as response:
                    assert response.status == 409
                encoded_download = await service.create_download_async(
                    FileSnapshot(manifest, source)
                )
                encoded_destination = tmp_path / "encoded-download"

                def encoded_response(
                    chunks: AsyncIterator[bytes], *, media_type: str
                ) -> StreamingResponse:
                    # Label raw bytes deliberately: decompression stays disabled,
                    # and the downloader must refuse the header before body acceptance.
                    return StreamingResponse(
                        chunks, media_type=media_type, headers={"Content-Encoding": "gzip"}
                    )

                with monkeypatch.context() as response_patch:
                    response_patch.setattr(app_transport, "StreamingResponse", encoded_response)
                    with pytest.raises(ConnectionError, match="executor download refused"):
                        await get_file(
                            harness.mac.connection.link, encoded_download, encoded_destination
                        )
                assert encoded_destination.read_bytes() == b""
                async with client.session.get(
                    harness.proxy.origin + "/api/executor/files/" + "f" * 32,
                    headers=client.headers(),
                ) as response:
                    assert response.status == 409
            finally:
                await client.close()
        finally:
            await harness.close()

    asyncio.run(run())


def test_stalled_canceled_upload_keeps_control_live_and_revokes_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            identity = harness.endpoint.runs.identity
            assert identity is not None
            client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            assert harness.mac.connection.link is not None
            service = harness.endpoint.transfers[identity]
            data = b"x" * 1000000
            manifest = FileManifest(
                FileScope.ticket,
                "ticket",
                "binary.dat",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
            slot = service.create_upload(manifest)
            prefix_sent = asyncio.Event()

            async def body() -> AsyncIterator[bytes]:
                yield data[:100]
                prefix_sent.set()
                await asyncio.Event().wait()

            async def put() -> None:
                async with client.session.put(
                    harness.proxy.origin + "/api/executor/files/" + slot.transfer_id,
                    headers=client.headers(),
                    data=body(),
                ) as response:
                    await response.read()

            task = asyncio.create_task(put())
            try:
                await asyncio.wait_for(prefix_sent.wait(), 2)
                await eventually(lambda: bool(service.active), "upload did not reach native app")
                generation = harness.mac.identity.connection_generation
                # Native inventory request traverses control while file body stalls.
                from planner.executor.protocol import WireMessageKind

                response = await asyncio.wait_for(
                    harness.endpoint.runs.link.request(
                        WireMessageKind.reconnect_ask, {"command_ids": []}
                    ),
                    2,
                )
                assert response.kind is WireMessageKind.reconnect_result
                assert harness.mac.identity.connection_generation == generation
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await eventually(
                    lambda: slot.transfer_id not in service.transfers,
                    "canceled HTTP upload retained its reservation",
                )
                assert not (service.root / slot.transfer_id).exists()
                assert harness.endpoint.runs.ready
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("malformed", ["text", "trailing", "empty", "oversized"])
def test_malformed_control_cannot_fence_current_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed: str
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            identity = harness.endpoint.runs.identity
            assert identity is not None
            async with harness.proxy.session_factory() as session:
                async with session.ws_connect(
                    "wss" + harness.proxy.origin[5:] + "/api/executor/connect",
                    headers={"X-Panels-Executor-ID": "test-executor"},
                    compress=0,
                ) as socket:
                    if malformed == "text":
                        await socket.send_str("hello")
                    elif malformed == "trailing":
                        await socket.send_bytes(b'{"kind":"hello"}extra')
                    elif malformed == "empty":
                        await socket.send_bytes(b"")
                    else:
                        await socket.send_bytes(b"x" * (MAX_ENVELOPE_BYTES + 1))
                    reply = await asyncio.wait_for(socket.receive(), 2)
                    assert reply.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
            assert harness.endpoint.runs.identity == identity and harness.endpoint.runs.ready
        finally:
            await harness.close()

    asyncio.run(run())


def test_duplicate_hello_preserves_active_file_reservations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import TypeAdapter

    from planner.executor.protocol import (
        FileTransfer,
        FileUploadRequest,
        WireMessage,
        WireMessageKind,
        as_object,
        encode_value,
    )

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            identity = harness.endpoint.runs.identity
            assert identity is not None
            writer = harness.endpoint.runs.link
            assert harness.mac.connection.link is not None
            service = harness.endpoint.transfers[identity]
            child = next(iter(harness.mac.runner._children.values()))
            assert child.ticket_id is not None
            manifest = FileManifest(
                FileScope.ticket,
                child.ticket_id,
                "empty",
                hashlib.sha256(b"").hexdigest(),
                0,
            )
            response = await harness.mac.connection.link.request(
                WireMessageKind.put_file,
                as_object(FileUploadRequest(manifest, child.reference)),
                request_id="reservation",
            )
            assert response.kind is WireMessageKind.file_transfer
            slot = TypeAdapter(FileTransfer).validate_python(response.payload)
            payload = encode_value(harness.mac.connection.health())
            assert isinstance(payload, dict)
            async with harness.proxy.session_factory() as session:
                async with session.ws_connect(
                    "wss" + harness.proxy.origin[5:] + "/api/executor/connect",
                    headers={"X-Panels-Executor-ID": "test-executor"},
                ) as socket:
                    await socket.send_bytes(
                        encode_message(
                            WireMessage(
                                WireMessageKind.hello,
                                "duplicate",
                                payload,
                            )
                        )
                    )
                    reply = await asyncio.wait_for(socket.receive(), 2)
                    assert reply.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
            assert harness.endpoint.runs.ready and harness.endpoint.runs.identity == identity
            assert harness.endpoint.runs.link is writer
            assert harness.endpoint.transfers[identity] is service and service.valid()
            assert slot.transfer_id in service.transfers
            native_client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            try:
                source = tmp_path / "empty-source"
                source.write_bytes(b"")
                await put_file(harness.mac.connection.link, slot, source)
                assert slot.transfer_id not in service.transfers
                assert not harness.endpoint.inbox.store.file_target(manifest).exists()
                harness.endpoint.inbox.store.install_files((manifest,))
                assert harness.endpoint.inbox.store.file_target(manifest).read_bytes() == b""
            finally:
                await native_client.close()
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("trigger", ["promote", "drain"])
def test_native_held_unknown_keeps_reservation_and_never_replays_provider_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
) -> None:
    """The same TLS host crosses held caller, native uncertainty, core ownership and replay."""

    from planner.conversation.backends.contracts import PromptWriteFailed, TurnToken
    from planner.conversation.contracts import (
        HeldPromptPromotionMode,
        PromptDeliveryMode,
        PromptDeliveryUncertain,
    )
    from planner.conversation.events import ConversationTurnEnding
    from planner.conversation.message_content import text_message_content
    from planner.core.db import connect

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        assert harness.endpoint.runs.link is not None
        assert harness.mac.connection.link is not None
        try:
            assert harness.native.sink is not None and harness.native.turn is not None
            first_turn = harness.native.turn
            await harness.system.send(
                "c_scripted",
                text_message_content("attempt once"),
                sender_label="Owner",
                mode=PromptDeliveryMode.queue,
                sender_message_id="held-unknown",
            )
            await harness.system.send(
                "c_scripted",
                text_message_content("remain held"),
                sender_label="Owner",
                mode=PromptDeliveryMode.queue,
                sender_message_id="must-not-drain",
                model_change="separate-held-intent",
            )
            held = await harness.system.held_prompts("c_scripted")
            assert len(held) == 2

            async def uncertain_write(turn: TurnToken, *args: object, **kwargs: object) -> None:
                harness.native.prompt_count += 1
                harness.native.turn = turn
                raise PromptWriteFailed("native acknowledgment lost after possible write")

            async def cancel() -> None:
                assert harness.native.sink is not None
                await harness.native.sink.report(
                    first_turn,
                    TurnEnded(
                        ending=ConversationTurnEnding.interrupted,
                        error_summary=None,
                        standard_error_tail=None,
                    ),
                )

            monkeypatch.setattr(harness.native, "write_prompt", uncertain_write)
            monkeypatch.setattr(harness.native, "cancel_running_turn", cancel, raising=False)
            if trigger == "promote":
                result = await asyncio.wait_for(
                    harness.system.promote_held_prompt(
                        "c_scripted",
                        held[0].held_prompt_id,
                        HeldPromptPromotionMode.send_now,
                    ),
                    2,
                )
                assert isinstance(result, PromptDeliveryUncertain)
            else:
                await harness.native.sink.report(
                    first_turn,
                    TurnEnded(
                        ending=ConversationTurnEnding.completed,
                        error_summary=None,
                        standard_error_tail=None,
                    ),
                )
            await eventually(
                lambda: harness.events("prompt_delivery_uncertain") == 1,
                "native held uncertainty did not become durable",
            )
            state = await harness.system._conversation_state("c_scripted")
            assert state is not None
            assert state.child_is_quarantined and state.reserved_turn is not None
            assert harness.native.prompt_count == 2
            connection = connect(harness.endpoint.db_path)
            try:
                assert (
                    connection.execute(
                        "SELECT delivery_uncertain FROM mac_runs WHERE conversation_id=?",
                        ("c_scripted",),
                    ).fetchone()[0]
                    == 1
                )
            finally:
                connection.close()
            uncertain = [
                outcome
                for record in harness.mac.runner._commands.values()
                if (outcome := record.outcome) is not None
                and outcome.disposition.value == "uncertain"
            ]
            assert len(uncertain) == 1
            outcome = uncertain[0]
            assert harness.mac.runner.ask((outcome.command_id,))[0].outcome == outcome
            assert (
                await harness.mac.runner.run(
                    harness.mac.runner._commands[outcome.command_id].command
                )
                == outcome
            )
            assert harness.native.prompt_count == 2
            assert harness.events("prompt_delivery_uncertain") == 1
            remaining = await harness.system.held_prompts("c_scripted")
            assert any(item.sender_message_id == "must-not-drain" for item in remaining)
        finally:
            await harness.close()

    asyncio.run(run())
