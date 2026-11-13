"""Remote-child consumers retain custody without a connected peer or full executor.

The existing wire/mailbox checks do not prove adapter admission or outcome mapping.
One exchange compares native command bytes, delayed withdrawal, restored Claude
configuration, steer certainty/error consumption, and durable answer identities.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import UserInputAnswer
from planner.conversation.message_content import text_message_content
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.vps_remote_child import LateSteer, MacRemoteChild

ROOT = Path(__file__).resolve().parents[3]


def test_remote_child_consumers(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    homes = create_synthetic_homes(tmp_path)
    start = ResolvedConversationStart(
        conversation_id="conversation",
        backend_key=ConversationBackendKey.claude,
        model="claude-old",
        reasoning_effort="low",
        role_materials=None,
        workspace_folder=Path("/nested/work"),
        access=ConversationAccess.full,
        workspace_path_policy=WorkspacePathPolicy.executor_default,
    )
    child = ChildReference("11111111-1111-4111-8111-111111111111", "child", "conversation")
    snapshot = wire.CanonicalSkillSnapshot(
        tuple(
            wire.CanonicalSkillSource(name, f"---\nname: {name}\ndescription: fixture\n---\n")
            for name in ("panels", "panels-worker")
        ),
        ("panels", "panels-worker"),
    )
    spec = {
        "start": wire.as_object(start),
        "child": wire.as_object(child),
        "snapshot": wire.as_object(snapshot),
    }

    async def reference() -> dict[str, Any]:
        sent: list[dict[str, Any]] = []
        results: list[Any] = []
        withdrawals: list[list[str]] = []
        ids: dict[str, str] = {}
        present, late, hold = True, False, False
        disposition = wire.CommandDisposition.succeeded
        result: dict[str, Any] = {"kind": "steer_accepted"}
        entered = asyncio.Event()
        release: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        turn = TurnToken(child.conversation_id, 3)

        def capture(command: wire.ChildCommand) -> None:
            if command.command_id not in ids:
                ids[command.command_id] = (
                    command.command_id
                    if command.command_id.startswith("answer-")
                    else f"minted-{len(ids)}"
                )
            sent.append({**wire.as_object(command), "command_id": ids[command.command_id]})

        class Commands:
            async def put(self, command: wire.ChildCommand) -> bool:
                capture(command)
                if hold:
                    entered.set()
                    return await release
                return True

            def mac_is_present(self) -> bool:
                return present

            async def deliver_steer(self, command: wire.ChildCommand) -> Any:
                capture(command)
                if late:
                    verdict: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                    verdict.set_result(True)
                    return LateSteer(verdict)
                return wire.CommandOutcome(command.command_id, child, disposition, result)

            async def stop(self, command: wire.ChildCommand) -> wire.CommandOutcome:
                capture(command)
                return wire.CommandOutcome(command.command_id, child, disposition, result)

            async def save_answer(self, command: wire.ChildCommand) -> bool:
                capture(command)
                return True

            async def cancel_initial_delivery(
                self, reference: ChildReference, command_ids: tuple[str, ...]
            ) -> bool:
                assert reference == child
                withdrawals.append([ids[value] for value in command_ids])
                return False

            def retire_child(self, reference: ChildReference) -> None:
                results.append({"detached": wire.as_object(reference)})

        async def observe(work: Any) -> None:
            try:
                value = await work()
                if isinstance(value, str):
                    results.append(value)
                elif value is None or isinstance(value, bool):
                    results.append(value)
                else:
                    from dataclasses import fields

                    entry = {field.name: getattr(value, field.name) for field in fields(value)}
                    if isinstance(entry.get("not_delivered"), asyncio.Future):
                        entry["not_delivered"] = await entry["not_delivered"]
                    results.append({"type": type(value).__name__, **entry})
            except Exception as error:
                results.append(
                    {
                        "error": type(error).__name__,
                        "message": "<command>"
                        if type(error).__name__ == "RemoteCommandUncertain"
                        else str(error),
                    }
                )

        commands = Commands()
        proxy = MacRemoteChild(child, commands, lambda _: snapshot)
        await observe(lambda: proxy.start(start, vendor_session_cursor="session"))

        def prompt() -> Any:
            return proxy.write_prompt(
                turn,
                text_message_content("composed"),
                sender_label="owner",
                sender_content=text_message_content("sender"),
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )

        await observe(prompt)
        adopted = MacRemoteChild(
            child, commands, lambda _: snapshot, started_on=start, initial_history_complete=False
        )
        await observe(
            lambda: adopted.write_prompt(
                turn,
                text_message_content("other"),
                sender_label="owner",
                sender_content=text_message_content("other"),
                mode=PromptDeliveryMode.queue,
                model_change="different",
                reasoning_effort_change=None,
            )
        )

        def steer() -> Any:
            return proxy.steer(turn, text_message_content("steer"), sender_label="owner")

        present = False
        await observe(steer)
        present, late = True, True
        await observe(steer)
        late, disposition, result = (
            False,
            wire.CommandDisposition.uncertain,
            {"composed_content_delivered": False},
        )
        await observe(steer)
        disposition, result = (
            wire.CommandDisposition.succeeded,
            {"kind": "steer_refused", "refusal_reason": "backend_rejected_steer"},
        )
        await observe(steer)
        result = {"kind": "steer_accepted", "composed_content_delivered": {}}
        await observe(steer)
        result = {}
        await observe(steer)
        disposition, result = (
            wire.CommandDisposition.refused,
            {"error_type": "NeedsRebind", "error_message": "fresh child"},
        )
        await observe(steer)
        await observe(lambda: proxy.answer_permission_ask("ask", "allow"))
        await observe(
            lambda: proxy.answer_permission_ask(
                "ask", "allow", answer_id="answer-permission", turn_token=turn
            )
        )
        await observe(
            lambda: proxy.answer_user_input(
                "request",
                (UserInputAnswer("q", ("yes",)),),
                answer_id="answer-input",
                turn_token=turn,
            )
        )
        disposition = wire.CommandDisposition.uncertain
        await observe(proxy.stop)
        hold = True
        writing = asyncio.create_task(prompt())
        await entered.wait()
        withdrawing = asyncio.create_task(proxy.withdraw_delivery())
        await asyncio.sleep(0)
        release.set_result(True)
        await observe(lambda: writing)
        await observe(lambda: withdrawing)
        await observe(prompt)
        await observe(adopted.withdraw_delivery)
        await observe(proxy.detach)
        return {"sent": sent, "results": results, "withdrawals": withdrawals}

    began = time.perf_counter()
    with (
        synthetic_home_environment(homes),
        patch("planner.executor.vps_remote_child.now_ms", return_value=1700000000000),
    ):
        expected = asyncio.run(asyncio.wait_for(reference(), 10))
    environment = {
        **homes,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/remote_child.ts")],
        input=json.dumps(spec),
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    assert actual == expected
    assert actual["results"][4]["not_delivered"] is True
    assert {"type": "BackendSteerAccepted", "composed_content_delivered": False} in actual[
        "results"
    ]
    assert actual["results"][-5] is True
    report = request.config.getoption("--compare-report-dir", default=None)
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit22-remote-child.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - began,
                    "commands": len(actual["sent"]),
                    "expected": expected,
                    "actual": actual,
                },
                indent=2,
            )
        )
