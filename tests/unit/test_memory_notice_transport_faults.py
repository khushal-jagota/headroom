"""Scripted subprocess proofs. Wire attempts do not establish provider admission.

A consumed caller authorization never supplies a delivered prompt or an answered boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_codex_scripted_app_server import scripted_app_server_launch

from planner.conversation.backends.codex_app_server import login
from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChild,
    CodexChildLaunch,
)
from planner.conversation.backends.codex_app_server.login import RememberedCodexLoginRejection
from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryUncertain,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ContextCompactedEventPayload,
    PromptDeliveryUncertainEventPayload,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore, StoredConversationEvent
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.runtime import worker_memory
from planner.runtime.worker_step_readiness_loop import tell_a_worker_it_lost_its_memory
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture(autouse=True)
def _synthetic_process_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The launch guard compares child and parent homes. Point both at this empty
    # synthetic home so even that guard cannot inspect the real account's login.
    codex_home = tmp_path / "synthetic-codex-home"
    codex_home.mkdir()
    safe_environment_names = {
        "PATH", "LANG", "LC_ALL", "TZ", "TMPDIR", "VIRTUAL_ENV",
        "PLAN_DB_PATH", "PLAN_LOGS_DIR", "PLAN_DISPATCHER_LOCK_PATH",
        "PLAN_SERVER_CONTROL_SOCKET", "PLAN_HERMES_HOME",
    }
    for name in tuple(os.environ):
        if name not in safe_environment_names:
            monkeypatch.delenv(name)
    synthetic_home = tmp_path / "synthetic-home"
    synthetic_home.mkdir()
    monkeypatch.setenv("HOME", str(synthetic_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def refuse_any_credential_read(home: Path) -> tuple[str, ...] | None:
        raise AssertionError(f"This evidence test must not inspect credential files: {home}")

    monkeypatch.setattr(login, "_credentials", refuse_any_credential_read)


class _ObservedConversationSystem(SqliteProcessConversationSystem):
    """Pass through real sends unchanged; retain return values absent from the record."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.observed_sends: list[tuple[str | None, PromptDeliveryMode, PromptDeliveryFate]] = []

    async def send(self, *args: Any, **kwargs: Any) -> PromptDeliveryFate:
        fate = await super().send(*args, **kwargs)
        self.observed_sends.append(
            (kwargs.get("sender_message_id"), kwargs.get("mode", PromptDeliveryMode.queue), fate)
        )
        return fate


@dataclass
class _NoticeBench:
    db_path: Path
    transcript_path: Path
    ticket_id: str
    system: _ObservedConversationSystem
    store: ConversationStore

    def connect(self) -> sqlite3.Connection:
        return connect(str(self.db_path))

    async def tell(self) -> bool:
        return await tell_a_worker_it_lost_its_memory(
            self.ticket_id,
            connect_database=self.connect,
            conversation_system=self.system,
            worker_type_registry=configured_worker_type_registry(),
            now=lambda: 100,
        )

    async def notice_events(self) -> tuple[StoredConversationEvent, ...]:
        return tuple(
            event
            for event in await self.store.read_events_after("c", 0)
            if str(getattr(event.payload, "sender_message_id", "")).startswith(
                worker_memory.MEMORY_NOTICE_MESSAGE_PREFIX
            )
        )

    def requests(self, method: str) -> list[dict[str, Any]]:
        entries = (
            json.loads(line)
            for line in self.transcript_path.read_text(encoding="utf-8").splitlines()
        )
        return [
            entry["received"]
            for entry in entries
            if entry.get("received", {}).get("method") == method
        ]


async def _wait_until_idle(system: SqliteProcessConversationSystem) -> None:
    async def wait() -> None:
        while await system.is_running("c"):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), 5)
    await system.wait_until_quiescent()


@asynccontextmanager
async def _notice_bench(
    workspace: Path, *, script: dict[str, Any], busy: bool = False
) -> AsyncIterator[_NoticeBench]:
    db_path = workspace / "synthetic.db"
    transcript_path = workspace / "transcript.jsonl"
    script_path = workspace / "script.json"
    script_path.write_text(
        json.dumps({"transcript_path": str(transcript_path), **script}), encoding="utf-8"
    )
    codex_home = workspace / "synthetic-codex-home"
    argv, environment = scripted_app_server_launch(script_path)
    launch = CodexChildLaunch(
        argv=argv,
        environment_overrides=tuple({**environment, "CODEX_HOME": str(codex_home)}.items()),
    )
    store = ConversationStore(str(db_path), integer_now=lambda: 100)

    def make_child(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> CodexAppServerBackendChild:
        assert resolved_start.backend_key is ConversationBackendKey.codex
        return CodexAppServerBackendChild(
            launch=launch,
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
            remembered_login_rejection=RememberedCodexLoginRejection(),
        )

    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO projects(id,name,folder_path,created_at,updated_at) "
            "VALUES ('notice-project','Synthetic notice workspace',?,0,0)", (str(workspace),)
        )
        conn.commit()
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            project_id="notice-project",
            employee_backend="codex",
            employee_launch_model="gpt-5.4-mini",
            title="Synthetic memory notice evidence",
            brief="Isolated evidence only",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
        )
    system = _ObservedConversationSystem(
        store=store,
        message_files=ConversationMessageFiles(str(db_path)),
        backend_child_factories={key: make_child for key in ConversationBackendKey},
    )
    bench = _NoticeBench(db_path, transcript_path, ticket.id, system, store)
    try:
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="c",
                backend_key=ConversationBackendKey.codex,
                model="gpt-5.4-mini",
                workspace_folder=workspace,
            )
        )
        await system.send(
            "c",
            text_message_content("Synthetic original worker step"),
            sender_label="loop",
            sender_message_id="worker_step_message_original",
        )
        await _wait_until_idle(system)
        launched = json.loads(transcript_path.read_text(encoding="utf-8").splitlines()[0])
        child_environment = launched["launched"]["environment"]
        assert child_environment["HOME"] == os.environ["HOME"]
        assert child_environment["CODEX_HOME"] == os.environ["CODEX_HOME"]
        assert not any("API_KEY" in name or "TOKEN" in name for name in child_environment)
        # Synthetic immutable history, not a reconstruction of provider activity.
        compacted = await store.append_event("c", ContextCompactedEventPayload())
        with bench.connect() as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id='c', worker_step_claim='out' WHERE id=?",
                (ticket.id,),
            )
            conn.execute(
                "UPDATE conversations SET automatically_compacted_through_sequence=? "
                "WHERE conversation_id='c'", (compacted.sequence,),
            )
            assert worker_memory.current_memory_notice_key(conn, ticket.id) is not None
        if busy:
            await system.send(
                "c", text_message_content("Synthetic running turn"), sender_label="owner"
            )
        yield bench
    finally:
        await system.shutdown()


def test_explicit_steer_uncertainty_keeps_reservation_without_replacement(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _notice_bench(
            tmp_path,
            script={"turns": [{}, {"actions": []}], "steers": [{"outcome": "malformed"}]},
            busy=True,
        ) as bench:
            assert await bench.tell() is True
            (first,) = await bench.system.held_prompts("c")
            fate = await bench.system.promote_held_prompt(
                "c", first.held_prompt_id, HeldPromptPromotionMode.steer
            )
            assert isinstance(fate, PromptDeliveryUncertain)
            assert await bench.system.held_prompts("c") == ()
            events = await bench.notice_events()
            assert len(events) == 1
            assert isinstance(events[0].payload, PromptDeliveryUncertainEventPayload)
            assert events[0].payload.mode is PromptDeliveryMode.steer
            assert events[0].payload.sender_message_id == first.sender_message_id
            assert await bench.tell() is False
            assert await bench.system.held_prompts("c") == ()
            assert len(bench.requests("turn/start")) == 2
            assert len(bench.requests("turn/steer")) == 1
            assert await bench.notice_events() == events
            with bench.connect() as conn:
                inspection = worker_memory.inspect_memory_notices(conn, bench.ticket_id)
                assert len(inspection.reservations) == 1
                assert inspection.reservations[0].sender_message_id == first.sender_message_id
                assert len(inspection.evidence) == 1
                assert inspection.evidence[0].kind == "prompt_delivery_uncertain"
                assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c")
            # Only explicit promotion returned uncertainty. Ordinary queue did not.
            # This uncertainty is neither a delivered prompt nor an answered boundary.

    asyncio.run(asyncio.wait_for(exercise(), 15))


def test_concurrent_polls_emit_one_scripted_notice_request(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _notice_bench(tmp_path, script={"turns": [{}, {"actions": []}]}) as bench:
            results = await asyncio.gather(*(bench.tell() for _ in range(8)))
            assert results.count(True) == 1
            assert len(bench.requests("turn/start")) == 2
            assert len(await bench.notice_events()) == 1
            with bench.connect() as conn:
                inspection = worker_memory.inspect_memory_notices(conn, bench.ticket_id)
                assert len(inspection.reservations) == 1

    asyncio.run(asyncio.wait_for(exercise(), 15))


def test_discarded_held_notice_retains_consumption_and_unanswered_boundary(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with _notice_bench(
            tmp_path, script={"turns": [{}, {"actions": []}]}, busy=True
        ) as bench:
            assert await bench.tell() is True
            (held,) = await bench.system.held_prompts("c")
            assert await bench.system.discard_held_prompt("c", held.held_prompt_id)
            assert await bench.tell() is False
            assert await bench.system.held_prompts("c") == ()
            (discarded,) = await bench.notice_events()
            assert discarded.kind.value == "prompt_discarded"
            assert len(bench.requests("turn/start")) == 2  # original and busy turn only
            with bench.connect() as conn:
                inspection = worker_memory.inspect_memory_notices(conn, bench.ticket_id)
                assert len(inspection.reservations) == 1
                assert inspection.reservations[0].sender_message_id == held.sender_message_id
                assert len(inspection.evidence) == 1
                assert inspection.evidence[0].kind == "prompt_discarded"
                assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c")

    asyncio.run(asyncio.wait_for(exercise(), 15))


def test_wire_effect_before_receipt_crash_remains_consumed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        async with _notice_bench(tmp_path, script={"turns": [{}, {"actions": []}]}) as bench:
            receipt_write_reached = False

            async def crash_before_receipt(*args: Any, **kwargs: Any) -> Any:
                nonlocal receipt_write_reached
                # This storage call follows the adapter's successful wire exchange.
                receipt_write_reached = True
                raise asyncio.CancelledError("synthetic crash before prompt receipt commit")

            monkeypatch.setattr(bench.store, "append_delivered_prompt", crash_before_receipt)
            with pytest.raises(asyncio.CancelledError):
                await bench.tell()
            assert receipt_write_reached
            assert len(bench.requests("turn/start")) == 2
            assert await bench.notice_events() == ()
            with bench.connect() as conn:
                before = worker_memory.inspect_memory_notices(conn, bench.ticket_id)
                assert len(before.reservations) == 1
                assert before.evidence == ()
                assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c")
            await bench.system.shutdown()

            def refuse_child_launch(**kwargs: Any) -> CodexAppServerBackendChild:
                raise AssertionError("Crash does not permit another automatic attempt")

            restarted = _ObservedConversationSystem(
                store=ConversationStore(str(bench.db_path), integer_now=lambda: 101),
                message_files=ConversationMessageFiles(str(bench.db_path)),
                backend_child_factories={
                    key: refuse_child_launch for key in ConversationBackendKey
                },
            )
            bench.system = restarted
            try:
                assert await bench.tell() is False
                assert restarted.observed_sends == []
                assert len(bench.requests("turn/start")) == 2
                assert not await restarted.is_running("c")
                with bench.connect() as conn:
                    assert worker_memory.inspect_memory_notices(conn, bench.ticket_id) == before
                    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c")
            finally:
                await restarted.shutdown()
            # The script received a request before the injected crash. No durable
            # prompt receipt, provider admission, model delivery, or answer is inferred.

    asyncio.run(asyncio.wait_for(exercise(), 15))
