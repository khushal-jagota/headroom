"""Fresh Mac command effects and durable settlement through the real local stores."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.compare.direct.mac_executor_fixture import report_direct, run_node, start_inputs
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.backends.contracts import (
    BackendPromptAccepted,
    BackendPromptRejected,
    BackendSteerAccepted,
    BackendSteerRefused,
    BackendSteerUncertain,
    PromptWriteFailed,
)
from planner.conversation.contracts import (
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    WorkspacePathPolicy,
)
from planner.conversation.events import UserInputAnswer
from planner.conversation.message_content import MessageFile, MessageText
from planner.execution_identity import AskKind, AskReference, ChildReference, TurnToken
from planner.executor import mac_mailbox, mac_runner
from planner.executor.configuration import ExecutorPaths
from planner.executor.mac_mailbox import CommandJournal
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner, command_owes_outputs
from planner.executor.protocol import (
    AnswerPermission,
    AnswerUserInput,
    CancelTurn,
    ChildCommand,
    ChildCommandArguments,
    ChildOperation,
    CommandOutcome,
    CommandResult,
    DurableAnswer,
    MailboxCommand,
    MailboxPollReply,
    QueuedEnvelope,
    StartRun,
    Steer,
    StopRun,
    WritePrompt,
    as_object,
    encode_value,
    type_adapter,
)

NOW = 1_700_000_010_123
HIGH = 9_007_199_254_741_003


def fixture(root: Path) -> dict[str, Any]:
    child = ChildReference("inc", "child", "conversation")
    turn = TurnToken("conversation", HIGH)
    asks = [
        AskReference(child, turn, key, kind)
        for key, kind in (
            ("permission", AskKind.permission),
            ("question", AskKind.user_input),
        )
    ]
    commands: dict[str, ChildCommand] = {}

    def add(
        key: str,
        args: ChildCommandArguments,
        reference: ChildReference = child,
        token: TurnToken = turn,
    ) -> None:
        commands[key] = ChildCommand(
            key,
            reference,
            ChildOperation(args.kind),
            None if isinstance(args, (StartRun, CancelTurn, StopRun)) else token,
            args,
        )

    base = type_adapter(StartRun).validate_python(start_inputs(root)[0])
    resolved = replace(
        base.resolved_start,
        conversation_id="conversation",
        workspace_folder=root / "workspace",
        workspace_path_policy=WorkspacePathPolicy.absolute,
    )
    start = replace(base, resolved_start=resolved, workspace_home_relative=None)
    add("start", start)
    prompt = WritePrompt(
        content=(MessageText("accepted"),),
        sender_content=(MessageText("original"),),
        sender_label="Worker",
        mode=PromptDeliveryMode.send_now,
        sender_message_count=3,
        model_change="changed",
        reasoning_effort_is_set=True,
        automatic_compaction=True,
    )
    add("prompt", prompt)
    add(
        "reject",
        replace(prompt, content=(MessageText("reject"),)),
        token=replace(turn, turn_number=2),
    )
    for key in ("steer-accepted", "steer-refused", "steer-uncertain"):
        add(
            key,
            Steer(
                content=(MessageText(key),),
                sender_label="Sender",
                sender_content=(MessageText("sender"),),
            ),
        )
    add("zero-held", CancelTurn())
    permission_answer = DurableAnswer("permission", asks[0], "allow", NOW)
    permission = AnswerPermission(ask_id="permission", option_id="allow", answer=permission_answer)
    add("permission", permission)
    answers = (UserInputAnswer("q", ("yes",)),)
    add(
        "question",
        AnswerUserInput(
            request_id="question",
            answers=answers,
            answer=DurableAnswer("question", asks[1], None, NOW, answers),
        ),
    )
    add("stale", replace(permission, answer=replace(permission_answer, answer_id="stale")))
    add("withdraw", replace(prompt, content=(MessageFile("input", "text/plain", "input.txt", 4),)))
    add("prewithdraw", prompt)
    add("missing", prompt, replace(child, child_id="missing"))
    add("stop", StopRun())
    replay_child = replace(child, child_id="replay-child", conversation_id="replay-conversation")
    add(
        "replay-start",
        replace(
            start,
            resolved_start=replace(resolved, conversation_id="replay-conversation"),
        ),
        replay_child,
    )
    add(
        "replay",
        replace(prompt, content=(MessageText("replay"),)),
        replay_child,
        replace(turn, conversation_id="replay-conversation"),
    )

    # Every value crosses the fresh Python schema before either implementation sees it.
    def canonical(kind: Any, value: Any) -> str:
        adapter = type_adapter(kind)
        raw = adapter.dump_json(value)
        adapter.validate_json(raw)
        return raw.decode()

    return dict(
        commands={key: canonical(ChildCommand, value) for key, value in commands.items()},
        asks=[canonical(AskReference, ask) for ask in asks],
    )


async def reference(root: Path, data: dict[str, Any], patch: pytest.MonkeyPatch) -> dict[str, Any]:
    (root / "workspace").mkdir(parents=True)
    commands = {
        key: type_adapter(ChildCommand).validate_json(value)
        for key, value in data["commands"].items()
    }
    patch.setattr(mac_runner, "now_ms", lambda: NOW)
    patch.setattr(mac_mailbox, "now_ms", lambda: NOW)
    patch.setattr(time, "time_ns", lambda: NOW * 1_000_000)
    patch.setattr(
        mac_runner, "install_native_skill_snapshot", lambda *args, **kwargs: root / "skills"
    )
    calls: list[Any] = []
    trace: list[Any] = []
    outcomes: list[Any] = []
    states: list[Any] = []
    facts: dict[str, bool] = {}
    entered, release, input_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finishing, finish_release = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    zero_entered = asyncio.Event()
    box = MacOutbox(root / "spool")
    journal = CommandJournal(root / "spool", "inc", outputs_owed=command_owes_outputs)
    native: Any = None
    runner: MacRunner

    class Native:
        async def start(self, resolved: Any, **options: Any) -> None:
            calls.append(["start", resolved.model, options])

        async def write_prompt(self, turn: Any, content: Any, **options: Any) -> Any:
            calls.append(
                ["prompt", encode_value(turn), encode_value(content), encode_value(options)]
            )
            text = content[0].text
            if text.endswith("accepted"):
                entered.set()
                await release.wait()
            if text.endswith("reject"):
                raise BackendPromptRejected("not admitted 🦉" * 6000)
            if text.endswith("replay"):
                raise PromptWriteFailed("delivery fate unknown 🦉")
            return BackendPromptAccepted(False)

        async def steer(self, turn: Any, content: Any, **options: Any) -> Any:
            facts["native_turn_identity"] = (
                facts.get("native_turn_identity", True)
                and turn == runner._children["child"].active_turn
            )
            calls.append(
                ["steer", encode_value(turn), encode_value(content), encode_value(options)]
            )
            text = content[0].text
            if text.endswith("refused"):
                return BackendSteerRefused(
                    PromptDeliveryRefusalReason.no_running_turn_to_steer_into
                )
            if text.endswith("uncertain"):
                return BackendSteerUncertain(None)
            return BackendSteerAccepted(False)

        async def cancel_running_turn(self) -> None:
            calls.append(["cancel"])

        async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
            calls.append(["permission", ask_id, option_id])

        async def answer_user_input(self, request_id: str, answers: Any) -> None:
            calls.append(["question", request_id, encode_value(answers)])
            current = runner._children["child"].asks[request_id]
            runner._children["child"].asks[request_id] = replace(
                current, turn=replace(current.turn, turn_number=2)
            )

        async def stop(self) -> None:
            calls.append(["stop"])

    native = Native()

    class Inputs:
        async def download_inputs(self, command: Any) -> None:
            calls.append(["inputs", command.command_id])
            input_entered.set()
            await asyncio.Future()

    runner = MacRunner(
        incarnation="inc",
        outbox=box,
        journal=journal,
        files=cast(Any, Inputs()),
        factory=lambda resolved: lambda **options: native,
        message_files=cast(Any, None),
        paths=ExecutorPaths(root / "state"),
        workspace_home=root,
    )
    reply = MailboxPollReply(
        tuple(
            MailboxCommand(i, key, NOW, child_command=command)
            for i, (key, command) in enumerate(commands.items(), 1)
        )
    )
    journal.record_poll_reply(reply, "inc")
    original_finish, original_add, original_mark = (
        journal.finish,
        box.add,
        journal.mark_outputs_published,
    )
    faults = {"finish": True, "append": True, "mark": True, "replay": True}

    def finish(key: str, result: Any, *, outputs_owed: bool) -> None:
        trace.append(["finish", key, outputs_owed])
        if key == "zero-held":
            finishing.set()
            loop.call_soon_threadsafe(zero_entered.set)
            if not finish_release.wait(10):
                raise TimeoutError("zero-output finish gate was abandoned")
            if faults.pop("finish", False):
                raise OSError("scripted journal finish")
        original_finish(key, result, outputs_owed=outputs_owed)

    def add(entry: Any, **kwargs: Any) -> Any:
        trace.append(["append", entry.submission_id])
        if entry.submission_id == "turn_failed_" + hashlib.sha256(
            b"replay"
        ).hexdigest() and faults.pop("replay", False):
            raise OSError("scripted append before commit")
        value = original_add(entry, **kwargs)
        if entry.submission_id == "turn_failed_" + hashlib.sha256(
            b"reject"
        ).hexdigest() and faults.pop("append", False):
            raise OSError("scripted append after commit")
        return value

    def mark(key: str) -> None:
        trace.append(["mark", key])
        if key == "permission" and faults.pop("mark", False):
            raise OSError("scripted publication mark")
        original_mark(key)

    patch.setattr(journal, "finish", finish)
    patch.setattr(box, "add", add)
    patch.setattr(journal, "mark_outputs_published", mark)

    async def run(key: str, fail: bool = False) -> None:
        try:
            value = await runner.run(commands[key])
        except OSError as error:
            if not fail:
                raise
            outcomes.append([key, str(error)])
        else:
            assert not fail
            outcomes.append([key, as_object(value)])

    def state() -> None:
        child = runner._children["child"]
        states.append(
            dict(
                turn=encode_value(child.active_turn),
                model=child.model,
                effort=child.reasoning_effort,
                asks=sorted(child.asks),
            )
        )

    try:
        await run("start")
        runner._children["child"].asks.update(
            {
                ask.ask_id: ask
                for raw in data["asks"]
                if (ask := type_adapter(AskReference).validate_json(raw))
            }
        )
        first = asyncio.create_task(runner.run(commands["prompt"]))
        await entered.wait()
        reordered = type_adapter(ChildCommand).validate_json(
            json.dumps(json.loads(data["commands"]["prompt"]), sort_keys=True)
        )
        second = asyncio.create_task(runner.run(reordered))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        facts["invoked_withdraw_refused"] = not runner.withdraw("prompt")
        release.set()
        outcomes.append(["prompt", as_object(await second)])
        changed = replace(
            commands["prompt"],
            arguments=replace(cast(Any, commands["prompt"].arguments), sender_label="changed"),
        )
        with pytest.raises(ValueError) as reuse:
            await runner.run(changed)
        facts["changed_content_rejected"] = (
            str(reuse.value) == "command id reused with different content"
        )
        state()
        for key in ("reject", "permission"):
            await run(key, True)
            await run(key)
            state()
        for key in (
            "steer-accepted",
            "steer-refused",
            "steer-uncertain",
            "question",
            "stale",
        ):
            await run(key)
        state()
        held = asyncio.create_task(run("zero-held", True))
        await zero_entered.wait()
        record = runner._commands["zero-held"]
        facts["zero_output_actual_retained"] = (
            finishing.is_set()
            and record.outcome is not None
            and not record.outputs
            and record.task is not None
            and not record.task.done()
        )
        finish_release.set()
        await held
        await run("zero-held")
        pending = asyncio.create_task(runner.run(commands["withdraw"]))
        await input_entered.wait()
        facts["preparation_withdrawn"] = runner.withdraw("withdraw")
        outcomes.append(["withdraw", as_object(await pending)])
        facts["prewithdrawn"] = runner.withdraw("prewithdraw")
        await run("prewithdraw")
        await run("missing")
        await run("stop")
        await run("replay-start")
        await run("replay", True)
        facts["all_faults_observed"] = not faults
        before = [c.command_id for c, _ in journal.unpublished()]
        journal.close()
        box.close()
        # A fresh runner recovers only persisted obligations; no native child is recreated.
        box = MacOutbox(root / "spool")
        journal = CommandJournal(root / "spool", "inc", outputs_owed=command_owes_outputs)
        resumed = MacRunner(
            incarnation="inc",
            outbox=box,
            journal=journal,
            factory=lambda resolved: (_ for _ in ()).throw(
                AssertionError("replay started a provider")
            ),
            message_files=cast(Any, None),
            paths=ExecutorPaths(root / "state"),
        )
        call_count = len(calls)
        for command, result in journal.unpublished():
            await resumed.publish_owed_outputs(command, result)
            await resumed.publish_owed_outputs(command, result)
        facts["replay_without_native"] = len(calls) == call_count and not journal.unpublished()
        pending_rows = [
            dict(
                submission_id=row.submission_id,
                sequence=str(row.sequence),
                conversation_id=row.conversation_id,
                kind=row.kind.value,
                payload=row.payload,
            )
            for row in box.pending()
        ]
        results = [
            [key, as_object(stored_result)]
            for key in commands
            if (stored_result := journal.result_of(key)) is not None
        ]
        return dict(
            calls=calls,
            trace=[row for index, row in enumerate(trace) if row not in trace[:index]],
            retry_attempts=trace,
            outcomes=outcomes,
            states=states,
            facts=facts,
            unpublished_before_reopen=before,
            pending=pending_rows,
            results=results,
        )
    finally:
        finish_release.set()
        release.set()
        journal.close()
        box.close()


def test_unit22_mac_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), monkeypatch.context() as patch:
        expected = asyncio.run(
            asyncio.wait_for(reference(tmp_path / "py", fixture(tmp_path / "py"), patch), 25)
        )
    python_attempts = expected.pop("retry_attempts")
    target = tmp_path / "ts"
    target.mkdir()
    (target / "input.json").write_text(json.dumps(fixture(target)))
    observed = run_node("server/executor/mac/testing/commands_parity.ts", target, homes, timeout=35)
    (target / "expected.json").write_text(json.dumps(expected, indent=2))
    (target / "observed.json").write_text(json.dumps(observed, indent=2))
    for _, result in observed["parity"]["results"]:
        type_adapter(CommandResult).validate_python(result)
    for _, outcome in observed["parity"]["outcomes"]:
        if isinstance(outcome, dict):
            type_adapter(CommandOutcome).validate_python(outcome)
    for row in observed["parity"]["pending"]:
        type_adapter(QueuedEnvelope).validate_python(
            {
                **row,
                "sequence": int(row["sequence"]),
                "sender_id": "synthetic",
                "file_dependencies": [],
            }
        )
    assert observed["parity"] == expected
    assert expected["facts"] and all(expected["facts"].values())
    assert observed["actual_work_joined"]
    report_direct(
        request,
        "unit-22-mac-commands.json",
        observed,
        started,
        "Command effects and retry/reopen settlement through real journal/spool; "
        "real native/callback custody with scripted skills/files/backend/exact-stop ports; "
        "no provider or network",
        python_retry_attempts=python_attempts,
    )
