"""Fresh Python journal oracle, reverse opening, and one cross-store process crash."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.executor import mac_mailbox
from planner.executor.mac_mailbox import CommandJournal
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import command_owes_outputs, owed_outputs
from planner.executor.protocol import (
    CommandDisposition,
    CommandOutcome,
    CommandResult,
    MailboxCommand,
    MailboxPollReply,
    ModelListRequest,
    type_adapter,
)

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_700_000_010_123
HIGH = 123456799
INCARNATION = "own-incarnation"


def _digest(raw: bytes) -> list[Any]:
    return [len(raw), hashlib.sha256(raw).hexdigest()]


def _document(raw: bytes) -> Any:
    if raw == b"":
        return raw  # Journal tombstones remain distinct from every JSON value.
    return json.loads(raw)


class Oracle:
    """Only prevalidated values enter this finite table. It never decodes TS input."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        self.indices: dict[tuple[str, bytes], int] = {}
        self.owed: dict[tuple[int, int], bool] = {}
        self.stats: list[dict[str, Any]] = []

    def remember(self, kind: Any, value: Any) -> bytes:
        raw = type_adapter(kind).dump_json(value)
        key = (kind.__name__, raw)
        if key not in self.indices:
            self.indices[key] = len(self.entries)
            self.entries.append(dict(root=kind.__name__, hex=raw.hex()))
        return raw

    def policy(self, command: MailboxCommand, result: CommandResult) -> bool:
        c = self.remember(MailboxCommand, command)
        r = self.remember(CommandResult, result)
        value = command_owes_outputs(command, result)
        self.owed[(self.indices[("MailboxCommand", c)], self.indices[("CommandResult", r)])] = value
        return value

    def rows(self, journal: CommandJournal) -> None:
        for command, result in journal._db.execute(
            "SELECT mailbox_command_json,result_json FROM commands"
        ):
            for kind, raw in ((MailboxCommand, command), (CommandResult, result)):
                if raw:
                    self.remember(kind, type_adapter(kind).validate_json(raw))

    def save(self) -> None:
        self.path.write_text(
            json.dumps(
                dict(
                    entries=self.entries, owed=[[*pair, value] for pair, value in self.owed.items()]
                )
            )
        )


def _inspect(journal: CommandJournal, box: MacOutbox, ids: list[str]) -> dict[str, Any]:
    def encoded(kind: Any, value: Any) -> Any:
        return _document(type_adapter(kind).dump_json(value))

    return dict(
        cursor=str(journal.poll_cursor()),
        unpublished=[
            dict(
                id=c.command_id,
                command=encoded(MailboxCommand, c),
                result=encoded(CommandResult, r),
            )
            for c, r in journal.unpublished()
        ],
        unreported=[
            dict(id=r.command_id, result=encoded(CommandResult, r)) for r in journal.unreported(32)
        ],
        results=[
            [i, None if (r := journal.result_of(i)) is None else encoded(CommandResult, r)]
            for i in ids
        ],
        pending=[[e.submission_id, str(e.sequence)] for e in box.pending()],
    )


def _reference(
    root: Path, steps: list[dict[str, Any]], oracle: Oracle, monkeypatch: pytest.MonkeyPatch
) -> list[Any]:
    observations: list[Any] = []
    with monkeypatch.context() as patch:
        patch.setattr(mac_mailbox, "now_ms", lambda: NOW)
        patch.setattr(time, "time_ns", lambda: NOW * 1_000_000)
        box = MacOutbox(root)
        try:
            journal = CommandJournal(root, INCARNATION, outputs_owed=oracle.policy)
            try:
                oracle.rows(journal)
                for step in steps:
                    op = step["op"]
                    if op == "poll":
                        reply = type_adapter(MailboxPollReply).validate_json(
                            bytes.fromhex(step["hex"])
                        )
                        observations.append(
                            [
                                dict(
                                    id=c.command_id,
                                    command=_document(oracle.remember(MailboxCommand, c)),
                                )
                                for c in journal.record_poll_reply(
                                    reply,
                                    step.get("incarnation", INCARNATION),
                                    polled_after=int(step.get("after", 0)),
                                )
                            ]
                        )
                    elif op == "finish":
                        result = type_adapter(CommandResult).validate_json(
                            bytes.fromhex(step["hex"])
                        )
                        journal.finish(step["id"], result, outputs_owed=step["owed"])
                    elif op == "lower":
                        observations.append(
                            journal.lower_poll_cursor(
                                int(step["high"]), cursor_at_lease=int(step["at_lease"])
                            )
                        )
                    elif op == "published":
                        journal.mark_outputs_published(step["id"])
                    elif op == "reported":
                        journal.mark_reported(step["ids"])
                    elif op == "shrink":
                        journal.shrink_to_tombstones()
                    elif op == "inspect":
                        observations.append(_inspect(journal, box, step["ids"]))
                    elif op == "publish":
                        command = type_adapter(MailboxCommand).validate_json(
                            bytes.fromhex(step["command_hex"])
                        )
                        finished_result = journal.result_of(command.command_id)
                        assert command.child_command is not None and finished_result is not None
                        assert finished_result.child_outcome is not None
                        entries = owed_outputs(command.child_command, finished_result.child_outcome)
                        assert len(entries) == 1
                        entry = entries[0]
                        envelope = box.add(entry)
                        step["columns"] = dict(
                            submission_id=envelope.submission_id,
                            conversation_id=entry.conversation_id,
                            child_id=command.child_command.child.child_id,
                            kind=entry.kind.value,
                        )
                        step["payload_hex"] = (
                            box._db.execute(
                                "SELECT payload_json FROM items WHERE submission_id=?",
                                (envelope.submission_id,),
                            )
                            .fetchone()[0]
                            .hex()
                        )
                    elif op != "crash":
                        raise AssertionError(op)
                    oracle.rows(journal)
            finally:
                journal.close()
        finally:
            box.close()
    oracle.save()
    return observations


def _snapshot(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for file, table, order in (
        ("commands.sqlite3", "commands", "command_id"),
        ("outbox.sqlite3", "items", "sequence"),
    ):
        with closing(sqlite3.connect(f"{(root / file).as_uri()}?mode=ro", uri=True)) as db:
            result[table] = [
                [v.hex() if isinstance(v, bytes) else v for v in row]
                for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}")
            ]
            result[file] = db.execute("PRAGMA user_version").fetchone()[0]
    return result


def _summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Retain each runtime's actual BLOB sizes and digests as compact evidence."""
    result = snapshot.copy()
    for table, columns in (("commands", (3, 5)), ("items", (5, 6))):
        result[table] = []
        for original in snapshot[table]:
            row = original.copy()
            for column in columns:
                if row[column] is not None:
                    row[column] = _digest(bytes.fromhex(row[column]))
            result[table].append(row)
    return result


def _meanings(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = {**snapshot}
    for table, columns in (("commands", (3, 5)), ("items", (5, 6))):
        result[table] = []
        for original in snapshot[table]:
            row = original.copy()
            for column in columns:
                if row[column] is not None:
                    row[column] = _document(bytes.fromhex(row[column]))
            result[table].append(row)
    return result


def _node(
    root: Path,
    steps: list[dict[str, Any]],
    oracle: Oracle,
    homes: dict[str, str],
    exit_code: int = 0,
    gate: Path | None = None,
) -> Any:
    specification = root.parent / "journal-input.json"
    specification.write_text(
        json.dumps(
            dict(
                root=str(root),
                fixture=str(oracle.path),
                incarnation=INCARNATION,
                now_ms=str(NOW),
                steps=steps,
                gate=None if gate is None else str(gate),
                gate_command_id="stop" if gate else None,
            )
        )
    )
    excluded = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    environment = {
        k: v for k, v in os.environ.items() if not k.startswith("PLAN_") and k not in excluded
    }
    environment.update(homes)
    environment.update(
        PLAN_TEST_MODE="1",
        PLAN_EXECUTOR_ROOT=str(root.parent),
        PLAN_SERVER_URL="http://127.0.0.1:1",
    )
    process = subprocess.Popen(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs';"
            "const {journal_parity}=await import(process.argv[1]);"
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8'));"
            "process.stdout.write(JSON.stringify(await journal_parity(input)));",
            (ROOT / "server/executor/mac/storage/testing/journal_parity.ts").as_uri(),
            str(specification),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        if gate is not None:
            deadline = time.monotonic() + 15
            while not gate.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.005)
            assert gate.exists(), "owned child did not enter the transaction gate"
            assert process.poll() is None, "owned child exited before SIGKILL"
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = process.communicate(timeout=35)
    except (subprocess.TimeoutExpired, AssertionError):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)
        raise
    assert process.returncode == exit_code, stderr
    if exit_code != 0:
        return None
    result = json.loads(stdout)
    assert result["stats"]["integrity_checks"] == result["stats"]["readers"]
    assert result["stats"]["integrity_checks"] == (
        result["stats"]["native_exact_checks"] + result["stats"]["imported_meaning_checks"]
    )
    oracle.stats.append(dict(result["stats"], operations=len(steps)))
    return result["observations"]


def _command(command_id: str, sequence: int, operation: str = "stop") -> MailboxCommand:
    return type_adapter(MailboxCommand).validate_python(
        dict(
            command_sequence=sequence,
            command_id=command_id,
            created_at_vps_ms=HIGH,
            child_command=dict(
                command_id=command_id,
                child=dict(
                    executor_incarnation=INCARNATION,
                    child_id="child",
                    conversation_id="conversation",
                ),
                operation=operation,
                turn=None
                if operation == "stop"
                else dict(conversation_id="conversation", turn_number=HIGH),
                arguments={"kind": "stop"}
                if operation == "stop"
                else dict(
                    kind="write_prompt",
                    content=[{"text": "hello"}],
                    sender_content=[],
                    sender_label="fixture",
                    mode="queue",
                ),
            ),
        )
    )


def test_unit22_mac_journal_roundtrip_and_publication_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    tmp_path = tmp_path.resolve(strict=True)
    homes = create_synthetic_homes(tmp_path)
    py, ts = (tmp_path / name / "spool" for name in ("py", "ts"))
    for root in (py, ts):
        root.parent.mkdir()
    oracle = Oracle(tmp_path / "python-journal-oracle.json")
    own, stale = _command("own", 1, "write_prompt"), _command("stale", 2, "write_prompt")
    model = MailboxCommand(3, "models", HIGH, model_list_request=ModelListRequest("fixture", False))
    stop = _command("stop", HIGH)
    ids = ["own", "stale", "models", "stop", "missing"]
    snapshots = []

    def poll(*commands: MailboxCommand, **extra: Any) -> dict[str, Any]:
        for command in commands:
            oracle.remember(MailboxCommand, command)
        return dict(
            op="poll",
            hex=oracle.remember(MailboxPollReply, MailboxPollReply(commands)).hex(),
            expected_command_count=len(commands),
            **extra,
        )

    def finish(command: MailboxCommand, result: CommandResult) -> dict[str, Any]:
        return dict(
            op="finish",
            id=command.command_id,
            hex=oracle.remember(CommandResult, result).hex(),
            owed=oracle.policy(command, result),
        )

    def paired(steps: list[dict[str, Any]]) -> list[Any]:
        expected = _reference(py, steps, oracle, monkeypatch)
        assert _node(ts, steps, oracle, homes) == expected
        snapshot = _snapshot(py)
        assert _meanings(_snapshot(ts)) == _meanings(snapshot)
        snapshots.append(_summary(snapshot))
        return expected

    with synthetic_home_environment(homes):
        large = CommandResult(
            "models",
            model_list={
                "models": [
                    {
                        "name": "🦉" * 270_000,
                        "integer": HIGH,
                        "negative": -HIGH,
                        "numbers": [1.0, -0.0, 1e-7, 1e-5, 1e16],
                    }
                ]
            },
        )
        assert len(oracle.remember(CommandResult, large)) > 1024 * 1024
        assert stop.child_command is not None
        stopped = CommandResult(
            "stop",
            CommandOutcome("stop", stop.child_command.child, CommandDisposition.succeeded, {}),
        )
        # Both roots start absent. TS creates and populates its own two databases.
        paired(
            [
                poll(own),
                poll(stale, incarnation="old-incarnation"),
                poll(model, stop),
                finish(model, large),
                finish(stop, stopped),
                dict(op="inspect", ids=ids),
            ]
        )
        seed = tmp_path / "python-populated" / "spool"
        shutil.copytree(py, seed)
        mixed = _snapshot(py)
        states = {row[0]: (row[4], row[6], row[7]) for row in mixed["commands"]}
        assert states["own"][0] == states["stale"][0] == "recorded"
        assert states["stop"] == ("finished", 0, 0)
        assert states["models"] == ("finished", 0, 1)
        # Python opens the independently TS-created root and WRITES restart settlement.
        rollback = _reference(ts, [dict(op="inspect", ids=ids)], oracle, monkeypatch)
        assert rollback == _reference(py, [dict(op="inspect", ids=ids)], oracle, monkeypatch)
        assert _meanings(_snapshot(ts)) == _meanings(_snapshot(py))
        assert all(row[4] == "finished" for row in _snapshot(ts)["commands"])
        reverse_summary = _summary(_snapshot(ts))
        # TS next opens the Python-populated mixed-state store, rather than its own rows.
        for root in (py, ts):
            shutil.rmtree(root)
            shutil.copytree(seed, root)
        _reference(py, [], oracle, monkeypatch)
        gated_stop = replace(stop, command_sequence=HIGH + 1)
        gate_poll = poll(gated_stop)
        oracle.policy(gated_stop, stopped)
        oracle.save()
        # The duplicate first UPDATEs sequence, then its policy callback holds the write.
        # Kill this owned process group before COMMIT, and prove that UPDATE rolled back.
        _node(
            ts,
            [gate_poll],
            oracle,
            homes,
            exit_code=-signal.SIGKILL,
            gate=ts.parent / "transaction-entered",
        )
        assert _meanings(_snapshot(ts)) == _meanings(_snapshot(py))
        killed_summary = _summary(_snapshot(ts))
        # Opening settles own and stale incarnations; own prompt output blocks its report.
        restarted = paired(
            [
                dict(op="inspect", ids=ids),
                dict(op="lower", high="3", at_lease="3"),
                dict(op="lower", high="3", at_lease=str(HIGH)),
                poll(_command("stale-held", HIGH + 1), after=str(HIGH)),
                poll(replace(own, command_sequence=4), replace(stop, command_sequence=5)),
                finish(stop, CommandResult("stop")),  # first finish remains the startup result
                dict(op="inspect", ids=ids),
            ]
        )
        assert restarted[0]["results"][0][1] is not None
        paired(
            [dict(op="published", id=i) for i in ids[:-1]]
            + [
                dict(op="reported", ids=ids[:-1]),
                dict(op="shrink"),
                poll(replace(own, command_sequence=6), replace(model, command_sequence=7)),
                dict(op="inspect", ids=ids),
            ]
        )
        # An orderly exit after durable finish separately checks the publication boundary.
        crash = _command("crash-stop", 8)
        assert crash.child_command is not None
        outcome = CommandOutcome(
            crash.command_id,
            crash.child_command.child,
            CommandDisposition.succeeded,
            {"integer": HIGH, "fraction": -0.0},
        )
        crash_steps = [
            poll(crash),
            finish(crash, CommandResult(crash.command_id, outcome)),
            dict(op="crash"),
        ]
        _reference(py, crash_steps, oracle, monkeypatch)
        _node(ts, crash_steps, oracle, homes, exit_code=73)
        assert _meanings(_snapshot(ts)) == _meanings(_snapshot(py))
        publish = dict(op="publish", command_hex=oracle.remember(MailboxCommand, crash).hex())
        recovered = paired(
            [
                dict(op="inspect", ids=[crash.command_id]),
                publish,
                publish.copy(),
                dict(op="published", id=crash.command_id),
                dict(op="inspect", ids=ids),
            ]
        )
        assert recovered[0]["unpublished"][-1]["id"] == crash.command_id
        assert (
            len(recovered[-1]["pending"]) == 1
        )  # deterministic output identity deduplicates replay
        assert _reference(ts, [dict(op="inspect", ids=ids)], oracle, monkeypatch) == [recovered[-1]]
    request.node.user_properties.extend(
        [
            ("paired_snapshots", len(snapshots)),
            ("elapsed_seconds", round(time.perf_counter() - started, 3)),
            (
                "test_boundary",
                "populated Python/TS handoff + SIGKILL rollback + orderly publication exit",
            ),
        ]
    )
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-22-mac-journal.json").write_text(
            json.dumps(
                dict(
                    kind="direct",
                    equal=True,
                    canonical_snapshots=snapshots,
                    blob_summary=(
                        "JSON meanings compare across peers. "
                        "Native reader bytes and SHA256 match native stored BLOBs."
                    ),
                    recovered=recovered,
                    finish_before_publication_exit_code=73,
                    midtransaction_signal="SIGKILL",
                    midtransaction_recovered=killed_summary,
                    python_to_typescript_mixed_states=_summary(mixed),
                    python_restart_settlement_on_typescript_store=reverse_summary,
                    typescript_created=True,
                    python_reverse_opens=2,
                    large_result_bytes=len(oracle.remember(CommandResult, large)),
                    elapsed_seconds=time.perf_counter() - started,
                    stats=oracle.stats,
                    fixture_entries=len(oracle.entries),
                    codec_evidence=(
                        "Native codec decode/encode with message-meaning equality. "
                        "Every native stream retains its byte count and SHA256 checks "
                        "against native storage. "
                        "Finite output-policy oracle only."
                    ),
                    measurement_scope="Journal open/ops/close; outbox opening excluded",
                ),
                indent=2,
            )
        )
