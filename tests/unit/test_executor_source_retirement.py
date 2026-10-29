"""Source retirement fences live native state and delayed pre-move assignments."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter
from tests.unit.test_executor_native_runtime import (
    ScriptedChild,
    _fixture_admission,
    canonical_snapshot,
)

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    MoveRetirementDisposition,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION, ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    AskReference,
    BackendEventMethod,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ExecutorIdentity,
    FileSnapshot,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    SourceRetirementRequest,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.runtime import NativeChildRegistry

IDENTITY = ExecutorIdentity(
    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "ca27d941-1e77-4ac9-a102-e0ac14b51626", 1
)


@pytest.fixture(autouse=True)
def native_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))


class Native(ScriptedChild):
    def __init__(self) -> None:
        super().__init__()
        self.stops = 0
        self.stop_fails = False
        self.stop_started = asyncio.Event()
        self.stop_release: asyncio.Event | None = None

    async def stop(self) -> None:
        self.stop_started.set()
        if self.stop_release is not None:
            await self.stop_release.wait()
        if self.stop_fails:
            raise RuntimeError("stop unconfirmed")
        self.stops += 1


def build(tmp_path: Path) -> tuple[NativeChildRegistry, Native, ChildCommand]:
    native = Native()

    def factory(*args: object) -> BackendChildFactory:
        return cast(BackendChildFactory, lambda **kwargs: cast(BackendChild, native))

    registry = NativeChildRegistry(
        incarnation=IDENTITY.executor_incarnation,
        spool=FilesystemSpool(tmp_path / "spool"),
        factory=factory,
        paths=ExecutorPaths(tmp_path / "executor"),
        message_files=ConversationMessageFiles(tmp_path / "unused"),
    )
    child = ChildReference(IDENTITY.executor_incarnation, "child", "c_source")
    resolved = ResolvedConversationStart(
        child.conversation_id,
        ConversationBackendKey.codex,
        "test",
        None,
        ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_source"),)),
        tmp_path,
        ConversationAccess.full,
    )
    command = ChildCommand(
        "start",
        child,
        ChildOperation.start,
        None,
        {
            "resolved_start": encode_value(resolved),
            "canonical_skill_snapshot": encode_value(canonical_snapshot()),
        },
    )
    return registry, native, _admitted_start(registry, command)


def _admitted_start(registry: NativeChildRegistry, command: ChildCommand) -> ChildCommand:
    """Give each new child identity real synthetic evidence for ordinary admission."""
    resolved = TypeAdapter(ResolvedConversationStart).validate_python(
        command.arguments["resolved_start"]
    )
    expectation = _fixture_admission(registry, command.child, resolved)
    return replace(
        command,
        arguments={**command.arguments, "admission_expectation": encode_value(expectation)},
    )


def drain(registry: NativeChildRegistry) -> None:
    for row in registry.spool.pending():
        registry.spool.acknowledge(
            AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.accepted,
                {},
                1,
            )
        )
    for key in tuple(registry.outcomes):
        registry.confirm_command(key)


def request(*refs: ChildReference) -> SourceRetirementRequest:
    return SourceRetirementRequest(IDENTITY, "t_source", "c_source", refs)


def test_retirement_tombstones_absent_delayed_start_and_allows_fresh_child(tmp_path: Path) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        receipt = await registry.retire_source("move", request(start.child), IDENTITY)
        assert receipt.result.disposition is MoveRetirementDisposition.retired
        assert native.starts == native.stops == 0
        assert await registry.retire_source("move", request(start.child), IDENTITY) == receipt
        refused = await registry.execute_command(start)
        assert refused.disposition is CommandDisposition.refused
        assert native.starts == 0
        fresh = _admitted_start(
            registry, replace(start, command_id="fresh", child=replace(start.child, child_id="new"))
        )
        assert (await registry.execute_command(fresh)).disposition is CommandDisposition.succeeded
        assert native.starts == 1
        with pytest.raises(ValueError, match="different content"):
            await registry.retire_source("move", request(), IDENTITY)

    asyncio.run(run())


def test_retirement_stops_once_preserves_first_command_result_and_revokes_ipc(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        outcome = await registry.execute_command(start)
        capability = registry.children[start.child.child_id].capability
        drain(registry)
        receipt = await registry.retire_source("move", request(start.child), IDENTITY)
        assert receipt.result.disposition is MoveRetirementDisposition.retired
        assert native.stops == 1
        assert await registry.retire_source("move", request(start.child), IDENTITY) == receipt
        assert await registry.execute_command(start) == outcome
        with pytest.raises(ValueError, match="different content"):
            await registry.execute_command(replace(start, arguments={}))
        with pytest.raises(PermissionError):
            registry.authenticate(start.child.child_id, capability)
        assert native.stops == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "activity",
    [
        "turn",
        "ask",
        "user_input",
        "native_lock",
        "publication",
        "unsent",
        "uncertain",
        "unassigned",
        "generation",
    ],
)
def test_source_activity_refuses_before_stop(tmp_path: Path, activity: str) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        await registry.execute_command(start)
        drain(registry)
        child = registry.children[start.child.child_id]
        req = request(start.child)
        if activity == "turn":
            child.active_turn = TurnToken("c_source", 1)
        elif activity == "ask":
            child.asks["ask"] = AskReference(start.child, TurnToken("c_source", 1), "ask")
        elif activity == "user_input":
            child.user_inputs["input"] = (
                AskReference(start.child, TurnToken("c_source", 1), "input"),
                BackendUserInputRequest("input", ()),
            )
        elif activity == "native_lock":
            await child.lock.acquire()
        elif activity == "publication":
            registry.begin_source_work(start.child)
        elif activity == "unsent":
            registry.spool.mint_envelope(
                conversation_id="c_source", kind=EnvelopeKind.cli_write, payload={}
            )
        elif activity == "uncertain":
            registry.outcomes["unknown"] = CommandOutcome(
                "unknown", start.child, CommandDisposition.uncertain, {}
            )
        elif activity == "unassigned":
            req = request()
        else:
            req = replace(req, expected_identity=replace(IDENTITY, connection_generation=2))
        receipt = await registry.retire_source("move", req, IDENTITY)
        assert receipt.result.disposition is MoveRetirementDisposition.refused
        assert native.stops == 0
        assert registry.children[start.child.child_id] is child
        assert start.child.child_id not in registry._retired_children

    asyncio.run(run())


def test_source_fence_covers_awaited_stop_and_leaves_other_ticket_usable(tmp_path: Path) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        await registry.execute_command(start)
        drain(registry)
        native.stop_release = asyncio.Event()
        retiring = asyncio.create_task(
            registry.retire_source("move", request(start.child), IDENTITY)
        )
        await native.stop_started.wait()
        child = registry.children[start.child.child_id]
        with pytest.raises(PermissionError):
            registry.authenticate(start.child.child_id, child.capability)
        late = replace(start, command_id="delayed")
        assert (await registry.execute_command(late)).disposition is CommandDisposition.refused
        # An unrelated native assignment remains admitted while the source is fenced.
        resolved = dict(start.arguments)
        other_start = replace(
            start,
            command_id="other",
            child=replace(start.child, child_id="other", conversation_id="c_other"),
        )
        settings = TypeAdapter(ResolvedConversationStart).validate_python(
            resolved["resolved_start"]
        )
        settings = replace(
            settings,
            conversation_id="c_other",
            role_materials=ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", "t_other"),)),
        )
        other_start = _admitted_start(
            registry,
            replace(
                other_start,
                arguments={**start.arguments, "resolved_start": encode_value(settings)},
            ),
        )
        assert (
            await registry.execute_command(other_start)
        ).disposition is CommandDisposition.succeeded
        native.stop_release.set()
        # A refused late command is retained unsent; that is honest unsettled source work.
        result = await retiring
        assert result.result.disposition is MoveRetirementDisposition.uncertain
        assert native.stops == 1 and native.starts == 2

    asyncio.run(run())


def test_stop_failure_retains_unknown_source_and_tombstone(tmp_path: Path) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        await registry.execute_command(start)
        drain(registry)
        native.stop_fails = True
        result = await registry.retire_source("move", request(start.child), IDENTITY)
        assert result.result.disposition is MoveRetirementDisposition.uncertain
        assert start.child.child_id in registry.children
        assert start.child.child_id in registry._retired_children
        assert native.stops == 0
        assert await registry.retire_source("move", request(start.child), IDENTITY) == result

    asyncio.run(run())


def test_event_publication_is_counted_before_await_and_absent_child_spool_is_not_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        await registry.execute_command(start)
        drain(registry)
        entered, release = asyncio.Event(), asyncio.Event()
        original = registry._emit

        async def emit(
            conversation_id: str,
            kind: EnvelopeKind,
            value: object,
            snapshots: tuple[FileSnapshot, ...] = (),
            submission_id: str | None = None,
        ) -> None:
            entered.set()
            await release.wait()
            await original(conversation_id, kind, value, snapshots, submission_id)

        monkeypatch.setattr(registry, "_emit", emit)
        publication = asyncio.create_task(
            registry.report_event(
                start.child,
                BackendEventMethod.turn_ended,
                TurnToken("c_source", 1),
                {
                    "ending": ConversationTurnEnding.completed,
                    "error_summary": None,
                    "standard_error_tail": None,
                },
            )
        )
        await entered.wait()
        result = await registry.retire_source("move", request(start.child), IDENTITY)
        assert result.result.disposition is MoveRetirementDisposition.refused
        assert native.stops == 0
        release.set()
        await publication
        registry.children.clear()
        result = await registry.retire_source("again", request(start.child), IDENTITY)
        assert result.result.disposition is MoveRetirementDisposition.refused

    asyncio.run(run())


def test_no_conversation_stop_uncertainty_blocks_fresh_ticket_start_after_vps_restart(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry, native, start = build(tmp_path)
        await registry.execute_command(start)
        drain(registry)
        native.stop_fails = True
        req = replace(request(start.child), conversation_id=None)
        result = await registry.retire_source("original-vps", req, IDENTITY)
        assert result.result.disposition is MoveRetirementDisposition.uncertain
        # A new VPS process can allocate a UUID with no restored conversation/core-turn row.
        # The resident registry outlives it and must fence that second native start.
        fresh = _admitted_start(
            registry,
            replace(start, command_id="new-vps-start", child=replace(start.child, child_id="new")),
        )
        outcome = await registry.execute_command(fresh)
        assert outcome.disposition is CommandDisposition.refused
        assert native.starts == 1
        drain(registry)
        native.stop_fails = False
        assert (
            await registry.retire_source("fresh-proof", req, IDENTITY)
        ).result.disposition is MoveRetirementDisposition.retired
        fresh = _admitted_start(
            registry,
            replace(
                fresh, command_id="after-retirement", child=replace(start.child, child_id="after")
            ),
        )
        assert (await registry.execute_command(fresh)).disposition is CommandDisposition.succeeded
        assert native.starts == 2 and native.stops == 1

    asyncio.run(run())


def test_ipc_journal_work_is_counted_before_thread_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        from threading import Event

        from planner.executor.configuration import ExecutorPaths
        from planner.executor.contracts import IpcOperation, IpcRequest
        from planner.executor.mac import MacExecutor

        registry, native, start = build(tmp_path)
        await registry.execute_command(start)
        drain(registry)
        mac = MacExecutor(
            paths=ExecutorPaths(tmp_path / "resident"),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
            registry=registry,
        )
        mac.spool = cast(FilesystemSpool, registry.spool)
        entered, release = Event(), Event()
        original = mac.spool.mint_envelope

        def blocked_mint(
            *,
            conversation_id: str,
            kind: EnvelopeKind,
            payload: JsonObject,
            snapshots: tuple[FileSnapshot, ...] = (),
            submission_id: str | None = None,
        ) -> QueuedEnvelope:
            entered.set()
            assert release.wait(5)
            return original(
                conversation_id=conversation_id,
                kind=kind,
                payload=payload,
                snapshots=snapshots,
                submission_id=submission_id,
            )

        monkeypatch.setattr(mac.spool, "mint_envelope", blocked_mint)
        child = registry.children[start.child.child_id]
        ipc = asyncio.create_task(
            mac.handle_ipc(
                IpcRequest(
                    "write",
                    IpcOperation.write,
                    start.child.child_id,
                    child.capability,
                    {"operation": "ticket.propose", "arguments": {}},
                )
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            result = await registry.retire_source("move", request(start.child), IDENTITY)
            assert result.result.disposition is MoveRetirementDisposition.refused
            assert native.stops == 0
        finally:
            release.set()
            # Cancel only the wait for a receipt, after immutable journal creation is complete.
            while registry._source_work:
                await asyncio.sleep(0)
            ipc.cancel()
            with pytest.raises(asyncio.CancelledError):
                await ipc
        assert registry.spool.pending()

    asyncio.run(run())


def test_mac_source_wire_request_returns_typed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        from pydantic import TypeAdapter

        from planner.executor.configuration import ExecutorPaths
        from planner.executor.contracts import (
            ConnectionState,
            SourceRetirementReceipt,
            WireMessage,
            WireMessageKind,
        )
        from planner.executor.mac import MacExecutor, as_object

        registry, native, delayed = build(tmp_path)
        mac = MacExecutor(
            paths=ExecutorPaths(tmp_path / "resident"),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
            registry=registry,
        )
        mac.identity = IDENTITY
        mac.state = ConnectionState.ready
        frames: list[WireMessage] = []

        async def send(frame: WireMessage) -> None:
            frames.append(frame)

        monkeypatch.setattr(mac, "_send", send)
        await mac._retire_source(
            WireMessage(
                WireMessageKind.source_retire, "wire-request", as_object(request(delayed.child))
            )
        )
        (frame,) = frames
        assert frame.kind is WireMessageKind.source_retired and frame.request_id == "wire-request"
        receipt = TypeAdapter(SourceRetirementReceipt).validate_python(frame.payload)
        assert receipt.result.disposition is MoveRetirementDisposition.retired
        assert receipt.identity == IDENTITY and receipt.assigned_children == (delayed.child,)
        assert native.starts == native.stops == 0

    asyncio.run(run())
