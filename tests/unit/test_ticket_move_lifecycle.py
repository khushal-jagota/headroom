"""Moves retire the actual source before changing locale and preserve conversation state."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast

import pytest
from tests.support.executor_catalog import ScriptedCatalogObserver
from tests.unit.test_conversation_system import _FakeBackend, _FakeBackendChild, _Harness
from tests.unit.test_current_model_resolution import Catalogue
from tests.unit.test_executor_source_retirement import IDENTITY, build

from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    MoveRetirementDisposition,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.model_resolution.contracts import (
    CatalogObservation,
    CatalogRequest,
    CatalogWorkspaceBinding,
    LaunchContext,
    SelectionKind,
)
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.service import ModelResolutionService
from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.executor.contracts import (
    ChildCommand,
    CommandOutcome,
    EnvelopeKind,
    ExecutorIdentity,
    SourceRetirementRequest,
    WireMessage,
    WireMessageKind,
)
from planner.executor.journal import FilesystemSpool
from planner.executor.mac import as_object
from planner.executor.routing import LocaleBackendChildFactory
from planner.executor.runtime import NativeChildRegistry
from planner.executor.vps import ExecutorEndpoint
from planner.tickets import data
from planner.tickets.contracts import Ticket, TicketLocale
from planner.tickets.locale_move import move_ticket_locale


class Writer:
    closing = False

    def is_closing(self) -> bool:
        return self.closing


def ticket(conn: Connection) -> Ticket:
    return data.create_ticket(
        conn,
        title="Move lifecycle",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="test",
        employee_launch_reasoning_effort="high",
        selection_kind=SelectionKind.concrete,
    )


class _MoveCatalogObserver(ScriptedCatalogObserver):
    """Scoped metadata validation around the existing fake child transports."""

    def __init__(
        self,
        destination: TicketLocale,
        native: _MoveCatalogObserver | None = None,
        *,
        identity: ExecutorIdentity = IDENTITY,
    ) -> None:
        super().__init__(lambda: identity)
        self.executor_identity = identity
        self.destination = destination
        self.native = native
        self.model = "test"
        self.efforts = ("high",)
        self.contexts: list[LaunchContext] = []

    async def capture_context(
        self,
        backend_key: ConversationBackendKey,
        *,
        workspace: CatalogWorkspaceBinding,
    ) -> LaunchContext:
        result = replace(
            await super().capture_context(backend_key, workspace=workspace),
            context_ref=f"move-fixture-{self.destination.value}",
            destination=self.destination,
            executor_identity=self.executor_identity
            if self.destination is TicketLocale.macos
            else None,
        )
        self.contexts.append(result)
        return result

    def verify_context(self, expected: LaunchContext) -> bool:
        return expected in self.contexts or (
            self.native is not None and self.native.verify_context(expected)
        )


def _move_harness(
    path: Path,
    *,
    remote: ExecutorEndpoint | None = None,
    native: _MoveCatalogObserver | None = None,
) -> _Harness:
    harness = _Harness(path)
    harness.clock.now = time.time()
    observer = _MoveCatalogObserver(TicketLocale.vps, native)
    harness.system._model_resolver = ModelResolutionService(
        snapshots=Catalogue(()),
        store=ModelResolutionStore(str(path)),
        backend_state=BackendStateStore(str(path)),
        observer=observer,
        now=harness.clock,
    )
    harness.system.configure_destination_validation(observer, endpoint=remote)
    return harness


async def _wait_for_actual_stop(moving: asyncio.Task[Ticket], began: asyncio.Event) -> None:
    """Expose a failed move instead of waiting forever for an effect it never began."""
    waiting = asyncio.create_task(began.wait())
    try:
        done, _ = await asyncio.wait(
            (moving, waiting), timeout=5, return_when=asyncio.FIRST_COMPLETED
        )
        if moving in done:
            await moving
            pytest.fail("move completed without entering the expected source stop")
        assert waiting in done, "source stop did not begin within the fixture bound"
        await waiting
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


def endpoint(conn: Connection, *, identity: ExecutorIdentity = IDENTITY) -> ExecutorEndpoint:
    path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    result = ExecutorEndpoint(path, cast(Clock, SimpleNamespace()))
    result.identity = identity
    result.writer = cast(asyncio.StreamWriter, Writer())
    result.ready = True
    result.store.begin_connection(identity, 1)
    result.store.mark_ready(identity, 2)
    return result


def test_empty_remote_source_still_requires_fresh_current_proof_and_tombstones_assignments(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        t = ticket(tmp_db)
        tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (t.id,))
        tmp_db.commit()
        remote = endpoint(tmp_db)
        registry, native, delayed = build(tmp_path)
        remote.store.register_child(delayed.child, "ticket", t.id, 3)
        calls: list[SourceRetirementRequest] = []

        async def request(kind: WireMessageKind, payload: object) -> WireMessage:
            from pydantic import TypeAdapter

            assert not tmp_db.in_transaction
            assert kind is WireMessageKind.source_retire
            req = TypeAdapter(SourceRetirementRequest).validate_python(payload)
            calls.append(req)
            # No native child or reconciliation inventory exists. Assigned ID must still be fenced.
            result = await registry.retire_source("move", req, IDENTITY)
            return WireMessage(WireMessageKind.source_retired, "move", as_object(result))

        monkeypatch.setattr(remote, "_request", request)
        moved = await move_ticket_locale(
            InMemoryConversationSystem(),
            tmp_db,
            t.id,
            locale=TicketLocale.vps,
            principal=OWNER_PRINCIPAL,
            now=4,
            source=remote.source_for_move(t.id, None),
        )
        assert moved.locale is TicketLocale.vps
        assert len(calls) == 1 and calls[0].assigned_children == (delayed.child,)
        assert delayed.child.child_id in registry._retired_children
        assert native.starts == native.stops == 0
        assert remote.store.child_assignment(delayed.child) is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["durable_generation", "writer_closing", "lost", "claim_conflict"]
)
def test_commit_failure_after_confirmed_remote_stop_reports_retired_source(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def run() -> None:
        t = ticket(tmp_db)
        tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (t.id,))
        tmp_db.commit()
        remote = endpoint(tmp_db)
        registry, native, delayed = build(tmp_path)
        remote.store.register_child(delayed.child, "ticket", t.id, 3)

        async def request(kind: WireMessageKind, payload: object) -> WireMessage:
            from pydantic import TypeAdapter

            req = TypeAdapter(SourceRetirementRequest).validate_python(payload)
            result = await registry.retire_source("move", req, IDENTITY)
            # Mutate durable authority through an independent SQLite connection.
            if failure == "durable_generation":
                remote.store.begin_connection(replace(IDENTITY, connection_generation=2), 4)
                remote.store.mark_ready(replace(IDENTITY, connection_generation=2), 5)
            elif failure == "writer_closing":
                cast(Writer, remote.writer).closing = True
            elif failure == "lost":
                remote.store.mark_lost(IDENTITY, 5)
            else:
                other = connect(remote.db_path)
                try:
                    other.execute("UPDATE tickets SET worker_step_claim='out' WHERE id=?", (t.id,))
                    other.commit()
                finally:
                    other.close()
            return WireMessage(WireMessageKind.source_retired, "move", as_object(result))

        monkeypatch.setattr(remote, "_request", request)
        from planner.tickets.locale_move import move_ticket_locale

        with pytest.raises(PlannerError, match="source retired") as caught:
            await move_ticket_locale(
                InMemoryConversationSystem(),
                tmp_db,
                t.id,
                locale=TicketLocale.vps,
                principal=OWNER_PRINCIPAL,
                now=4,
                source=remote.source_for_move(t.id, None),
            )
        assert caught.value.detail["source_retired"] is True
        assert data.read_ticket(tmp_db, t.id).locale is TicketLocale.macos
        assert native.stops == native.starts == 0

    asyncio.run(run())


@pytest.mark.parametrize("state", ["absent", "lost", "reconciling", "closing"])
def test_unknown_remote_source_refuses_before_request(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    async def run() -> None:
        t = ticket(tmp_db)
        tmp_db.execute("UPDATE tickets SET locale='macos' WHERE id=?", (t.id,))
        tmp_db.commit()
        remote = endpoint(tmp_db)
        if state == "absent":
            remote.writer = None
        elif state == "lost":
            remote.store.mark_lost(IDENTITY, 5)
        elif state == "reconciling":
            remote.ready = False
        else:
            cast(Writer, remote.writer).closing = True

        async def request(*args: object) -> WireMessage:
            pytest.fail("unknown source must not dispatch retirement")

        monkeypatch.setattr(remote, "_request", request)
        result = await remote.source_for_move(t.id, None).retire()
        assert result.disposition is MoveRetirementDisposition.refused

    asyncio.run(run())


@pytest.mark.parametrize("uncertain_return", [False, True])
def test_both_directions_and_return_to_mac_use_fresh_children_preserve_posture_and_restart(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain_return: bool
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    async def run() -> None:
        from pydantic import TypeAdapter

        from planner.tickets.locale_move import move_ticket_locale

        t = ticket(tmp_db)
        from planner.runtime import conversation_start

        monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
        # Admission uses the strict wire identity parser, unlike retirement-only fixtures.
        identity = replace(IDENTITY, executor_incarnation="11111111-1111-4111-8111-111111111111")
        remote = endpoint(tmp_db, identity=identity)
        native_observer = _MoveCatalogObserver(TicketLocale.macos, identity=identity)
        h = _move_harness(Path(remote.db_path), remote=remote, native=native_observer)

        async def observe_catalog(
            request: CatalogRequest,
            *,
            attempt_id: str,
            deadline: float | None = None,
        ) -> CatalogObservation:
            assert request.expected_context.executor_identity == identity
            return await native_observer.observe(
                request.backend_key,
                workspace=request.expected_context.workspace,
                attempt_id=attempt_id,
                deadline=deadline or time.monotonic() + 5,
            )

        monkeypatch.setattr(remote, "observe_catalog", observe_catalog)
        remote.runtime = SimpleNamespace(system=h.system, message_files=h.message_files)
        natives: list[_FakeBackend] = []

        def native_factory(
            resolved: ResolvedConversationStart, capability: str, ref: object
        ) -> BackendChildFactory:
            backend = _FakeBackend(resolved.conversation_id)
            natives.append(backend)

            def create(**kwargs: object) -> BackendChild:
                return cast(
                    BackendChild,
                    _FakeBackendChild(backend, cast(BackendEventSink, kwargs["event_sink"])),
                )

            return cast(BackendChildFactory, create)

        from planner.executor.configuration import ExecutorPaths

        registry = NativeChildRegistry(
            paths=ExecutorPaths(tmp_path / "executor"),
            incarnation=identity.executor_incarnation,
            spool=FilesystemSpool(tmp_path / "spool"),
            factory=native_factory,
            admission_observer=native_observer,
            message_files=ConversationMessageFiles(remote.db_path),
        )

        async def command(cmd: ChildCommand) -> CommandOutcome:
            assert not tmp_db.in_transaction
            outcome = await registry.execute_command(cmd)
            pending = registry.spool.pending()
            assert pending, "native command must publish its actual durable outcome"
            envelope = pending[0]
            assert (
                envelope.kind is EnvelopeKind.command_outcome
                and envelope.payload.get("command_id") == cmd.command_id
            ), "fixture must accept earlier callbacks before a command outcome; never skip FIFO"
            assert TypeAdapter(CommandOutcome).validate_python(envelope.payload) == outcome
            # Production resolves command futures only after canonical acceptance.
            # Recording the real outcome settles the exact start lease before the
            # next prompt command binds it. Generic events keep the drain below.
            receipt = await remote.accept_envelope(envelope, identity)
            registry.spool.acknowledge(receipt)
            registry.confirm_command(cmd.command_id)
            return outcome

        remote.factory.send_command = command

        async def request(kind: WireMessageKind, payload: object) -> WireMessage:
            assert not tmp_db.in_transaction
            assert kind is WireMessageKind.source_retire
            req = TypeAdapter(SourceRetirementRequest).validate_python(payload)
            result = await registry.retire_source(str(len(registry._retirements)), req, identity)
            return WireMessage(WireMessageKind.source_retired, "move", as_object(result))

        monkeypatch.setattr(remote, "_request", request)
        router = LocaleBackendChildFactory(remote.db_path, h._make_child, remote.factory)
        h.system._backend_child_factories = dict.fromkeys(ConversationBackendKey, router)

        async def drain_events() -> None:
            for envelope in registry.spool.pending():
                receipt = await remote.accept_envelope(envelope, identity)
                registry.spool.acknowledge(receipt)
                if envelope.kind is EnvelopeKind.command_outcome:
                    registry.confirm_command(str(envelope.payload["command_id"]))

        await h.system.start_conversation(
            ConversationStartRequest(
                conversation_id="c_move",
                backend_key=ConversationBackendKey.codex,
                model="test",
                reasoning_effort="high",
                workspace_folder=tmp_path,
                role_materials=ConversationRoleMaterials(
                    "worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", t.id))
                ),
            )
        )
        tmp_db.execute("UPDATE tickets SET conversation_id='c_move' WHERE id=?", (t.id,))
        tmp_db.commit()
        assert isinstance(
            await h.system.send(
                "c_move", text_message_content("initial local work"), sender_label="Owner"
            ),
            PromptDeliveryStarted,
        )
        await h.complete_turn("c_move")
        before = await h.store.read_conversation("c_move")
        await move_ticket_locale(
            h.system, tmp_db, t.id, locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=4
        )
        assert h.backend("c_move").stops == 1
        assert await h.store.read_conversation("c_move") == before
        assert isinstance(
            await h.system.send("c_move", text_message_content("Mac work"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        await drain_events()
        native = natives[-1]
        assert native.sink is not None and native.live_turn_token is not None
        await native.sink.turn_ended(
            native.live_turn_token,
            ending=ConversationTurnEnding.completed,
            error_summary=None,
            standard_error_tail=None,
        )
        await drain_events()
        await h.settle()
        old_ref = h.system._conversations["c_move"].executor_child_reference
        before = await h.store.read_conversation("c_move")
        if uncertain_return:
            native.stop_raises_something_unnamed = True
            with pytest.raises(PlannerError) as caught:
                await move_ticket_locale(
                    h.system,
                    tmp_db,
                    t.id,
                    locale=TicketLocale.vps,
                    principal=OWNER_PRINCIPAL,
                    now=5,
                    source=remote.source_for_move(t.id, "c_move"),
                )
            assert caught.value.detail["source_retired"] is None
            assert data.read_ticket(tmp_db, t.id).locale is TicketLocale.macos
            assert native.stops == 0
            state = h.system._conversations["c_move"]
            assert state.child_is_quarantined and state.executor_child_reference == old_ref
            await h.system.shutdown()
            restarted = _move_harness(Path(remote.db_path), remote=remote, native=native_observer)
            restored = await restarted.system._conversation_state("c_move")
            assert restored is not None and restored.child_is_quarantined
            assert restored.executor_child_reference == old_ref
            await restarted.system.shutdown()
            # An explicit fresh source proof can confirm stop; clean the fake native child.
            native.stop_raises_something_unnamed = False
            req = SourceRetirementRequest(
                identity,
                t.id,
                "c_move",
                remote.store.assigned_ticket_children(t.id, identity.executor_incarnation),
            )
            assert (
                await registry.retire_source("cleanup-proof", req, identity)
            ).result.disposition is MoveRetirementDisposition.retired
            state.child = None
            await h.system.shutdown()
            return
        await move_ticket_locale(
            h.system,
            tmp_db,
            t.id,
            locale=TicketLocale.vps,
            principal=OWNER_PRINCIPAL,
            now=5,
            source=remote.source_for_move(t.id, "c_move"),
        )
        assert native.stops == 1
        assert await h.store.read_conversation("c_move") == before
        # A fresh VPS core must retain ended-turn posture but discard retired remote authority.
        await h.system.shutdown()
        # Retain fake provider observations across a real replacement of the runtime owner.
        observed_backends = h.backends
        restarted = _move_harness(Path(remote.db_path), remote=remote, native=native_observer)
        restarted.backends = observed_backends
        restored = await restarted.system._conversation_state("c_move")
        assert restored is not None and restored.executor_child_reference is None
        assert restored.last_ended_turn_token is not None
        h = restarted
        remote.runtime = SimpleNamespace(system=h.system, message_files=h.message_files)
        router = LocaleBackendChildFactory(remote.db_path, h._make_child, remote.factory)
        h.system._backend_child_factories = dict.fromkeys(ConversationBackendKey, router)
        assert isinstance(
            await h.system.send(
                "c_move", text_message_content("local again"), sender_label="Owner"
            ),
            PromptDeliveryStarted,
        )
        await h.complete_turn("c_move")
        assert h.backend("c_move").session_starts == 2
        assert (
            h.backend("c_move").model == "test" and h.backend("c_move").reasoning_effort == "high"
        )
        await move_ticket_locale(
            h.system, tmp_db, t.id, locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=6
        )
        assert isinstance(
            await h.system.send("c_move", text_message_content("Mac again"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        await drain_events()
        new_ref = h.system._conversations["c_move"].executor_child_reference
        assert new_ref != old_ref and new_ref.executor_incarnation == old_ref.executor_incarnation
        assert len(natives) == 2 and natives[1].session_starts == 1
        assert native.stops == 1
        assert h.backend("c_move").stops == 2
        await h.system.shutdown()

    # This fixture bound does not settle an uncertain command or release its authority.
    asyncio.run(asyncio.wait_for(run(), 20))


@pytest.mark.parametrize("failure", ["hash", "authority", "compatibility", "claim", "same_locale"])
def test_preflight_has_no_native_effects(
    tmp_db: Connection, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        from tests.support.principals import ticket_principal

        from planner.tickets.locale_move import move_ticket_locale

        t = ticket(tmp_db)
        h = _move_harness(Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2])))
        await h.system.start_conversation(
            ConversationStartRequest(conversation_id="c_preflight", model="test")
        )
        tmp_db.execute("UPDATE tickets SET conversation_id='c_preflight' WHERE id=?", (t.id,))
        tmp_db.commit()
        assert isinstance(
            await h.system.send("c_preflight", text_message_content("work"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        await h.complete_turn("c_preflight")
        backend = h.backend("c_preflight")
        before = await h.store.read_conversation("c_preflight")
        if failure == "compatibility":

            def refuse_compatibility(locale: TicketLocale, backend: str) -> None:
                raise PlannerError(
                    ErrorCode.validation,
                    "injected compatibility refusal",
                    {"locale": locale.value, "employee_backend": backend},
                )

            monkeypatch.setattr(data, "require_locale_backend_compatibility", refuse_compatibility)
        elif failure == "claim":
            tmp_db.execute("UPDATE tickets SET worker_step_claim='out' WHERE id=?", (t.id,))
            tmp_db.commit()
        ticket_before = data.read_ticket(tmp_db, t.id)
        locale = TicketLocale.vps if failure == "same_locale" else TicketLocale.macos
        principal = ticket_principal(t.id) if failure == "authority" else OWNER_PRINCIPAL
        expected_hash = "stale" if failure == "hash" else None
        if failure == "same_locale":
            await move_ticket_locale(
                h.system,
                tmp_db,
                t.id,
                locale=locale,
                principal=principal,
                now=4,
                expected_record_sha256=expected_hash,
            )
        else:
            expected_reason = (
                "injected compatibility refusal" if failure == "compatibility" else None
            )
            with pytest.raises(PlannerError, match=expected_reason):
                await move_ticket_locale(
                    h.system,
                    tmp_db,
                    t.id,
                    locale=locale,
                    principal=principal,
                    now=4,
                    expected_record_sha256=expected_hash,
                )
        assert backend.stops == 0 and backend.live_children == 1
        assert data.read_ticket(tmp_db, t.id) == ticket_before
        assert ticket_before.locale is TicketLocale.vps
        assert await h.store.read_conversation("c_preflight") == before
        await h.system.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize(
    "activity", ["running", "held", "permission", "user_input", "quarantine", "event"]
)
def test_local_activity_refuses_before_stop(tmp_db: Connection, activity: str) -> None:
    async def run() -> None:
        from planner.tickets.locale_move import move_ticket_locale

        t = ticket(tmp_db)
        h = _move_harness(Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2])))
        await h.system.start_conversation(
            ConversationStartRequest(conversation_id="c_busy", model="test")
        )
        tmp_db.execute("UPDATE tickets SET conversation_id='c_busy' WHERE id=?", (t.id,))
        tmp_db.commit()
        assert isinstance(
            await h.system.send("c_busy", text_message_content("work"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        if activity == "permission":
            await h.raise_permission_ask("c_busy")
        elif activity == "user_input":
            await h.request_user_input("c_busy")
        elif activity == "held":
            assert isinstance(
                await h.system.send("c_busy", text_message_content("queued"), sender_label="Owner"),
                PromptDeliveryQueued,
            )
        elif activity in ("quarantine", "event"):
            await h.complete_turn("c_busy")
            if activity == "quarantine":
                h.system._conversations["c_busy"].child_is_quarantined = True
            else:
                # A queue handler can be active after queue.get removed its entry.
                h.system._conversations["c_busy"].backend_event_in_progress = True
        with pytest.raises(PlannerError) as caught:
            await move_ticket_locale(
                h.system, tmp_db, t.id, locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=4
            )
        assert caught.value.detail["retirement_started"] is False
        assert h.backend("c_busy").stops == 0
        assert data.read_ticket(tmp_db, t.id).locale is TicketLocale.vps
        await h.system.shutdown()

    asyncio.run(run())


def test_local_stop_failure_quarantines_retained_source(
    tmp_db: Connection,
) -> None:
    async def run() -> None:
        from planner.tickets.locale_move import move_ticket_locale

        t = ticket(tmp_db)
        h = _move_harness(Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2])))
        await h.system.start_conversation(
            ConversationStartRequest(conversation_id="c_failure", model="test")
        )
        tmp_db.execute("UPDATE tickets SET conversation_id='c_failure' WHERE id=?", (t.id,))
        tmp_db.commit()
        assert isinstance(
            await h.system.send("c_failure", text_message_content("work"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        await h.complete_turn("c_failure")
        backend = h.backend("c_failure")
        backend.stop_raises_something_unnamed = True
        with pytest.raises(PlannerError) as caught:
            await move_ticket_locale(
                h.system, tmp_db, t.id, locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=4
            )
        assert caught.value.detail["retirement_started"] is True
        assert caught.value.detail["source_retired"] is None
        state = h.system._conversations["c_failure"]
        assert state.child_is_quarantined and state.child is not None
        assert data.read_ticket(tmp_db, t.id).locale is TicketLocale.vps
        backend.stop_raises_something_unnamed = False
        await h.system.shutdown()

    asyncio.run(run())


def test_confirmed_local_stop_then_hash_conflict_keeps_locale_and_can_restart_source(
    tmp_db: Connection,
) -> None:
    async def run() -> None:
        from planner.tickets.locale_move import move_ticket_locale

        t = ticket(tmp_db)
        h = _move_harness(Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2])))
        await h.system.start_conversation(
            ConversationStartRequest(conversation_id="c_conflict", model="test")
        )
        tmp_db.execute("UPDATE tickets SET conversation_id='c_conflict' WHERE id=?", (t.id,))
        tmp_db.commit()
        assert isinstance(
            await h.system.send("c_conflict", text_message_content("work"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        await h.complete_turn("c_conflict")
        expected = data.ticket_record_sha256(tmp_db, t.id)
        backend = h.backend("c_conflict")
        backend.stop_has_begun, backend.stops_wait_for_release = asyncio.Event(), asyncio.Event()
        moving = asyncio.create_task(
            move_ticket_locale(
                h.system,
                tmp_db,
                t.id,
                locale=TicketLocale.macos,
                principal=OWNER_PRINCIPAL,
                now=4,
                expected_record_sha256=expected,
            )
        )
        await _wait_for_actual_stop(moving, backend.stop_has_begun)
        assert not tmp_db.in_transaction
        other = connect(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
        try:
            other.execute("UPDATE tickets SET title='Concurrent edit' WHERE id=?", (t.id,))
            other.commit()
        finally:
            other.close()
        backend.stops_wait_for_release.set()
        with pytest.raises(PlannerError, match="source retired") as caught:
            await moving
        assert caught.value.detail["source_retired"] is True
        assert backend.stops == 1 and backend.live_children == 0
        assert data.read_ticket(tmp_db, t.id).locale is TicketLocale.vps
        await h.system.send(
            "c_conflict", text_message_content("retry at canonical source"), sender_label="Owner"
        )
        assert backend.session_starts == 2 and backend.live_children == 1
        await h.system.shutdown()

    asyncio.run(run())


def test_move_link_fence_serializes_send_worker_and_reset(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        from planner.runtime.conversation_start import (
            conversation_link_lock,
            reset_ticket_conversation,
        )
        from planner.runtime.worker_step_readiness_loop import start_ready_worker_step
        from planner.tickets.locale_move import move_ticket_locale
        from planner.worker_types.configuration import configured_worker_type_registry

        t = ticket(tmp_db)
        h = _move_harness(Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2])))
        await h.system.start_conversation(
            ConversationStartRequest(conversation_id="c_lock", model="test")
        )
        tmp_db.execute("UPDATE tickets SET conversation_id='c_lock' WHERE id=?", (t.id,))
        tmp_db.commit()
        assert isinstance(
            await h.system.send("c_lock", text_message_content("work"), sender_label="Owner"),
            PromptDeliveryStarted,
        )
        await h.complete_turn("c_lock")
        backend = h.backend("c_lock")
        backend.stop_has_begun, backend.stops_wait_for_release = asyncio.Event(), asyncio.Event()
        moving = asyncio.create_task(
            move_ticket_locale(
                h.system, tmp_db, t.id, locale=TicketLocale.macos, principal=OWNER_PRINCIPAL, now=4
            )
        )
        await _wait_for_actual_stop(moving, backend.stop_has_begun)
        assert conversation_link_lock(f"ticket:{t.id}").locked()
        entered: list[str] = []

        async def send() -> None:
            # The canonical Ticket send route takes this same lock before resolution/allocation.
            async with conversation_link_lock(f"ticket:{t.id}"):
                entered.append("send")

        worker_connections: list[Connection] = []
        claims: list[str] = []
        original_read = data.read_ticket
        original_claim = data.claim_ticket_for_worker_step

        def worker_db() -> Connection:
            connection = connect(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
            worker_connections.append(connection)
            return connection

        def observe_read(connection: Connection, ticket_id: str) -> Ticket:
            if any(connection is owned for owned in worker_connections) and "worker" not in entered:
                entered.append("worker")
            return original_read(connection, ticket_id)

        def observe_claim(
            connection: Connection,
            ticket_id: str,
            *,
            readiness_check: data._WorkerStepReadinessCheck,
            now: int,
        ) -> Ticket | None:
            claims.append(ticket_id)
            return original_claim(connection, ticket_id, readiness_check=readiness_check, now=now)

        monkeypatch.setattr(data, "read_ticket", observe_read)
        monkeypatch.setattr(data, "claim_ticket_for_worker_step", observe_claim)

        sending = asyncio.create_task(send())
        worker = asyncio.create_task(
            start_ready_worker_step(
                t.id,
                connect_database=worker_db,
                conversation_system=h.system,
                worker_type_registry=configured_worker_type_registry(),
                now=lambda: 5,
            )
        )
        resetting = asyncio.create_task(reset_ticket_conversation(h.system, tmp_db, t.id, now=6))
        # Yield via an Event callback, giving each contender one chance to acquire admission.
        scheduling = asyncio.Event()
        asyncio.get_running_loop().call_soon(scheduling.set)
        await scheduling.wait()
        # Opening a connection precedes the link lock; canonical reads and claims do not.
        assert len(worker_connections) == 1
        assert entered == [] and not sending.done() and not worker.done() and not resetting.done()
        assert claims == []
        assert data.read_ticket(tmp_db, t.id).worker_step_claim is t.worker_step_claim
        assert backend.session_starts == 1 and backend.stops == 0
        backend.stops_wait_for_release.set()
        await moving
        await asyncio.gather(sending, worker, resetting)
        assert entered == ["send", "worker"]
        assert backend.session_starts == 1 and backend.stops == 1
        assert data.read_ticket(tmp_db, t.id).locale is TicketLocale.macos
        await h.system.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("bad_case", range(4))
def test_retired_delayed_start_refusal_journal_is_accepted_with_no_live_child_authority(
    tmp_db: Connection, tmp_path: Path, bad_case: int
) -> None:
    async def run() -> None:
        from dataclasses import replace

        from planner.conversation.backends.contracts import TurnToken
        from planner.executor.contracts import (
            BackendEvent,
            BackendEventMethod,
            CliWrite,
            CommandDisposition,
            SourceRetirementRequest,
        )

        t = ticket(tmp_db)
        remote = endpoint(tmp_db)
        registry, native, start = build(tmp_path)
        remote.store.register_child(start.child, "ticket", t.id, 3)
        result = await registry.retire_source(
            "move", SourceRetirementRequest(IDENTITY, t.id, None, (start.child,)), IDENTITY
        )
        assert result.result.disposition is MoveRetirementDisposition.retired
        remote.store.close_child(start.child, 4)
        outcome = await registry.execute_command(start)
        assert outcome.disposition is CommandDisposition.refused
        assert native.starts == native.stops == 0
        (envelope,) = registry.spool.pending()
        receipt = await remote.accept_envelope(envelope, IDENTITY)
        registry.spool.acknowledge(receipt)
        assert registry.spool.pending() == ()
        reconciliation = remote.store.reconcile(registry.inventory(IDENTITY), 5)
        assert reconciliation.acknowledged_command_ids == (start.command_id,)
        for command_id in reconciliation.acknowledged_command_ids:
            registry.confirm_command(command_id)
        assert registry.inventory(IDENTITY).retained_command_outcomes == ()
        # A closed identity only settles a conclusive no-effect refusal. It grants
        # neither a native event, CLI write, uncertain outcome nor applied answer.
        bad_payloads = [
            (
                EnvelopeKind.command_outcome,
                replace(outcome, command_id="unknown", disposition=CommandDisposition.uncertain),
            ),
            (
                EnvelopeKind.command_outcome,
                replace(outcome, command_id="answer", result={"answer_application": {}}),
            ),
            (EnvelopeKind.cli_write, CliWrite("ticket.propose", {}, start.child, None)),
            (
                EnvelopeKind.backend_event,
                BackendEvent(
                    start.child,
                    BackendEventMethod.turn_ended,
                    TurnToken(start.child.conversation_id, 1),
                    {},
                ),
            ),
        ]
        kind, payload = bad_payloads[bad_case]
        row = registry.spool.mint_envelope(
            conversation_id=start.child.conversation_id, kind=kind, payload=as_object(payload)
        )
        with pytest.raises(PlannerError, match="no VPS assignment"):
            await remote.accept_envelope(row, IDENTITY)

    asyncio.run(run())
