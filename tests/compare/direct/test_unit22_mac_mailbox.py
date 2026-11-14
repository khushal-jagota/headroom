"""One fresh Python/scripted proof for mailbox order, reporting and admitted shutdown."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from tests.compare.direct.test_unit22_mac_journal import Oracle, _command, _digest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.executor import mac_mailbox
from planner.executor.configuration import OUTBOX_RETRY_INITIAL_SECONDS
from planner.executor.contracts import MacHttpClient
from planner.executor.mac_catalog import MacCatalog
from planner.executor.mac_mailbox import (
    CommandJournal,
    MacCommandQueues,
    MacPollLoop,
    MacResultReporter,
)
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReply,
    CommandOutcomeReport,
    CommandResult,
    MailboxCommand,
    MailboxPollReply,
    ModelListRequest,
    RejectedCommandResult,
    type_adapter,
)

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_700_000_010_123


def encoded(kind: Any, value: Any) -> bytes:
    return type_adapter(kind).dump_json(value)


def command(command_id: str, sequence: int, child: str = "a", stop: bool = False) -> MailboxCommand:
    c = _command(command_id, sequence, "stop" if stop else "write_prompt")
    assert c.child_command is not None
    reference = replace(c.child_command.child, executor_incarnation="inc", child_id=child)
    return replace(c, child_command=replace(c.child_command, child=reference))


def performance_spec(journal: CommandJournal) -> dict[str, Any]:
    """Reuse the same populated journal for canonical expansion and report boundaries."""
    raw = json.loads(type_adapter(MailboxCommand).dump_json(command("performance", 15)))
    raw.pop("model_list_request")
    raw["child_command"]["arguments"].pop("kind")
    text_bytes = (
        4 * 1024 * 1024
        - len(json.dumps({"commands": [raw]}, separators=(",", ":")).encode())
        + len("hello")
    )
    raw["child_command"]["arguments"]["content"][0]["text"] = "x" * text_bytes
    poll = json.dumps({"commands": [raw]}, separators=(",", ":")).encode()
    decoded = type_adapter(MailboxPollReply).validate_json(poll)
    canonical = type_adapter(MailboxCommand).dump_json(decoded.commands[0])
    journal.record_poll_reply(decoded, "inc")
    reports = []
    for sequence, size in enumerate((4 * 1024 * 1024 - 4096, 4 * 1024 * 1024 + 4096), 16):
        identifier = f"size-{sequence}"
        c = MailboxCommand(
            sequence, identifier, 1, model_list_request=ModelListRequest("codex", False)
        )
        journal.record_poll_reply(MailboxPollReply((c,)), "inc")
        result = CommandResult(identifier, model_list={"text": "x" * size})
        journal.finish(identifier, result, outputs_owed=False)
        report = MacResultReporter(cast(MacHttpClient, None), journal)._report()
        reports.append(
            _digest(type_adapter(CommandOutcomeReport).dump_json(CommandOutcomeReport(report)))
        )
        journal.mark_reported((identifier,))
    return dict(
        poll=poll.decode(),
        raw_poll_bytes=len(poll),
        canonical_command=_digest(canonical),
        reports=reports,
    )


async def reference(
    root: Path, oracle: Oracle, patch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], dict[str, Any]]:
    patch.setattr(mac_mailbox, "now_ms", lambda: NOW)
    box = MacOutbox(root / "spool")
    journal = CommandJournal(box.root, "inc", outputs_owed=oracle.policy)
    commands = {
        c.command_id: c
        for c in [
            command("duplicate", 1),
            command("a1", 2),
            command("a2", 3),
            command("b", 4, "b"),
            command("stop", 5, stop=True),
            MailboxCommand(6, "model", 1, model_list_request=ModelListRequest("codex", False)),
            command("f", 9),
            command("k", 10),
            command("r", 11),
            MailboxCommand(12, "shutdown", 1, model_list_request=ModelListRequest("codex", True)),
            command("committed", 13),
            command("committed-later", 14),
        ]
    }
    results = {
        i: CommandResult(
            i, CommandOutcome(i, c.child_command.child, CommandDisposition.succeeded, {})
        )
        for i, c in commands.items()
        if c.child_command is not None
    }
    duplicate = results["duplicate"].child_outcome
    assert duplicate is not None
    results["duplicate"] = replace(
        results["duplicate"],
        child_outcome=replace(
            duplicate, disposition=CommandDisposition.uncertain, result={"reason": "fixture"}
        ),
    )
    results["model"] = CommandResult("model", model_list={"value": "model"})
    results["shutdown"] = CommandResult(
        "shutdown",
        model_list={
            "models": [
                dict(
                    name="🦉" * 270_000,
                    integer=123456799,
                    numbers=[1.0, -0.0, 1e-7, 1e-5, 1e16],
                )
            ]
        },
    )
    assert len(encoded(CommandResult, results["shutdown"])) > 1024 * 1024
    spec: dict[str, Any] = dict(
        commands={i: encoded(MailboxCommand, c).hex() for i, c in commands.items()},
        results={i: encoded(CommandResult, r).hex() for i, r in results.items()},
        now_ms=str(NOW),
    )

    def reply(*values: MailboxCommand) -> MailboxPollReply:
        return MailboxPollReply(values)

    seed = reply(commands["duplicate"])
    served_duplicate = replace(commands["duplicate"], command_sequence=8)
    polled = reply(*(commands[i] for i in ("a1", "a2", "b", "stop", "model")), served_duplicate)
    spec.update(
        seed=encoded(MailboxPollReply, seed).hex(),
        poll=encoded(MailboxPollReply, polled).hex(),
        shutdown=encoded(MailboxPollReply, reply(commands["shutdown"])).hex(),
        committed=encoded(
            MailboxPollReply, reply(commands["committed"], commands["committed-later"])
        ).hex(),
        report_commands=encoded(
            MailboxPollReply, reply(*(commands[i] for i in ("f", "k", "r")))
        ).hex(),
    )
    (
        planned,
        planned_waiting,
        first,
        release,
        stopped,
        replayed,
        catalog_entered,
        catalog_release,
        compacted,
    ) = (asyncio.Event() for _ in range(9))
    finished = {i: asyncio.Event() for i in ("a1", "a2", "b", "stop", "model", "shutdown")}
    calls: list[str] = []
    completed: list[str] = []
    replays: list[str] = []
    catalog_reads: list[str] = []
    reports: list[Any] = []
    report_replies: list[str] = []
    poll_calls, fault_mark = 0, False
    retry_times: dict[str, list[float]] = {"poll": [], "runner": []}
    original_mark, original_shrink = journal.mark_reported, journal.shrink_to_tombstones
    loop = asyncio.get_running_loop()

    def mark(ids: Any) -> None:
        nonlocal fault_mark
        if fault_mark:
            fault_mark = False
            raise sqlite3.OperationalError("synthetic report feedback write failure")
        original_mark(ids)

    def shrink() -> None:
        original_shrink()
        loop.call_soon_threadsafe(compacted.set)

    patch.setattr(journal, "mark_reported", mark)
    patch.setattr(journal, "shrink_to_tombstones", shrink)

    def wake() -> None:
        if journal.result_of("model") is not None:
            finished["model"].set()

    class Runner:
        admission_stopped = False

        async def planned_replay_finished(self) -> None:
            planned_waiting.set()
            await planned.wait()

        async def run(self, child: Any) -> Any:
            i = child.command_id
            calls.append(i)
            if i == "b":
                retry_times["runner"].append(time.perf_counter())
                if len(retry_times["runner"]) == 1:
                    raise OSError("scripted runner retry")
            if i in ("a1", "stop"):
                (first if i == "a1" else stopped).set()
                await release.wait()
            journal.finish(i, results[i], outputs_owed=oracle.policy(commands[i], results[i]))
            if i == "stop":
                journal.mark_outputs_published(i)
            completed.append(i)
            finished[i].set()
            return results[i].child_outcome

        async def publish_owed_outputs(self, c: MailboxCommand, r: CommandResult) -> None:
            replays.append(c.command_id)
            journal.mark_outputs_published(c.command_id)
            replayed.set()

    class Catalog:
        async def read(self, request: ModelListRequest) -> Any:
            i = "shutdown" if request.refresh else "model"
            catalog_reads.append(i)
            if i == "shutdown":
                catalog_entered.set()
                try:
                    await catalog_release.wait()
                except asyncio.CancelledError:
                    # This scripted owned call shields its result, as the required TS port does.
                    await catalog_release.wait()
            return results[i].model_list

    responses = [
        CommandOutcomeReply(("k",), ()),
        CommandOutcomeReply(("unrelated",), ()),
        CommandOutcomeReply(("k",), ()),
        CommandOutcomeReply((), (RejectedCommandResult("r", "reject"),)),
        CommandOutcomeReply(("f",), ()),
    ]

    class Client:
        async def poll_commands(self, after: int, wait_seconds: float) -> MailboxPollReply:
            nonlocal poll_calls
            poll_calls += 1
            if poll_calls == 1:
                retry_times["poll"].append(time.perf_counter())
                raise ConnectionError("scripted poll retry")
            if poll_calls == 2:
                retry_times["poll"].append(time.perf_counter())
                return polled
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def report_outcomes(self, report: CommandOutcomeReport) -> CommandOutcomeReply:
            nonlocal fault_mark
            reports.append(_digest(encoded(CommandOutcomeReport, report)))
            response = responses[len(reports) - 1]
            report_replies.append(encoded(CommandOutcomeReply, response).hex())
            if len(reports) == 1:
                fault_mark = True
            return response

    runner, client = Runner(), Client()
    queues = MacCommandQueues(
        cast(MacRunner, runner), journal, cast(MacCatalog, Catalog()), result_finished=wake
    )
    poll = MacPollLoop(
        cast(MacHttpClient, client), journal, queues, incarnation="inc", result_finished=wake
    )
    polling: asyncio.Task[Any] | None = None
    reporting: asyncio.Task[Any] | None = None
    try:
        journal.record_poll_reply(seed, "inc")
        journal.finish("duplicate", results["duplicate"], outputs_owed=True)
        journal.mark_outputs_published("duplicate")
        polling = asyncio.create_task(poll.run())
        await planned_waiting.wait()
        planned_blocked = poll_calls == 0
        planned.set()
        await asyncio.gather(
            first.wait(),
            stopped.wait(),
            finished["b"].wait(),
            finished["model"].wait(),
            replayed.wait(),
        )
        second_not_started = "a2" not in calls
        release.set()
        await asyncio.gather(*(finished[i].wait() for i in ("a1", "a2", "stop")))
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
        polling = None
        fifo = calls.index("a1") < calls.index("a2") and completed.index("a1") < completed.index(
            "a2"
        )
        original_mark(["duplicate", "a1", "a2", "b", "stop", "model"])
        journal.record_poll_reply(reply(*(commands[i] for i in ("f", "k", "r"))), "inc")
        for i in ("f", "k", "r"):
            journal.finish(i, results[i], outputs_owed=False)
        reporter = MacResultReporter(cast(MacHttpClient, client), journal)
        reporting = asyncio.create_task(reporter.run())
        await compacted.wait()
        reporting.cancel()
        await asyncio.gather(reporting, return_exceptions=True)
        reporting = None
        late_reply_ack_applied = all(result.command_id != "f" for result in journal.unreported(32))
        journal.record_poll_reply(reply(commands["shutdown"]), "inc")
        queues.submit(commands["shutdown"])
        await catalog_entered.wait()
        closing = asyncio.create_task(queues.close())
        await asyncio.sleep(0)
        stop_waited = not closing.done()
        catalog_release.set()
        await closing
        stored = journal.result_of("shutdown")
        assert stored is not None
        journal.mark_reported(("shutdown",))
        journal.record_poll_reply(reply(commands["committed"], commands["committed-later"]), "inc")
        journal.close()
        journal = CommandJournal(box.root, "inc", outputs_owed=oracle.policy)
        spec["recovery_results"] = {}
        for key in ("committed", "committed-later"):
            recovered = journal.result_of(key)
            assert recovered is not None and recovered.child_outcome is not None
            assert recovered.child_outcome.disposition is CommandDisposition.uncertain
            spec["recovery_results"][key] = _digest(encoded(CommandResult, recovered))
            journal.mark_outputs_published(key)
        journal.mark_reported(("committed", "committed-later"))
        spec["performance"] = performance_spec(journal)
        spec["reference_retry_seconds"] = {
            key: times[1] - times[0] for key, times in retry_times.items()
        }
        oracle.save()
        spec["report_replies"] = report_replies
        return dict(
            planned_blocked=planned_blocked,
            second_not_started=second_not_started,
            fifo=fifo,
            late_reply_ack_applied=late_reply_ack_applied,
            calls=calls,
            retry_delays={
                key: times[1] - times[0] >= OUTBOX_RETRY_INITIAL_SECONDS - 0.01
                for key, times in retry_times.items()
            },
            replays=replays,
            catalog_reads=catalog_reads,
            reports=reports,
            stop_waited=stop_waited,
            shutdown_result=_digest(type_adapter(CommandResult).dump_json(stored)),
        ), spec
    finally:
        release.set()
        catalog_release.set()
        planned.set()
        for task in (polling, reporting):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await queues.close()
        journal.close()
        box.close()


def node(root: Path, spec: dict[str, Any], oracle: Oracle, homes: dict[str, str]) -> dict[str, Any]:
    root.mkdir()
    source = root / "input.json"
    source.write_text(json.dumps(dict(spec, root=str(root), fixture=str(oracle.path))))
    excluded = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    environment = {
        k: v for k, v in os.environ.items() if not k.startswith("PLAN_") and k not in excluded
    }
    environment.update(homes)
    environment.update(
        PLAN_TEST_MODE="1", PLAN_EXECUTOR_ROOT=str(root), PLAN_SERVER_URL="http://127.0.0.1:1"
    )
    process = subprocess.Popen(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs';"
            "const {mailbox_parity}=await import(process.argv[1]);"
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8'));"
            "process.stdout.write(JSON.stringify(await mailbox_parity(input)));",
            (ROOT / "server/executor/mac/mailbox/testing/parity.ts").as_uri(),
            str(source),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=35)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)
        raise
    assert process.returncode == 0, stderr
    return cast(dict[str, Any], json.loads(stdout))


def test_unit22_mac_mailbox_scripted_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    tmp_path = tmp_path.resolve(strict=True)
    homes = create_synthetic_homes(tmp_path)
    oracle = Oracle(tmp_path / "oracle.json")
    with synthetic_home_environment(homes), monkeypatch.context() as patch:
        expected, spec = asyncio.run(
            asyncio.wait_for(reference(tmp_path / "python", oracle, patch), 30)
        )
        observed = node(tmp_path / "typescript", spec, oracle, homes)
    assert observed["observations"] == expected, observed["stats"]
    assert expected["planned_blocked"] and expected["fifo"] and expected["stop_waited"]
    assert expected["second_not_started"]
    assert expected["late_reply_ack_applied"]
    assert expected["retry_delays"] and all(expected["retry_delays"].values())
    assert expected["replays"] == ["duplicate"] and len(expected["reports"]) == 5
    assert observed["aggregate_shutdown"] and all(observed["aggregate_shutdown"].values())
    assert observed["performance"]["canonical_command"] == spec["performance"]["canonical_command"]
    assert observed["performance"]["reports"] == spec["performance"]["reports"]
    assert observed["performance"]["canonical_command"][0] > spec["performance"]["raw_poll_bytes"]
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        report = dict(
            observed,
            kind="direct",
            equal=True,
            elapsed_seconds=time.perf_counter() - started,
            reference_retry_seconds=spec["reference_retry_seconds"],
        )
        (output / "unit-22-mac-mailbox.json").write_text(json.dumps(report, indent=2))
