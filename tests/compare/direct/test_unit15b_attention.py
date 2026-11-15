"""Prove automatic prompt provenance through real core attention participants.

The prior summary proof used prewritten events. This case feeds two scripted
turns through the real core, including its held queue, before it reads a summary.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot
from tests.compare.scripted.scripted_child import ScriptedChild

from planner.attention.actions import read_summary
from planner.attention.contracts import SummaryRequest
from planner.conversation.api import delivery_fate_json
from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.notifications import apns_data
from planner.notifications import data as notifications
from planner.notifications.logic.preview import message_preview, notification_label
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.data import create_ticket

ROOT = Path(__file__).resolve().parents[3]
START = int(datetime(2026, 11, 2, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())


def attention_rows(path: str) -> dict[str, Any]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
            for table in ("attention_history_metadata",)
        }


async def exercise(
    database: str, ticket: str, workspace: str, preview_cases: list[str]
) -> dict[str, Any]:
    now = START - 86400
    store = ConversationStore(database, integer_now=lambda: now)
    children: list[ScriptedChild] = []

    def factory(*, event_sink: Any, **_: Any) -> ScriptedChild:
        child = ScriptedChild(event_sink)
        children.append(child)
        return child

    core = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories={key: factory for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(database),
        unix_time_now=lambda: now,
        monotonic_now=lambda: float(now),
    )
    try:
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id="attention-system",
                model="model",
                workspace_folder=Path(workspace),
            )
        )
        conn = connect(database)
        try:
            conn.execute(
                "UPDATE tickets SET conversation_id='attention-system' WHERE id=?", (ticket,)
            )
            conn.commit()
        finally:
            conn.close()
        now = START + 100
        fates = []
        for text in ("Run the next worker step", "A queued worker step"):
            fates.append(
                delivery_fate_json(
                    await core.send(
                        "attention-system",
                        text_message_content(text),
                        sender_label="Panels loop",
                    )
                )
            )
        for _ in range(2):
            await children[0].finish()
            await core.wait_until_quiescent()
        conn = connect(database)
        try:
            summary = read_summary(
                conn, SummaryRequest(date(2026, 11, 2), date(2026, 11, 2)), now=START + 86401
            )
        finally:
            conn.close()
        result: dict[str, Any] = {
            "fates": fates,
            "native_text": (await children[0].inspect())["delivered_text"],
            "rows": snapshot(database),
            "attention": attention_rows(database),
            "summary": summary,
        }
        content_started = time.perf_counter()
        result["notification_content"] = await notification_content_proof(
            store, database, ticket, now, preview_cases
        )
        result["content_seconds"] = time.perf_counter() - content_started
        freshness_started = time.perf_counter()
        result["notification_freshness"] = notification_freshness_proof(database, ticket, now)
        result["freshness_seconds"] = time.perf_counter() - freshness_started
        return result
    finally:
        await core.shutdown()
        for child in children:
            await child.terminate()


async def notification_content_proof(
    store: ConversationStore, database: str, ticket: str, now: int, cases: list[str]
) -> dict[str, Any]:
    """Use the original store and Ticket after the provenance result was captured."""
    sender = Principal(PrincipalKind.ticket, ticket)

    def message(text: str) -> MessageToOwnerEventPayload:
        return MessageToOwnerEventPayload(
            content=text_message_content(text), sender=sender,
            recipient=OWNER_PRINCIPAL, sender_label="Wren Manager",
        )

    first = await store.append_event(
        "attention-system",
        message("**Ready** to [review](https://private.invalid).\nPlease check `copy`."),
    )
    await store.append_event(
        "attention-system", message("Later words must not replace the snapshot")
    )
    with closing(connect(database)) as conn:
        conn.execute(
            "UPDATE tickets SET title='Later name',conversation_id=NULL WHERE id=?", (ticket,),
        )
        conn.commit()
        notifications.queue_deliveries(conn, now)
        web = next(
            item.intent for item in notifications.pending_deliveries(conn, now)
            if item.edge.subject_id == ticket and item.edge.notification_type == "awaiting_reply"
        )
        native = next(
            item.intent for item in apns_data.pending_deliveries(conn, now)
            if item.edge.subject_id == ticket and item.edge.notification_type == "awaiting_reply"
        )
        assert web.body == "Ready to review. Please check copy." and web.reply is None
        assert native.reply is not None
        assert native.reply.message_id == f"attention-system:{first.sequence}"
        conn.execute("UPDATE tickets SET title=? WHERE id=?", ("📋" * 90, ticket))
        conn.commit()
        tickets_data.file_current_proposal(
            conn, ticket, body="A new proposal", principal=sender, now=now
        )
        workflow = json.loads(conn.execute(
            "SELECT workflow_json FROM tickets WHERE id=?", (ticket,),
        ).fetchone()[0])
        for stage in workflow["stages"]:
            stage["label"] = "Later stage label"
        conn.execute(
            "UPDATE tickets SET title='Later approval name',workflow_json=? WHERE id=?",
            (json.dumps(workflow), ticket),
        )
        conn.commit()
        notifications.queue_deliveries(conn, now)
        rows = {
            table: [
                {
                    key: json.loads(value)
                    if key == "context_json" and value is not None else value
                    for key, value in dict(row).items()
                }
                for row in conn.execute(
                    f"SELECT * FROM {table} WHERE subject_kind='ticket' "
                    "AND subject_id=? ORDER BY notification_type,generation", (ticket,),
                )
            ]
            for table in (
                "notification_attention_edges", "notification_deliveries",
                "notification_apns_deliveries",
            )
        }
        approval = next(
            row for row in rows["notification_deliveries"]
            if row["notification_type"] == "awaiting_approval"
        )
        assert approval["title"] == "📋" * 59 + "…"
        assert approval["body"] != "Later stage label waits for your approval."
    return {
        "rows": rows, "web": asdict(web), "native": asdict(native),
        "previews": [
            {"label": notification_label(text), "preview": message_preview(text)} for text in cases
        ],
    }


def notification_freshness_proof(database: str, ticket: str, now: int) -> dict[str, Any]:
    """Reuse the content phase's actual approval edge and both queued channels."""
    with closing(connect(database)) as conn:
        def snapshot_rows() -> dict[str, Any]:
            return {
                table: [
                    {
                        key: json.loads(value)
                        if key == "context_json" and value is not None else value
                        for key, value in dict(row).items()
                    }
                    for row in conn.execute(
                        f"SELECT * FROM {table} WHERE subject_kind='ticket' "
                        "AND subject_id=? ORDER BY notification_type,generation", (ticket,),
                    )
                ]
                for table in (
                    "notification_attention_state", "notification_attention_edges",
                    "notification_deliveries", "notification_apns_deliveries",
                )
            }

        def selected(at: int) -> list[Any]:
            return [
                sorted(
                    [asdict(row.edge) for row in rows if row.edge.subject_id == ticket],
                    key=lambda row: (row["notification_type"], row["generation"]),
                )
                for rows in (
                    notifications.pending_deliveries(conn, at),
                    apns_data.pending_deliveries(conn, at),
                )
            ]

        notifications.queue_deliveries(conn, now)
        before = snapshot_rows()
        fresh = selected(now + 299)
        after_fresh = snapshot_rows()
        assert all(
            any(edge["notification_type"] == "awaiting_approval" for edge in rows)
            for rows in fresh
        )
        expired = selected(now + 300)
        after_expired = snapshot_rows()
        assert expired == [[], []]
        for table in ("notification_attention_state", "notification_attention_edges"):
            assert before[table] == after_expired[table]
        conn.commit()
        return dict(before=before, fresh=fresh, after_fresh=after_fresh,
                    expired=expired, after_expired=after_expired)


def test_unit15b_agent_only_attention(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    selected = environment(tmp_path)
    py_db, ts_db = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    preview_cases = [
        "# Ready\n\n- **First** [link](https://private.invalid)\n- `second`",
        "![private](https://private.invalid/image.png)", "", "界" * 241,
        "Visible https://example.com and <https://example.org>", "\ufeffa\u0085b\u001cc",
    ]
    for budget in (60, 240):
        for cluster in ("👩‍💻", "🇬🇧", "e\u0301"):
            preview_cases.extend((
                "a" * (budget - 2) + cluster + "tail",
                "a" * (budget - 1 - len(cluster)) + cluster + "xy",
            ))
    with patch.dict(os.environ, selected, clear=True):
        conn = connect(str(py_db))
        try:
            create_schema(conn)
            ticket = create_ticket(
                conn,
                title="Agent-only attention",
                principal=OWNER_PRINCIPAL,
                now=START - 86400,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type="coding",
            )
            conn.execute("UPDATE attention_history_metadata SET started_at=?", (START - 86400,))
            notifications.register_subscription(
                conn, endpoint="https://push.invalid/content", p256dh="synthetic",
                auth="synthetic", now=START,
            )
            apns_data.register_device(
                conn, device_token="aabb", bundle_id="invalid.panels.ios",
                environment="development", now=START,
            )
            conn.commit()
            with sqlite3.connect(ts_db) as target:
                conn.backup(target)
        finally:
            conn.close()
        python = asyncio.run(exercise(str(py_db), ticket.id, str(tmp_path), preview_cases))
    spec = {
        "database": str(ts_db), "ticket": ticket.id, "workspace": str(tmp_path),
        "start": START, "preview_cases": preview_cases,
    }
    input_path = tmp_path / "attention-input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
            "const {exercise}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await exercise("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/unit15b_attention.ts"),
        ],
        cwd=ROOT,
        env=selected,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    typescript = json.loads(completed.stdout)
    content_seconds = {
        "python": python.pop("content_seconds"), "typescript": typescript.pop("content_seconds"),
    }
    freshness_seconds = {
        "python": python.pop("freshness_seconds"),
        "typescript": typescript.pop("freshness_seconds"),
    }
    expected, actual = normalize(python), normalize(typescript)
    report = {
        "python": python,
        "typescript": typescript,
        "compared_python": expected,
        "compared_typescript": actual,
        "seconds": time.perf_counter() - started,
        "content_seconds": content_seconds,
        "freshness_seconds": freshness_seconds,
        "source_test": "test_system_prompt_provenance_keeps_agent_only_attention_share_usable",
        "boundary": (
            "Two real-core scripted turns, queued delivery, actual area participants "
            "and populated attention summary"
        ),
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / "unit15b-attention.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
    for result in (python, typescript):
        assert result["fates"] == [
            {"fate": "started"},
            {"fate": "queued", "queue_position": 1},
        ]
        assert result["native_text"] == [
            "Run the next worker step",
            "A queued worker step",
        ]
        prompts = [
            json.loads(row["payload"])
            for row in result["rows"]["conversation_events"]
            if row["kind"] == "prompt"
        ]
        assert len(prompts) == 2
        assert all(
            prompt["system_generated"] is True and prompt.get("sender") is None
            for prompt in prompts
        )
        figures = result["summary"]["totals"]["current"]
        assert figures["owner_messages"]["value"] == 0
        assert figures["owner_messages"]["coverage_status"] == "complete"
        assert figures["active_ticket_count"]["value"] == 1
        assert figures["without_owner_involvement_share"]["value"] == 1
        assert result["attention"]["attention_history_metadata"]
        assert all(
            result["rows"][table]
            for table in (
                "conversation_lifecycle_facts",
                "conversation_view_rows",
                "conversation_view_turns",
            )
        )
