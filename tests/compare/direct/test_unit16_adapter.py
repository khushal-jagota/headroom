"""One scripted ACP peer behind the fresh Python and TS real conversation cores.

The shared action list names each scenario. Only owned Node fixture processes start.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot

from planner.conversation.api import delivery_fate_json
from planner.conversation.backends.hermes_acp import AcpChildLaunch, HermesAcpBackendChildFactory
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    WorkspacePathPolicy,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    ModelThinkingFrame,
    ToolCallProgressFrame,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import MessageImage, text_message_content
from planner.conversation.message_files import ConversationMessageFiles, MessageFileMissing
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]


def send(name: str, **values: Any) -> dict[str, Any]:
    return {"operation": "send", "text": f"[CASE:{name}]", **values}


def ended(count: int) -> dict[str, Any]:
    return {"operation": "ended", "count": count}


CASES = [
    {"name": "plain", "actions": [send("plain"), ended(1)]},
    {"name": "tools", "actions": [send("tools"), ended(1)]},
    {"name": "media", "actions": [send("media", image=True), ended(1)]},
    {
        "name": "permission",
        "actions": [
            send("permission"),
            {"operation": "answer"},
            ended(1),
            send("permission"),
            {"operation": "remember_ask"},
            {"operation": "interrupt"},
            ended(2),
            {"operation": "late_answer"},
        ],
    },
    {
        "name": "steer",
        "actions": [send("steer-base"), send("steer-accepted", mode="steer"), ended(1)],
    },
    {
        "name": "steer-refused",
        "actions": [
            send("steer-base"),
            send("steer-refused", mode="steer"),
            {"operation": "interrupt"},
            ended(2),
        ],
    },
    {
        "name": "steer-uncertain",
        "actions": [
            send("steer-base"),
            send("steer-uncertain", mode="steer"),
            {"operation": "interrupt"},
            ended(1),
            send("steer-base"),
            send("steer-lost-reply", mode="steer"),
            ended(2),
        ],
    },
    {"name": "cancel", "actions": [send("hold"), {"operation": "interrupt"}, ended(1)]},
    {"name": "send-now", "actions": [send("hold"), send("replacement", mode="send_now"), ended(2)]},
    {
        "name": "resume",
        "actions": [send("plain"), ended(1), {"operation": "kill"}, send("resume"), ended(2)],
    },
    {
        "name": "refused-load",
        "actions": [send("refuse-load"), ended(1), {"operation": "kill"}, send("resume")],
    },
    {"name": "native", "actions": [{"operation": "send", "text": "/native argument"}, ended(1)]},
    {"name": "missing-interpreter", "actions": [send("plain")]},
    {
        "name": "provider-error",
        "actions": [send("provider-error"), ended(1), send("rpc-error"), ended(2)],
    },
    {"name": "crash", "actions": [send("crash"), ended(1)]},
    {"name": "malformed", "actions": [send("malformed"), ended(1)]},
    {
        "name": "config-rebind",
        "actions": [
            send("plain"),
            ended(1),
            send(
                "image-after-config",
                image=True,
                effort="low",
                model="synthetic-other",
                fail_read=True,
            ),
            ended(2),
            send("model-rejected", model="synthetic-refused"),
        ],
    },
    *[
        {
            "name": name,
            "actions": [
                send("plain"),
                ended(1),
                {"operation": "sweep"},
                send("released-after-maintenance"),
                {"operation": "finish", "text": text},
                ended(3),
                {"operation": "sweep", "seconds": 300},
            ],
        }
        for name, text in (
            (
                "compact-confirmed",
                "Context compressed: 40 -> 12 messages\n~12,345 -> ~4,321 tokens",
            ),
            ("compact-unconfirmed", "Compression failed: provider unavailable"),
        )
    ],
]


async def python_exercise(spec: dict[str, Any]) -> dict[str, Any]:
    parent_environment_before = dict(os.environ)
    now = 100.0
    serial = 0
    fail_reads = 0
    failed_reads = 0
    files = ConversationMessageFiles(spec["database"])
    original_read = files.read

    async def read(identity: str, file: str) -> bytes:
        nonlocal fail_reads, failed_reads
        if fail_reads:
            fail_reads -= 1
            failed_reads += 1
            raise MessageFileMissing(file)
        return await original_read(identity, file)

    backend_events: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    base = HermesAcpBackendChildFactory(
        AcpChildLaunch(
            argv=(spec["node"], spec["executable"]),
            environment_overrides=(
                ("UNIT16_TRACE", spec["trace"]),
                ("UNIT16_STATE", spec["state"]),
                ("UNIT16_CONTROL", spec["control"]),
            ),
        )
    )

    def factory(**options: Any) -> Any:
        downstream = options["event_sink"]

        class Sink:
            async def report(self, turn: Any, event: Any) -> None:
                value = dataclasses.asdict(event)
                backend_events.append(
                    {
                        "turn": dataclasses.asdict(turn) if turn else None,
                        "event": type(event).__name__,
                        "value": value,
                    }
                )
                if type(event).__name__ == "AgentMessageCompleted":
                    for part in value["content"]:
                        if "stored_file_id" in part:
                            contents = await original_read(
                                options["resolved_start"].conversation_id, part["stored_file_id"]
                            )
                            kept.append(
                                {
                                    "stored_file_id": part["stored_file_id"],
                                    "media_type": part["media_type"],
                                    "sha256": hashlib.sha256(contents).hexdigest(),
                                    "size": len(contents),
                                }
                            )
                await downstream.report(turn, event)

        selected = base
        if options["resolved_start"].conversation_id == "unit16-missing-interpreter":
            selected = HermesAcpBackendChildFactory(
                AcpChildLaunch(argv=(str(Path(spec["workspace"]) / "missing-hermes-python"),))
            )
        return selected(**{**options, "event_sink": Sink()})

    tail = ConversationLiveTail()
    core = SqliteProcessConversationSystem(
        store=ConversationStore(spec["database"], integer_now=lambda: int(now)),
        backend_child_factories={key: factory for key in ConversationBackendKey},
        message_files=files,
        unix_time_now=lambda: now,
        live_tail=tail,
    )
    checkpoints = []

    async def wait(predicate: Any, reason: str) -> None:
        deadline = time.monotonic() + 10
        while not await predicate():
            if time.monotonic() > deadline:
                raise AssertionError("Timed out: " + reason)
            await asyncio.sleep(0.005)
        await core.wait_until_quiescent()

    with patch.object(files, "read", read):
        try:
            for item in spec["cases"]:
                # Keep prior conversations outside the maintenance lookback.
                now += 10000
                started = time.perf_counter()
                identity = "unit16-" + item["name"]
                event_start, file_start = len(backend_events), len(kept)
                subscription = tail.subscribe(identity)
                fates = []
                await core.start_conversation(
                    ConversationStartRequest(
                        conversation_id=identity,
                        backend_key=ConversationBackendKey.hermes,
                        model="synthetic-model",
                        workspace_folder=Path(spec["workspace"]),
                        workspace_path_policy=WorkspacePathPolicy.absolute,
                        role_materials=ConversationRoleMaterials(
                            "Synthetic owner",
                            (
                                ("PLAN_ACTOR", "owner"),
                                ("PLAN_CONVERSATION_ID", identity),
                                ("UNIT16_CASE", item["name"]),
                            ),
                        ),
                    )
                )
                sender = 0
                last_ask: str | None = None
                for action in item["actions"]:
                    operation = action["operation"]
                    if operation == "send":
                        sender += 1
                        content = text_message_content(action["text"])
                        if action.get("image"):
                            image = await files.keep(
                                identity,
                                b"synthetic-image",
                                media_type="image/png",
                                sender_message_id="seed-image",
                                piece_index=0,
                            )
                            content = (*content, MessageImage(image.stored_file_id, "image/png"))
                        if action.get("fail_read"):
                            fail_reads = 1
                        fates.append(
                            delivery_fate_json(
                                await core.send(
                                    identity,
                                    content,
                                    sender_label="Owner",
                                    sender_message_id=f"{item['name']}-sender-{sender}",
                                    mode=PromptDeliveryMode(action.get("mode", "queue")),
                                    reasoning_effort_change=action.get("effort"),
                                    model_change=action.get("model"),
                                )
                            )
                        )
                    elif operation == "ended":

                        async def complete(
                            identity: str = identity, count: int = action["count"]
                        ) -> bool:
                            rows = snapshot(spec["database"])["conversation_events"]
                            return sum(
                                row["conversation_id"] == identity and row["kind"] == "turn_ended"
                                for row in rows
                            ) >= count and not await core.is_running(identity)

                        await wait(complete, identity + " ended " + str(action["count"]))
                    elif operation == "answer":
                        await wait(
                            lambda identity=identity: core.has_pending_permission_ask(identity),
                            identity + " permission",
                        )
                        ask = next(iter(await core.pending_permission_ask_ids(identity)))
                        fates.append(
                            {
                                "answer_fate": str(
                                    await core.answer_permission_ask(identity, ask, "allow")
                                )
                            }
                        )
                    elif operation == "remember_ask":
                        await wait(
                            lambda identity=identity: core.has_pending_permission_ask(identity),
                            identity + " unanswered permission",
                        )
                        last_ask = next(iter(await core.pending_permission_ask_ids(identity)))
                    elif operation == "late_answer":
                        assert last_ask is not None
                        fates.append(
                            {
                                "late_answer_fate": str(
                                    await core.answer_permission_ask(identity, last_ask, "allow")
                                )
                            }
                        )
                    elif operation == "interrupt":
                        await core.interrupt(identity)
                    elif operation == "kill":
                        await core.kill(identity)
                    elif operation == "sweep":
                        now += action.get("seconds", 3000)
                        await core._sweep_idle_children()
                    elif operation == "finish":
                        serial += 1
                        temporary = Path(spec["control"] + ".new")
                        temporary.write_text(
                            json.dumps(
                                {
                                    "serial": serial,
                                    "scenario": item["name"],
                                    "command": "finish",
                                    "text": action["text"],
                                }
                            )
                        )
                        temporary.replace(spec["control"])
                await core.wait_until_quiescent()
                subscription.close()
                frames = []
                async for frame in subscription:
                    if isinstance(
                        frame, (AgentMessageDeltaFrame, ModelThinkingFrame, ToolCallProgressFrame)
                    ):
                        frames.append(
                            {
                                "conversation_id": identity,
                                "frame": type(frame).__name__,
                                "value": dataclasses.asdict(frame),
                            }
                        )
                rows = {
                    table: [row for row in values if row["conversation_id"] == identity]
                    for table, values in snapshot(spec["database"]).items()
                }
                checkpoints.append(
                    {
                        "name": item["name"],
                        "fates": fates,
                        "rows": rows,
                        "backend_events": backend_events[event_start:],
                        "frames": frames,
                        "kept": kept[file_start:],
                        "running": await core.is_running(identity),
                        "seconds": time.perf_counter() - started,
                    }
                )
                await core.kill(identity)
        finally:
            await core.shutdown()
    return {
        "parent_environment_unchanged": dict(os.environ) == parent_environment_before,
        "checkpoints": checkpoints,
        "failed_reads": failed_reads,
        "trace": [json.loads(line) for line in Path(spec["trace"]).read_text().splitlines()],
    }


def compared(result: dict[str, Any]) -> dict[str, Any]:
    checkpoints = []
    for item in result["checkpoints"]:
        # Python's SDK admits callbacks concurrently. Their cross-type order is
        # diagnostic only. Exact per-type order, count, turn, and payload remain.
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in item["backend_events"]:
            grouped.setdefault(event["event"], []).append(
                {"turn": event["turn"], "value": event["value"]}
            )
        checkpoints.append(
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key not in {"seconds", "backend_events"}
                },
                "backend_events_by_type": grouped,
            }
        )
    # SDK request IDs are private correlation identities, matched by request order.
    requests = []
    next_id: dict[str, int] = {}
    for row in result["trace"]:
        if row["kind"] != "in":
            continue
        message = dict(row["message"])
        if "method" in message and "id" in message:
            index = next_id.get(row["scenario"], 0)
            next_id[row["scenario"]] = index + 1
            message["id"] = f"request-{index}"
        requests.append({"scenario": row["scenario"], "message": message})
    value = normalize(
        {
            "checkpoints": checkpoints,
            "requests": requests,
            "failed_reads": result["failed_reads"],
            "parent_environment_unchanged": result["parent_environment_unchanged"],
        }
    )
    for checkpoint in value["checkpoints"]:
        if checkpoint["name"] != "missing-interpreter":
            continue
        for row in checkpoint["rows"]["conversation_events"]:
            if row["kind"] == "prompt_delivery_refused":
                payload = row["payload"]
                assert payload["refusal_reason"] == "backend_did_not_start"
                assert isinstance(payload["refusal_detail"], str)
                assert "missing-hermes-python" in payload["refusal_detail"]
                payload["refusal_detail"] = "<missing interpreter>"
        for row in checkpoint["rows"]["conversation_view_rows"]:
            if row["row_kind"] == "prompt_refused":
                summary = json.loads(row["rest_summary"])
                assert summary["refusal_reason"] == "backend_did_not_start"
                excerpt = summary["refusal_detail_excerpt"]
                assert isinstance(excerpt, str) and excerpt
                summary["refusal_detail_excerpt"] = "<missing interpreter>"
                row["rest_summary"] = json.dumps(summary, sort_keys=True)
    files: dict[str, str] = {}

    def walk(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {name: walk(child, name) for name, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(child, key) for child in item]
        if (
            key == "stored_file_id"
            and isinstance(item, str)
            and re.fullmatch(r"f_[a-f0-9]{32}", item)
        ):
            return files.setdefault(item, "received-file-" + str(len(files) + 1))
        return item

    compared_value = cast(dict[str, Any], walk(value))
    # Complete actual launch values bypass every identity normalization above.
    compared_value["launches"] = [row for row in result["trace"] if row["kind"] == "launch"]
    return compared_value


def test_unit16_adapter_real_core_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Protect adapter semantics through actual ACP bytes and both durable cores."""
    started = time.perf_counter()
    selected = {**environment(tmp_path), "PLAN_SERVER_URL": "http://127.0.0.1:0"}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    py_db, ts_db = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    node = subprocess.check_output(
        ["node", "-p", "process.execPath"], env=selected, text=True
    ).strip()
    spec: dict[str, Any] = {
        "database": str(py_db),
        "workspace": str(workspace),
        "node": node,
        "executable": str(ROOT / "server/conversation/testing/hermes/scripted_agent.ts"),
        "trace": str(tmp_path / "trace.jsonl"),
        "state": str(tmp_path / "state.json"),
        "control": str(tmp_path / "control.json"),
        "cases": CASES,
    }
    with patch.dict(os.environ, selected, clear=True):
        connection = connect(str(py_db))
        try:
            create_schema(connection)
            connection.commit()
            with sqlite3.connect(ts_db) as target:
                connection.backup(target)
        finally:
            connection.close()
        python = asyncio.run(asyncio.wait_for(python_exercise(spec), 150))
    for key in ("trace", "state", "control"):
        Path(spec[key]).unlink(missing_ok=True)
    spec["database"] = str(ts_db)
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "import {pathToFileURL} from 'node:url'; "
            "const {run_part2_parity}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await run_part2_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/hermes/part2_parity.ts"),
        ],
        cwd=ROOT,
        env=selected,
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    typescript = json.loads(completed.stdout)
    left, right = compared(python), compared(typescript)
    report = {
        "python": python,
        "typescript": typescript,
        "compared_python": left,
        "compared_typescript": right,
        "seconds": time.perf_counter() - started,
        "normalizations": [
            "SDK request IDs by scenario request order",
            "known core control UUIDs through existing Unit18 normalization",
            "random received stored-file IDs only, with exact file hashes and sizes",
            "missing-interpreter refusal detail and its projected excerpt use native diagnostics",
            "backend callbacks grouped by type, preserving per-type order/count/turn/payload. "
            "Raw cross-type callback order stays diagnostic and is not parity-proven. "
            "Persisted core events, live frames, and ACP requests retain exact order.",
        ],
        "boundary": (
            "Real ACP child and adapter behind both real cores. Config rollback injects "
            "one kept-image read failure after effort changes, before prompt bytes. "
            "No HTTP host or provider."
        ),
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit16-adapter.json").write_text(json.dumps(report, indent=2) + "\n")
    if left != right:
        differing = [
            expected["name"]
            for expected, actual in zip(left["checkpoints"], right["checkpoints"], strict=True)
            if expected != actual
        ]
        pytest.fail(
            f"Hermes parity differs in checkpoints {differing}. "
            f"ACP request traces equal: {left['requests'] == right['requests']}. "
            "See unit16-adapter.json for full compared values.",
            pytrace=False,
        )
    cases = {item["name"]: item for item in left["checkpoints"]}
    # These anchors prove fixture activation or safety properties that equality
    # alone cannot prove. Python supplies all remaining expected core behavior.
    assert list(cases) == [item["name"] for item in CASES]
    malformed = cases["malformed"]["backend_events_by_type"]
    assert "PlanUpdated" not in malformed and "ToolCallProgress" not in malformed
    assert len(malformed["ComposerCatalogReported"]) == 1
    assert [
        entry["display_text"]
        for entry in malformed["ComposerCatalogReported"][0]["value"]["composer_catalog"]
    ] == ["/compress", "/native"]
    assert cases["permission"]["fates"][-1] == {"late_answer_fate": "refused"}
    assert [
        row["payload"]["ending"]
        for row in cases["provider-error"]["rows"]["conversation_events"]
        if row["kind"] == "turn_ended"
    ] == ["completed", "failed"]
    model_writes = [
        row["message"]["params"]["modelId"]
        for row in left["requests"]
        if row["scenario"] == "config-rebind"
        and row["message"].get("method") == "session/set_model"
    ]
    assert model_writes == [
        "synthetic-other",
        "synthetic-model",
        "synthetic-other",
        "synthetic-refused",
    ]
    assert (
        sum(
            row["kind"] == "prompt" and row["scenario"] == "config-rebind"
            for row in python["trace"]
        )
        == 2
    )
    assert sum(row["kind"] == "lost-steer-reply" for row in python["trace"]) == 1
    assert (
        sum(
            row["kind"] == "prompt" and row["scenario"] == "steer-uncertain"
            for row in python["trace"]
        )
        == 2
    )
    for name, count in (("compact-confirmed", 1), ("compact-unconfirmed", 0)):
        assert len(cases[name]["backend_events_by_type"].get("ContextCompacted", [])) == count
        assert (
            sum(row["kind"] == "prompt" and row["scenario"] == name for row in python["trace"]) == 3
        )
    assert python["failed_reads"] == typescript["failed_reads"] == 1
    assert (
        python["parent_environment_unchanged"] is typescript["parent_environment_unchanged"] is True
    )
    assert "PRIVATE_THOUGHT_MUST_NEVER_ESCAPE" not in json.dumps(left["checkpoints"])
    assert "REPLAY_MUST_NOT_APPEAR" not in json.dumps(left["checkpoints"])
    assert all(not item["running"] for item in left["checkpoints"])
    assert not any(
        row["kind"] == "fixture-error" for result in (python, typescript) for row in result["trace"]
    )
