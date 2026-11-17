"""Fresh core comparisons for maintenance eligibility and activity provenance.

These eight cases are the newly merged source test boundaries. The existing
scripted process proves actual deliveries. SQLite rows prove maintenance activity,
read admission, and backlog/reset behavior. No provider or HTTP host is added.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot
from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry
from tests.unit.test_conversation_system import _backlog_ticket, _link_worker_ticket

from planner.conversation.api import delivery_fate_json
from planner.conversation.backends.contracts import ContextCompacted, ToolCallStarted
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryMode,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema
from planner.runtime.conversation_start import reset_ticket_conversation
from planner.runtime.logic.worker_step_prompt import MEMORY_LOSS_NOTICE, compose_worker_step_prompt
from planner.runtime.worker_memory import (
    MEMORY_NOTICE_MESSAGE_PREFIX,
    conversation_holds_an_unanswered_memory_loss,
    ticket_needs_memory_notice,
)
from planner.tickets import data as tickets_data

ROOT = Path(__file__).resolve().parents[3]
CASES = (
    "memory_only",
    "held_batch",
    "steer_accepted",
    "steer_uncertain",
    "before_selection",
    "after_selection",
    "queued_backlog",
    "replaced",
)


def comparison(value: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Manager-approved per-run bijection for exact generated notice IDs only."""
    aliases: dict[str, str] = {}

    def walk(item: Any, field: str = "") -> Any:
        if isinstance(item, dict):
            return {key: walk(child, key) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child, field) for child in item]
        if (
            isinstance(item, str)
            and field in {"sender_message_id", "awaiting", "lookup_id"}
            and re.fullmatch(MEMORY_NOTICE_MESSAGE_PREFIX + r"[0-9a-f]{32}", item)
        ):
            if field == "lookup_id":
                assert item in aliases, "A notice lookup must name a recorded notice"
                return aliases[item]
            return aliases.setdefault(
                item, MEMORY_NOTICE_MESSAGE_PREFIX + f"notice-{len(aliases) + 1}"
            )
        return item

    return walk(normalize(value)), aliases


async def python_case(path: Path, case: str) -> dict[str, Any]:
    now = 1000
    registry = ScriptedBackendRegistry()
    store = ConversationStore(str(path), integer_now=lambda: now)
    core = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories={key: registry.make_child for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(str(path)),
        live_tail=ConversationLiveTail(),
        unix_time_now=lambda: now,
        monotonic_now=lambda: now,
    )
    identity = "old" if case == "replaced" else "c"
    checkpoints: list[dict[str, Any]] = []
    sequence = 0

    async def send(
        text: str, *, notice: bool = False, steer: bool = False, wake: bool = False
    ) -> dict[str, Any]:
        nonlocal sequence
        sequence += 1
        fate = await core.send(
            identity,
            text_message_content(text),
            sender_label="Panels" if notice else "owner",
            sender_message_id=(
                MEMORY_NOTICE_MESSAGE_PREFIX
                if notice
                else "worker_step_"
                if wake or (case == "memory_only" and sequence == 1)
                else "owner-"
            )
            + str(sequence),
            mode=PromptDeliveryMode.steer if steer else PromptDeliveryMode.queue,
        )
        return delivery_fate_json(fate)

    async def finish() -> None:
        await registry.current[identity].finish()
        await core.wait_until_quiescent()

    async def compact() -> None:
        await registry.current[identity].emit(ContextCompacted())
        await registry.current[identity].flush_events()
        await core.wait_until_quiescent()

    async def sweep() -> None:
        await core._sweep_idle_children()
        await core.wait_until_quiescent()

    async def capture(name: str, fates: list[dict[str, Any]] | None = None) -> None:
        conn = connect(str(path))
        try:
            debt = conversation_holds_an_unanswered_memory_loss(conn, identity)
            candidate = ticket_needs_memory_notice(conn, "t_worker")
        finally:
            conn.close()
        checkpoints.append(
            {
                "name": name,
                "fates": fates or [],
                "rows": snapshot(str(path)),
                "native": await registry.inspect(identity),
                "open_memory_only": store._open_turn_holds_only_memory_notices_sync(identity),
                "eligibility": await store.automated_maintenance_suppression_reason(identity),
                "debt": debt,
                "notice_candidate": candidate,
            }
        )

    async def freeze_selection(change: Callable[[], Any]) -> list[str]:
        select = store.conversations_due_for_automatic_compaction
        selected = await select(due_at_or_before=now - 50 * 60, activity_after=now - 60 * 60)
        result = change()
        if asyncio.iscoroutine(result):
            await result

        async def once(**window: int) -> tuple[str, ...]:
            store.conversations_due_for_automatic_compaction = select  # type: ignore[method-assign]
            return selected

        store.conversations_due_for_automatic_compaction = once  # type: ignore[method-assign]
        return list(selected)

    try:
        if case == "memory_only":
            conn = connect(str(path))
            try:
                with conn:
                    conn.execute("UPDATE tickets SET worker_step_claim='out' WHERE id='t_worker'")
            finally:
                conn.close()
            await send("real work")
            await finish()
            now += 50 * 60
            await sweep()
            await compact()
            await finish()
            await capture("compact-debt")
            _backlog_ticket(path)
            await capture("backlogged-debt")
            conn = connect(str(path))
            try:
                with conn:
                    conn.execute("UPDATE tickets SET is_backlogged=0 WHERE id='t_worker'")
            finally:
                conn.close()
            stale = await send("Your context was compacted", notice=True)
            await capture("idle-notice-is-stale", [stale])
            await send("work that compacts", wake=True)
            await compact()
            await finish()
            fate = await send("Your context was compacted", notice=True)
            await registry.current[identity].emit(
                ToolCallStarted(
                    tool_call_id="t1",
                    title="panels worker",
                    tool_kind="execute",
                )
            )
            await registry.current[identity].flush_events()
            await core.wait_until_quiescent()
            await capture("active-memory-notice", [fate])
            await finish()
            now += 51 * 60
            await sweep()
            await capture("memory-does-not-recompact")
            # Send outside the compaction window: TypeScript never compacts at send time.
            now += 10 * 60
            fate = await send("more work")
            await finish()
            now += 50 * 60
            await sweep()
            await capture("real-work-restores-compaction", [fate])
        elif case in ("held_batch", "steer_accepted", "steer_uncertain"):
            conn = connect(str(path))
            try:
                with conn:
                    conn.execute("UPDATE tickets SET worker_step_claim='out' WHERE id='t_worker'")
            finally:
                conn.close()
            await send("work that compacts", wake=True)
            await compact()
            await finish()
            first = await send("Your context was compacted", notice=True)
            await capture("notice-started", [first])
            if case == "held_batch":
                second = await send("Your context was compacted", notice=True)
                owner = await send("owner")
                await finish()
            else:
                await registry.configure(
                    identity,
                    "steer",
                    {
                        "result": {
                            "fate": "uncertain" if case == "steer_uncertain" else "accepted",
                            "composed_content_delivered": True,
                        }
                    },
                )
                second = await send("owner", steer=True)
                owner = second
            await capture("real-work-joined", [second, owner])
            await finish()
            now += 50 * 60
            await sweep()
            await capture("joined-work-is-due")
            if case == "held_batch":

                async def queue_wake(revision: int) -> tuple[str, str, dict[str, Any]]:
                    sender_id = f"worker_step_message_t_worker:{revision}"
                    with closing(connect(str(path))) as conn:
                        with conn:
                            conn.execute(
                                "INSERT INTO worker_step_dispatch_intents "
                                "VALUES ('t_worker',?,?, 'dispatch_started')",
                                (revision, sender_id),
                            )
                        ticket = tickets_data.read_ticket(conn, "t_worker")
                        text = compose_worker_step_prompt(
                            ticket, worker_type_definition=ticket.workflow
                        ).model_text
                    fate = await core.send(
                        identity,
                        text_message_content(text),
                        sender_label="Panels",
                        sender_message_id=sender_id,
                    )
                    return sender_id, text, delivery_fate_json(fate)

                stale = await send("Your context was compacted", notice=True)
                wake_id, wake_text, wake_fate = await queue_wake(0)
                await queue_wake(1)
                first_child = registry.current[identity]
                await registry.configure(
                    identity,
                    "write_prompt",
                    {
                        "barrier": "memory-rebind",
                        "error": {"name": "NeedsRebind", "message": "fresh child"},
                    },
                )
                await compact()
                finishing = asyncio.create_task(finish())
                await first_child.wait_for_barrier("memory-rebind")
                with closing(connect(str(path))) as conn, conn:
                    conn.execute(
                        "UPDATE conversations SET automatically_compacted_through_sequence=0 "
                        "WHERE conversation_id=?",
                        (identity,),
                    )
                await first_child.release("memory-rebind")
                await finishing
                await capture("wake-rebind-recomputed", [stale, wake_fate])
                checkpoints[-1]["wake"] = {"sender_message_id": wake_id, "text": wake_text}
                checkpoints[-1]["awaiting"] = sorted(
                    await core.sender_message_ids_awaiting_outcome(identity)
                )
                stale = await send("Your context was compacted", notice=True)
                wake_id, wake_text, wake_fate = await queue_wake(2)
                await registry.configure(
                    identity,
                    "write_prompt",
                    {
                        "error": {
                            "name": "Error",
                            "message": "acknowledgement lost after possible emission",
                        }
                    },
                )
                await compact()
                await finish()
                replay = await core.send(
                    identity,
                    text_message_content(wake_text),
                    sender_label="Panels",
                    sender_message_id=wake_id,
                )
                await capture("wake-note-uncertain", [stale, wake_fate, delivery_fate_json(replay)])
                checkpoints[-1]["wake"] = {"sender_message_id": wake_id, "text": wake_text}
                checkpoints[-1]["awaiting"] = sorted(
                    await core.sender_message_ids_awaiting_outcome(identity)
                )
        else:
            await send("real work")
            if case == "queued_backlog":
                notice = await send("Your context was compacted", notice=True)
                owner = await send("behind it")
                _backlog_ticket(path)
                await finish()
                await capture("queued-notice-suppressed-owner-survives", [notice, owner])
            else:
                await finish()
                now += (51 if case == "replaced" else 50) * 60
                selected: list[str] = []
                if case == "before_selection":
                    _backlog_ticket(path)
                elif case == "after_selection":
                    selected = await freeze_selection(lambda: _backlog_ticket(path))
                else:

                    async def reset() -> None:
                        conn = connect(str(path))
                        try:
                            await reset_ticket_conversation(core, conn, "t_worker", now=2)
                        finally:
                            conn.close()

                    selected = await freeze_selection(reset)
                await sweep()
                now += 5 * 60
                await sweep()
                await capture("maintenance-suppressed")
                checkpoints[-1]["selected_before_change"] = selected
                if case == "replaced":
                    now += 10 * 60
                    fate = await send("Your context was compacted", notice=True)
                else:
                    fate = await send("still there?")
                await core.wait_until_quiescent()
                await capture("later-send", [fate])
        return {"case": case, "checkpoints": checkpoints}
    finally:
        await core.shutdown()
        await registry.close()


def positive(result: dict[str, Any]) -> None:
    """Require the source test's concrete outcome after exact row comparison."""
    case = result["case"]
    points = {point["name"]: point for point in result["checkpoints"]}

    def prompts(point: dict[str, Any]) -> list[str]:
        return [text for child in point["native"] for text in child["delivered_text"]]

    def discards(point: dict[str, Any]) -> list[tuple[str, str]]:
        return [
            (row["payload"]["text"], row["payload"]["suppression_reason"])
            for row in point["rows"]["conversation_events"]
            if row["kind"] == "prompt_discarded"
        ]

    if case == "memory_only":
        assert points["compact-debt"]["debt"] is True
        assert points["compact-debt"]["notice_candidate"] is False
        assert points["backlogged-debt"]["debt"] is True
        assert points["backlogged-debt"]["notice_candidate"] is False
        assert points["active-memory-notice"]["open_memory_only"] is True
        assert points["idle-notice-is-stale"]["fates"][0] == {
            "fate": "suppressed",
            "reason": "worker_memory_notice_no_longer_needed",
        }
        assert prompts(points["memory-does-not-recompact"]).count("/compact") == 1
        assert prompts(points["real-work-restores-compaction"]).count("/compact") == 2
        assert points["real-work-restores-compaction"]["fates"][0]["fate"] == "started"
        assert prompts(points["real-work-restores-compaction"])[-2:] == ["more work", "/compact"]
    elif case in ("held_batch", "steer_accepted", "steer_uncertain"):
        assert points["notice-started"]["open_memory_only"] is True
        assert points["real-work-joined"]["open_memory_only"] is False
        assert prompts(points["joined-work-is-due"])[-1] == "/compact"
        if case == "held_batch":
            calls = [
                call
                for child in points["real-work-joined"]["native"]
                for call in child["calls"]
                if call["command"] == "write_prompt"
            ]
            assert calls[-1]["arguments"]["sender_message_count"] == 1
            assert discards(points["real-work-joined"])[-1] == (
                "Your context was compacted",
                "worker_memory_notice_no_longer_needed",
            )
            rebound, uncertain = points["wake-rebind-recomputed"], points["wake-note-uncertain"]
            # Read actual write inputs, including the refused attempt before rebind.
            writes = [
                call
                for child in rebound["native"]
                for call in child["calls"]
                if call["command"] == "write_prompt"
            ]

            def joined_text(call: dict[str, Any]) -> str:
                return "".join(piece["text"] for piece in call["arguments"]["sender_content"])

            assert joined_text(writes[-2]).count(MEMORY_LOSS_NOTICE) == 1
            assert MEMORY_LOSS_NOTICE not in joined_text(writes[-1])
            assert rebound["awaiting"] == []
            for point, kind in ((rebound, "prompt"), (uncertain, "prompt_delivery_uncertain")):
                rows = [
                    row["payload"]
                    for row in point["rows"]["conversation_events"]
                    if row["kind"] == kind
                ]
                wake = next(
                    row
                    for row in rows
                    if row.get("sender_message_id") == point["wake"]["sender_message_id"]
                )
                assert wake["text"] == point["wake"]["text"]
                assert MEMORY_LOSS_NOTICE not in wake["text"]
            unknown = [
                row["payload"]
                for row in uncertain["rows"]["conversation_events"]
                if row["kind"] == "prompt_delivery_uncertain"
            ]
            assert len(unknown) == 2
            assert sum(MEMORY_LOSS_NOTICE in row["text"] for row in unknown) == 1
            assert uncertain["awaiting"] == [uncertain["wake"]["sender_message_id"]]
            assert (
                discards(uncertain)[-2:]
                == [("Your context was compacted", "worker_memory_notice_included_in_step")] * 2
            )
        else:
            assert points["real-work-joined"]["fates"][0]["fate"] == (
                "uncertain" if case == "steer_uncertain" else "injected"
            )
    elif case == "queued_backlog":
        point = points["queued-notice-suppressed-owner-survives"]
        assert prompts(point) == ["real work", "behind it"]
        assert discards(point) == [("Your context was compacted", "worker_ticket_backlogged")]
        assert point["fates"][0]["fate"] == "queued"
    else:
        point = points["later-send"]
        if case == "replaced":
            assert point["fates"][0]["fate"] == "suppressed"
            assert prompts(point) == ["real work"]
            assert discards(point) == [
                ("/compact", "worker_conversation_replaced"),
                ("Your context was compacted", "worker_conversation_replaced"),
            ]
            assert (
                sum(
                    call["command"] == "start"
                    for child in point["native"]
                    for call in child["calls"]
                )
                == 1
            )
            assert point["eligibility"] == "worker_conversation_replaced"
        else:
            assert point["fates"][0]["fate"] == "started"
            assert prompts(point) == ["real work", "still there?"]
            assert discards(point) == (
                [("/compact", "worker_ticket_backlogged")] if case == "after_selection" else []
            )
        if case != "before_selection":
            assert points["maintenance-suppressed"]["selected_before_change"] == [
                "old" if case == "replaced" else "c"
            ]


def test_unit15b_maintenance_matches_fresh_python(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    selected = environment(tmp_path)
    reports: list[dict[str, Any]] = []
    for case in CASES:
        directory = tmp_path / case
        directory.mkdir()
        py_db, ts_db = directory / "python.sqlite", directory / "typescript.sqlite"
        with patch.dict(os.environ, selected, clear=True):
            conn = connect(str(py_db))
            try:
                create_schema(conn)
                conn.commit()
            finally:
                conn.close()
            seed_store = ConversationStore(str(py_db), integer_now=lambda: 1000)
            asyncio.run(
                seed_store.create_conversation(
                    resolve_conversation_start_request(
                        ConversationStartRequest(
                            conversation_id="old" if case == "replaced" else "c",
                            model="model",
                            workspace_folder=Path("/tmp"),
                        )
                    )
                )
            )
            _link_worker_ticket(py_db, "old" if case == "replaced" else "c")
            with sqlite3.connect(py_db) as source, sqlite3.connect(ts_db) as target:
                source.backup(target)
            python = asyncio.run(asyncio.wait_for(python_case(py_db, case), 30))
        spec = {"database": str(ts_db), "case": case}
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {run} from './server/conversation/testing/unit15b_maintenance.ts';"
                "process.stdout.write(JSON.stringify(await run(JSON.parse(process.argv[1]))));",
                json.dumps(spec),
            ],
            cwd=ROOT,
            env=selected,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        typescript = cast(dict[str, Any], json.loads(completed.stdout))
        expected, python_ids = comparison(python)
        actual, typescript_ids = comparison(typescript)
        reports.append(
            {
                "case": case,
                "python": expected,
                "typescript": actual,
                "raw": {"python": python, "typescript": typescript},
                "notice_identity_aliases": {"python": python_ids, "typescript": typescript_ids},
            }
        )
        destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-15b.maintenance.json").write_text(
            json.dumps({"seconds": time.perf_counter() - started, "cases": reports}, indent=2)
            + "\n"
        )
        assert expected == actual
        positive(expected)
        positive(actual)
