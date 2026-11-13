"""The three deferred steer behaviors through the existing scripted core fixture.

One ordered exchange proves queue position, delayed non-arrival, and restart replay.
No provider, listener, browser host, or new transport is involved.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot
from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry

from planner.conversation.api import delivery_fate_json
from planner.conversation.backends.contracts import BackendSteerUncertain
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    HeldPromptPromotionMode,
    PromptDeliveryMode,
)
from planner.conversation.events import PromptDeliveryUncertainEventPayload
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import message_content_text, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]


async def source(path: Path) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    registry: ScriptedBackendRegistry
    store: ConversationStore
    core: SqliteProcessConversationSystem

    def cycle() -> None:
        nonlocal registry, store, core
        registry = ScriptedBackendRegistry()
        store = ConversationStore(str(path), integer_now=lambda: 1000)
        core = SqliteProcessConversationSystem(
            store=store,
            backend_child_factories={key: registry.make_child for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(str(path)),
            live_tail=ConversationLiveTail(),
            unix_time_now=lambda: 1000,
            monotonic_now=lambda: 1000,
        )

    async def start(identity: str) -> None:
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id=identity,
                backend_key=ConversationBackendKey.codex,
                model="model",
                workspace_folder=Path("/tmp"),
            )
        )

    async def send(identity: str, text: str, steer: bool = False, anonymous: bool = False) -> Any:
        return delivery_fate_json(
            await core.send(
                identity,
                text_message_content(text),
                sender_label="owner",
                sender_message_id=None if anonymous else f"{identity}-{text}",
                mode=PromptDeliveryMode.steer if steer else PromptDeliveryMode.queue,
            )
        )

    async def finish(identity: str) -> None:
        await registry.current[identity].finish()
        await core.wait_until_quiescent()

    async def capture(name: str, identity: str, fates: list[Any] | None = None) -> None:
        held = await core.held_prompts(identity)
        trace.append(
            {
                "name": name,
                "fates": fates or [],
                "held": [
                    {"text": message_content_text(item.content), "reason": item.queue_reason.value}
                    for item in held
                ],
                "rows": snapshot(str(path)),
            }
        )

    async def close() -> None:
        await core.shutdown()
        await registry.close()

    async def restart_case(identity: str, anonymous: bool = False, held: bool = False) -> None:
        await start(identity)
        await send(identity, "incumbent")
        never: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        async def abandoned(*args: Any, **kwargs: Any) -> BackendSteerUncertain:
            return BackendSteerUncertain(None, not_delivered=never)

        registry.current[identity].steer = abandoned  # type: ignore[method-assign]
        await send(identity, "late steer", True, anonymous)
        await finish(identity)
        sequence = next(
            row.sequence
            for row in await store.read_events_after(identity, 0)
            if isinstance(row.payload, PromptDeliveryUncertainEventPayload)
        )
        await capture(f"{identity}-before-restart", identity)
        await close()
        cycle()
        arrived: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        arrived.set_result(True)
        if held:
            await send(identity, "fresh incumbent")
        await core.await_late_steer(identity, sequence, arrived)
        await asyncio.gather(*core._conversations[identity].late_steers)
        await core.wait_until_quiescent()
        if held:
            await core.await_late_steer(identity, sequence, arrived)
            await asyncio.gather(*core._conversations[identity].late_steers)
            await capture(f"{identity}-held", identity)
            await close()
            cycle()
            await core.await_late_steer(identity, sequence, arrived)
            await asyncio.gather(*core._conversations[identity].late_steers)
            await core.wait_until_quiescent()
        await capture(f"{identity}-once", identity)
        await close()
        cycle()
        await core.await_late_steer(identity, sequence, arrived)
        await core.wait_until_quiescent()
        await capture(f"{identity}-no-repeat", identity)

    cycle()
    try:
        await start("refused")
        await send("refused", "incumbent")
        await registry.configure(
            "refused",
            "steer",
            {"result": {"fate": "refused", "refusal_reason": "remote_steer_not_delivered"}},
        )
        fate = await send("refused", "late steer", True)
        await capture("refused-queued", "refused", [fate])
        await finish("refused")
        await capture("refused-next", "refused")
        await finish("refused")
        await start("promoted")
        await send("promoted", "incumbent")
        await send("promoted", "late steer")
        selected = (await core.held_prompts("promoted"))[0]
        await registry.configure(
            "promoted",
            "steer",
            {"result": {"fate": "refused", "refusal_reason": "remote_steer_not_delivered"}},
        )
        fate = await core.promote_held_prompt(
            "promoted", selected.held_prompt_id, HeldPromptPromotionMode.steer
        )
        await capture("promoted-queued", "promoted", [delivery_fate_json(fate)])
        await finish("promoted")
        await capture("promoted-next", "promoted")
        await finish("promoted")
        await start("late")
        await send("late", "incumbent")
        verdict: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        async def uncertain(*args: Any, **kwargs: Any) -> BackendSteerUncertain:
            return BackendSteerUncertain(None, not_delivered=verdict)

        registry.current["late"].steer = uncertain  # type: ignore[method-assign]
        fate = await send("late", "late steer", True)
        await send("late", "next")
        await finish("late")
        await capture("awaiting-non-arrival", "late", [fate])
        verdict.set_result(True)
        await asyncio.gather(*core._conversations["late"].late_steers)
        await core.wait_until_quiescent()
        await capture("late-first", "late")
        await finish("late")
        await start("arrived")
        await send("arrived", "incumbent")
        arrived_verdict: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        async def delivered(*args: Any, **kwargs: Any) -> BackendSteerUncertain:
            return BackendSteerUncertain(None, not_delivered=arrived_verdict)

        registry.current["arrived"].steer = delivered  # type: ignore[method-assign]
        await send("arrived", "already arrived", True)
        await send("arrived", "next")
        await finish("arrived")
        arrived_verdict.set_result(False)
        await asyncio.gather(*core._conversations["arrived"].late_steers)
        await core.wait_until_quiescent()
        await capture("arrived-no-repeat", "arrived")
        await finish("arrived")
        await restart_case("restart")
        await restart_case("restart-null", anonymous=True)
        await restart_case("restart-held", held=True)
    finally:
        await close()
    return trace


def test_deferred_steers_match_fresh_python(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    began = time.perf_counter()
    selected = environment(tmp_path)
    selected["PLAN_TEST_MODE"] = "1"
    py, ts = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    with patch.dict(os.environ, selected, clear=True):
        connection = connect(str(py))
        try:
            create_schema(connection)
            connection.commit()
        finally:
            connection.close()
        with sqlite3.connect(py) as left, sqlite3.connect(ts) as right:
            left.backup(right)
        expected = asyncio.run(asyncio.wait_for(source(py), 30))
    completed = subprocess.run(
        ["node", str(ROOT / "server/conversation/testing/unit22_steers.ts")],
        input=json.dumps({"path": str(ts)}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=selected,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    expected, actual = normalize(expected), normalize(actual)
    destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "unit22-steers.json").write_text(
        json.dumps(
            {"seconds": time.perf_counter() - began, "source": expected, "candidate": actual},
            indent=2,
        )
        + "\n"
    )
    assert actual == expected
    final = actual[-1]["rows"]["conversation_events"]
    for identity, texts in {
        "restart": ["incumbent", "late steer"],
        "restart-null": ["incumbent", "late steer"],
        "restart-held": ["incumbent", "fresh incumbent", "late steer"],
    }.items():
        assert [
            row["payload"]["text"]
            for row in final
            if row["conversation_id"] == identity and row["kind"] == "prompt"
        ] == texts
    queued = next(row for row in actual if row["name"] == "restart-held-held")
    assert queued["held"] == [{"text": "late steer", "reason": "steer_refused"}]
