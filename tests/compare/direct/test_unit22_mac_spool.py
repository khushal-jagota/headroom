"""Fresh populated parity, TS-created rollback compatibility, and one precommit crash."""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    ItemSubmission,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    encode_bounded,
    type_adapter,
)

ROOT = Path(__file__).resolve().parents[3]
NOW_NS = 1_700_000_010_000_000_000
PRAGMAS = (
    "user_version journal_mode synchronous fullfsync trusted_schema "
    "foreign_keys recursive_triggers auto_vacuum"
).split()


def _rows(root: Path) -> dict[str, Any]:
    with closing(
        sqlite3.connect(f"{(root / 'spool/outbox.sqlite3').as_uri()}?mode=ro", uri=True)
    ) as db:
        result: dict[str, Any] = {
            table: [
                [v.hex() if isinstance(v, bytes) else v for v in row]
                for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}")
            ]
            for table, order in (
                ("sender", "singleton"),
                ("items", "sequence"),
                ("item_files", "sequence,relative_path"),
                ("ticket_files", "ticket_id,relative_path"),
                ("sqlite_sequence", "name"),
            )
        }
    result["blobs"] = {
        p.name: p.read_bytes().hex() for p in sorted((root / "spool/blobs").iterdir())
    }
    return result


def _receipt(box: MacOutbox, submission: str, accepted: bool) -> AcceptanceReceipt:
    return AcceptanceReceipt(
        box.sender_id,
        submission,
        ReceiptDisposition.accepted if accepted else ReceiptDisposition.refused,
        {"original": True},
        1,
    )


def _reference(
    root: Path,
    steps: list[dict[str, Any]],
    opened: float,
    observed: float,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    clock = [opened]
    captures: list[Any] = []
    receipts: list[Any] = []
    with monkeypatch.context() as patch:
        patch.setattr(time, "time_ns", lambda: NOW_NS)
        patch.setattr(time, "monotonic", lambda: clock[0])
        box = MacOutbox(root / "spool")
        try:
            for step in steps:
                operation = step["operation"]
                if operation == "recent":
                    (root / "artifacts/tickets/ticket/recent.txt").write_bytes(b"recent-B\n")
                elif operation == "orphan":
                    (root / "spool/blobs/.tmp-orphan").write_bytes(b"uncommitted")
                elif operation == "acknowledge":
                    held = box.lookup(step["submission_id"])
                    assert held is not None
                    receipt = _receipt(box, step["submission_id"], step["accepted"])
                    step["receipt_hex"] = type_adapter(AcceptanceReceipt).dump_json(receipt).hex()
                    step["invalidated"] = [
                        {
                            "ticket_id": m.owner_id,
                            "relative_path": m.relative_path,
                            "sha256": m.sha256,
                        }
                        for m in held.envelope.file_dependencies
                    ]
                    box.acknowledge(receipt)
                    row = box._db.execute(
                        "SELECT receipt_json FROM items WHERE submission_id=?",
                        (step["submission_id"],),
                    ).fetchone()
                    receipts.append(None if row is None else row[0].hex())
                else:
                    columns = step["columns"]
                    payload: JsonObject = {
                        "child": {"child_id": columns["child_id"]},
                        "marker": columns["submission_id"],
                    }
                    if step.get("duplicate"):
                        payload["marker"] = "changed duplicate must not replace original"
                    envelope = box.add(
                        OutboxEntry(
                            columns["conversation_id"],
                            EnvelopeKind(columns["kind"]),
                            payload,
                            columns["submission_id"],
                        ),
                        ticket_id="ticket" if step.get("capture") else None,
                    )
                    original = box._db.execute(
                        "SELECT payload_json FROM items WHERE submission_id=?",
                        (envelope.submission_id,),
                    ).fetchone()[0]
                    step["payload_hex"] = (
                        json.dumps(
                            {"payload": payload, "files": []}, sort_keys=True, separators=(",", ":")
                        ).encode()
                        if step.get("duplicate")
                        else original
                    ).hex()
                    if step.get("capture"):
                        files = [
                            dict(
                                box._db.execute(
                                    "SELECT * FROM ticket_files "
                                    "WHERE ticket_id='ticket' AND relative_path=?",
                                    (m.relative_path,),
                                ).fetchone()
                            )
                            for m in envelope.file_dependencies
                        ]
                        captures.append(
                            [
                                {k: str(v) if k == "modified_ns" else v for k, v in f.items()}
                                for f in files
                            ]
                        )
            clock[0] = observed
            profile = {p: str(box._db.execute(f"PRAGMA {p}").fetchone()[0]) for p in PRAGMAS}
            return {
                "sender": box.sender_id,
                "captures": captures,
                "receipts": receipts,
                "pending": [[e.submission_id, e.sequence] for e in box.pending()],
                "conversations": [
                    [r[0], r[1]]
                    for r in box._db.execute(
                        "SELECT conversation_id,MIN(sequence) FROM items "
                        "WHERE receipt_json IS NULL GROUP BY conversation_id ORDER BY MIN(sequence)"
                    )
                ],
                "count": box.pending_count(),
                "age": box.lease_backlog().oldest_item_age_ms,
                "writer_profile": profile,
                "reader_profile": {p: profile[p] for p in ("trusted_schema", "foreign_keys")},
            }
        finally:
            box.close()


def _node(
    root: Path,
    steps: list[dict[str, Any]],
    homes: dict[str, str],
    opened: float,
    observed: float,
    exit_code: int = 0,
) -> dict[str, Any] | None:
    specification = root / "driver-input.json"
    specification.write_text(
        json.dumps(
            dict(
                root=str(root),
                steps=steps,
                now_ns=str(NOW_NS),
                opened_at=opened,
                observed_at=observed,
            )
        )
    )
    provider_keys = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    environment = {
        k: v for k, v in os.environ.items() if not k.startswith("PLAN_") and k not in provider_keys
    }
    environment.update(homes)
    environment.update(PLAN_TEST_MODE="1", PLAN_EXECUTOR_ROOT=str(root))
    process = subprocess.Popen(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs';"
            "const {spool_parity}=await import(process.argv[1]);"
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8'));"
            "process.stdout.write(JSON.stringify(await spool_parity(input),"
            "(_k,v)=>typeof v==='bigint'?v.toString():v));",
            (ROOT / "server/executor/mac/storage/testing/parity.ts").as_uri(),
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
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)
        raise
    assert process.returncode == exit_code, stderr
    return json.loads(stdout) if exit_code == 0 else None


def _append(
    submission: str,
    conversation: str = "c_one",
    child: str = "child",
    kind: str = "cli_write",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "operation": "append",
        "columns": dict(
            submission_id=submission, conversation_id=conversation, child_id=child, kind=kind
        ),
        **extra,
    }


def test_unit22_mac_spool_rows_capture_receipts_and_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started, tmp_path = time.perf_counter(), tmp_path.resolve(strict=True)
    homes = create_synthetic_homes(tmp_path)
    snapshots: list[dict[str, Any]] = []
    seed, py, ts = (tmp_path / name for name in ("seed", "py", "ts"))
    with synthetic_home_environment(homes):
        _reference(
            seed,
            [
                _append("seed_one", kind="backend_event"),
                _append("seed_two", "c_two", "other", "backend_event"),
            ],
            1,
            1,
            monkeypatch,
        )
        for root in (py, ts):
            shutil.copytree(seed, root)
            folder = root / "artifacts/tickets/ticket"
            folder.mkdir(parents=True)
            (folder / "old.txt").write_bytes("stable bytes 🦉\n".encode())
            (folder / "recent.txt").write_bytes(b"recent-A\n")
            os.utime(folder / "old.txt", ns=(NOW_NS, NOW_NS - 10_000_000_001))
            os.utime(folder / "recent.txt", ns=(NOW_NS, NOW_NS - 500_000_123))
            (root / "outside").write_bytes(b"do not capture symlink targets")
            (folder / "link.txt").symlink_to(root / "outside")

        def paired(steps: list[dict[str, Any]], opened: float) -> None:
            expected = _reference(py, steps, opened, opened + 0.375, monkeypatch)
            assert _node(ts, steps, homes, opened, opened + 0.375) == expected
            canonical = _rows(py)
            assert _rows(ts) == canonical
            snapshots.append(canonical)

        # Python must run before the candidate: it supplies fresh opaque codec bytes.
        first = [
            _append("capture_one", capture=True),
            _append("capture_one", duplicate=True),
            {"operation": "recent"},
            _append("capture_two", capture=True),
        ]
        paired(first, 100)
        second = [
            {"operation": "acknowledge", "submission_id": "capture_one", "accepted": False},
            _append("capture_three", capture=True),
            {"operation": "acknowledge", "submission_id": "capture_two", "accepted": True},
            _append("stop", kind="child_stopped"),
            {"operation": "acknowledge", "submission_id": "stop", "accepted": True},
            _append("other_receipt", "c_two", "other"),
            {"operation": "acknowledge", "submission_id": "other_receipt", "accepted": True},
            {"operation": "orphan"},
        ]
        paired(second, 200)
        committed = [_append("committed", "c_crash", "crash")]
        _reference(py, committed, 300, 300, monkeypatch)
        crash = _append("uncommitted", "c_crash", "crash")
        crash.update(operation="crash", payload_hex=committed[0]["payload_hex"])
        _node(ts, committed + [crash], homes, 300, 300, exit_code=71)
        paired([], 400)
        recovered = _rows(py)
        submissions = [row[1] for row in recovered["items"]]
        assert "committed" in submissions and "uncommitted" not in submissions
        assert "other_receipt" not in submissions and ".tmp-orphan" not in recovered["blobs"]
        assert (ts / "outside").read_bytes() == b"do not capture symlink targets"
        fresh = tmp_path / "typescript-created"
        fresh.mkdir()
        near, rejected, refusal_messages = [], [], []
        for sequence, character in enumerate(("x", "🦉"), 1):
            sub = f"fresh_{sequence}"
            # This case owns the byte budget, independent of Python number spelling.
            payload: JsonObject = {"text": "", "numbers": [1, 2, 3]}
            envelope = QueuedEnvelope(
                "s" * 32, "fresh", sequence, sub, EnvelopeKind.backend_event, payload
            )
            overhead = len(encode_bounded(ItemSubmission(envelope)))
            payload["text"] = character * (
                (MAX_ENVELOPE_BYTES - overhead) // len(character.encode())
            )
            step = _append(sub, "fresh", "", "backend_event")
            step["payload_hex"] = (
                json.dumps({"files": [], "payload": payload}, sort_keys=True, separators=(",", ":"))
                .encode()
                .hex()
            )
            step["encoded_size"] = len(encode_bounded(ItemSubmission(envelope)))
            near.append(step)
            payload["text"] = str(payload["text"]) + character
            try:
                encode_bounded(ItemSubmission(envelope))
            except ValueError as error:
                refusal_messages.append(str(error))
            else:
                pytest.fail("the fresh Python encoder admitted an oversized fixture")
            oversized = _append(sub + "_over", "fresh", "", "backend_event", reject=True)
            oversized["payload_hex"] = (
                json.dumps({"files": [], "payload": payload}, sort_keys=True, separators=(",", ":"))
                .encode()
                .hex()
            )
            rejected.append(oversized)
        created = _node(fresh, near + rejected, homes, 500, 500)
        assert created is not None and created["refusals"] == refusal_messages
        reverse = MacOutbox(fresh / "spool")
        try:
            pending = reverse.pending()
            assert created["writer_profile"] == {
                p: str(reverse._db.execute(f"PRAGMA {p}").fetchone()[0]) for p in PRAGMAS
            }
            assert reverse.sender_id == created["sender"]
            assert [e.submission_id for e in pending] == [
                s["columns"]["submission_id"] for s in near
            ]
            assert [len(encode_bounded(ItemSubmission(e))) for e in pending] == [
                s["encoded_size"] for s in near
            ]
            reverse.acknowledge(_receipt(reverse, pending[0].submission_id, True))
            assert [e.submission_id for e in reverse.pending()] == [pending[1].submission_id]
        finally:
            reverse.close()
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-22-mac-spool.json").write_text(
            json.dumps(
                dict(
                    kind="direct",
                    equal=True,
                    snapshots=snapshots,
                    recovered=recovered,
                    typescript_created=created,
                    crash_exit_code=71,
                    elapsed_seconds=time.perf_counter() - started,
                ),
                indent=2,
            )
        )
