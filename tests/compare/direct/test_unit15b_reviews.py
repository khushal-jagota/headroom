"""Fresh-Python comparison of review admission across child-start boundaries.

The shared scripted process supplies the child. Real proposal, transfer, notice,
record and projection writers supply every compared row. No HTTP host is needed
for a race inside child preparation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.scripted.record_inspection import record_rows
from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry
from tests.support.synthetic_homes import create_synthetic_homes
from tests.unit.test_conversation_system import _resolve_review, _review_notice

from planner.conversation.api import delivery_fate_json
from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import TestClock as ClockForTest
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.manager_wakes import data as wakes
from planner.manager_wakes.contracts import WakeBatch
from planner.manager_wakes.runtime import deliver_batch
from planner.managers import data as managers
from planner.tickets import data as tickets

ROOT = Path(__file__).resolve().parents[3]


def source_rows(path: str) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return {
            name: [dict(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY 1")]
            for name in ("manager_wakes", "manager_wake_batches", "manager_wake_batch_members")
        }


async def exercise(spec: dict[str, Any]) -> dict[str, Any]:
    path = spec["path"]
    registry = ScriptedBackendRegistry()
    store = ConversationStore(path, integer_now=lambda: 100)
    core = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories={key: registry.make_child for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(path),
        live_tail=ConversationLiveTail(),
        unix_time_now=lambda: 100,
        monotonic_now=lambda: 100,
    )
    fates: list[Any] = []
    try:
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id="c",
                model="original",
                reasoning_effort="low",
                workspace_folder=Path("/tmp"),
            )
        )
        transfer = spec["case"].startswith("transfer")
        if transfer:
            await core.start_conversation(
                ConversationStartRequest(
                    conversation_id="current",
                    model="original",
                    reasoning_effort="low",
                    workspace_folder=Path("/tmp"),
                )
            )
            with closing(connect(path)) as conn:
                conn.execute(
                    "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff','c') "
                    "ON CONFLICT(agent_key) DO UPDATE SET conversation_id='c'"
                )
                conn.execute(
                    "UPDATE agents SET conversation_id='current' WHERE agent_key=?",
                    ("manager_" + spec["manager"],),
                )
                conn.commit()
                old_batch = wakes.batches_waiting_for_outcome(conn)[0]

        async def deliver(batch: WakeBatch) -> bool:
            return await deliver_batch(
                batch,
                connect_database=lambda: connect(path),
                conversation_system=core,
                process_token="test",
                now=lambda: 10,
            )

        if spec["case"] == "rebind":
            await core.send("c", text_message_content("Start"), sender_label="Owner")
            await registry.current["c"].finish()
            await core.wait_until_quiescent()
            await registry.configure(
                "c", "write_prompt", {"error": {"name": "NeedsRebind", "message": "replace"}}
            )
        await registry.configure("c", "start", {"barrier": "prepare"}, target="next")

        async def send() -> Any:
            return await core.send(
                "c",
                text_message_content(spec["message"]),
                sender_label="Chief",
                sender_message_id=spec["sender"],
                sender=CHIEF_PRINCIPAL,
                recipient=Principal(PrincipalKind.manager, spec["manager"]),
                model_change="new-model" if spec["case"] == "rebind" else None,
                reasoning_effort_change="high" if spec["case"] == "rebind" else None,
            )

        task = asyncio.create_task(deliver(old_batch) if transfer else send())
        # The child is constructed synchronously by the core before start awaits.
        while "c" not in registry.current or (
            spec["case"] == "rebind" and len(registry._children) < 2
        ):
            await asyncio.sleep(0)
        child = registry.current["c"]
        await child.wait_for_barrier("prepare")
        duplicate = asyncio.create_task(send()) if spec["case"] == "prepare" else None
        current_batch = None
        with closing(connect(path)) as conn:
            if spec["case"].startswith("transfer"):
                if spec["case"] == "transfer-resolved":
                    _resolve_review(Path(path), spec["ticket"])
                tickets.edit_ticket(
                    conn,
                    spec["ticket"],
                    edit={
                        "manager_id": spec["manager"],
                        "ceiling_holder": Principal(PrincipalKind.manager, spec["manager"]),
                    },
                    principal=OWNER_PRINCIPAL,
                    title_max_chars=200,
                    now=5,
                )
                if spec["case"] == "transfer-resolved":
                    tickets.file_current_proposal(
                        conn,
                        spec["ticket"],
                        body="New review",
                        principal=Principal(PrincipalKind.ticket, spec["ticket"]),
                        now=6,
                    )
                current_batch = wakes.claim_next_batch(conn, process_token="test", now=10)
                assert current_batch is not None

            else:
                _resolve_review(Path(path), spec["ticket"])
        await child.release("prepare")
        result = await task
        fates.append({"delivered": result} if transfer else delivery_fate_json(result))
        if duplicate:
            fates.append(delivery_fate_json(await duplicate))
            fates.append(delivery_fate_json(await send()))
        if current_batch:
            fates.append({"delivered": await deliver(current_batch)})
        await core.wait_until_quiescent()
        observations = {identity: await registry.inspect(identity) for identity in ("c", "current")}
        if spec["case"] == "prepare":
            await core.shutdown()
            await registry.close()
            registry = ScriptedBackendRegistry()
            core = SqliteProcessConversationSystem(
                store=store,
                backend_child_factories={
                    key: registry.make_child for key in ConversationBackendKey
                },
                message_files=ConversationMessageFiles(path),
                live_tail=ConversationLiveTail(),
                unix_time_now=lambda: 100,
                monotonic_now=lambda: 100,
            )
            fates.append(delivery_fate_json(await send()))
            assert not registry.current

        return {
            "fates": fates,
            "records": {identity: record_rows(path, identity) for identity in ("c", "current")},
            "sources": source_rows(path),
            "backend": observations,
            "running": await core.is_running("c"),
        }
    finally:
        await core.shutdown()
        await registry.close()


def normalize(value: Any) -> Any:
    """Map generated delivery/cursor identities, preserving all content and hashes."""
    text = json.dumps(value, sort_keys=True)
    identities: dict[str, str] = {}
    for pattern in (
        r"manager_delivery_wake_[0-9a-f]{32}",
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    ):
        for match in re.findall(pattern, text):
            identities.setdefault(match, f"generated-{len(identities)}")
    for original, replacement in identities.items():
        text = text.replace(original, replacement)
    return json.loads(text)


def test_unit15b_review_start_boundaries(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        **create_synthetic_homes(tmp_path),
    }
    reports = []
    destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    for case in ("prepare", "rebind", "transfer", "transfer-resolved"):
        seed = tmp_path / f"{case}-seed.sqlite"
        with patch.dict(os.environ, environment, clear=True):
            with closing(connect(str(seed))) as conn:
                create_schema(conn)
                conn.execute("INSERT INTO conversation_runtime_epoch VALUES (1,'unit15b')")
                manager = managers.create_manager(
                    conn,
                    title="New reviewer",
                    project_id="project_other",
                    clock=ClockForTest(datetime.fromtimestamp(10, UTC)),
                )
                conn.commit()
            ticket, sender, message = _review_notice(seed)
        paths = [tmp_path / f"{case}-{side}.sqlite" for side in ("python", "typescript")]
        for path in paths:
            with closing(sqlite3.connect(seed)) as source, closing(sqlite3.connect(path)) as target:
                source.backup(target)
        spec = {
            "case": case,
            "ticket": ticket,
            "sender": sender,
            "message": message,
            "manager": manager.id,
            "path": str(paths[0]),
        }
        with patch.dict(os.environ, environment, clear=True):
            expected = asyncio.run(asyncio.wait_for(exercise(spec), 30))
        spec["path"] = str(paths[1])
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {run} from './server/conversation/testing/unit15b_reviews.ts';"
                "process.stdout.write(JSON.stringify(await run(JSON.parse(process.argv[1]))));",
                json.dumps(spec),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=40,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        actual = json.loads(completed.stdout)
        reports.append({"case": case, "python": expected, "typescript": actual})
        (destination / "unit-15b-review-starts.json").write_text(
            json.dumps({"seconds": time.perf_counter() - started, "cases": reports}, indent=2)
        )
        native = {
            identity: [text for child in children for text in child["delivered_text"]]
            for identity, children in expected["backend"].items()
        }
        if case == "prepare":
            assert [fate["fate"] for fate in expected["fates"]] == ["suppressed"] * 4
            assert [event["kind"] for event in expected["records"]["c"]["events"]] == [
                "prompt_discarded"
            ]
            assert native["c"] == []
            assert {"suppressed", "unavailable"} <= {
                fact["status"] for fact in expected["records"]["c"]["lifecycle"]
            }

        if case.startswith("transfer"):
            assert expected["fates"][0] == {"delivered": False}
        else:
            assert expected["fates"][0]["fate"] == "suppressed"
        assert not expected["running"]
        if case.startswith("transfer"):
            assert expected["fates"][-1] == {"delivered": True}
            old_sender = expected["records"]["c"]["events"][0]["payload"]["sender_message_id"]
            new_sender = expected["records"]["current"]["events"][0]["payload"]["sender_message_id"]
            assert old_sender != new_sender
            assert native["c"] == [] and len(native["current"]) == 1

            assert len(expected["records"]["c"]["events"]) == 1
            assert expected["records"]["c"]["events"][0]["kind"] == "prompt_discarded"
            assert [e["kind"] for e in expected["records"]["current"]["events"]] == ["prompt"]
        if case == "rebind":
            assert expected["records"]["c"]["conversation"]["model"] == "original"
            assert expected["records"]["c"]["conversation"]["reasoning_effort"] == "low"
            assert native["c"] == ["Start"]
            assert any(call["command"] == "stop" for call in expected["backend"]["c"][-1]["calls"])

        assert normalize(actual) == normalize(expected)
    destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "unit-15b-review-starts.json").write_text(
        json.dumps({"seconds": time.perf_counter() - started, "cases": reports}, indent=2)
    )
