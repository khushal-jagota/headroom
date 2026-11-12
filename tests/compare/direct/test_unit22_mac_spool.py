"""Populated storage parity and one real pre-commit process death, without a host.

Python supplies its fresh opaque codec bytes. TS must independently capture files
and preserve every canonical row, immutable blob, receipt, and ordering decision.
"""

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

from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    JsonObject,
    ReceiptDisposition,
    type_adapter,
)

ROOT = Path(__file__).resolve().parents[3]
NOW_NS = 1_700_000_010_000_000_000


def _rows(root: Path) -> dict[str, Any]:
    with closing(
        sqlite3.connect(f"{(root / 'spool/outbox.sqlite3').as_uri()}?mode=ro", uri=True)
    ) as db:
        result: dict[str, Any] = {}
        for table, order in (
            ("sender", "singleton"),
            ("items", "sequence"),
            ("item_files", "sequence,relative_path"),
            ("ticket_files", "ticket_id,relative_path"),
            ("sqlite_sequence", "name"),
        ):
            result[table] = [
                [v.hex() if isinstance(v, bytes) else v for v in row]
                for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}")
            ]
        result["version"] = db.execute("PRAGMA user_version").fetchone()[0]
    result["blobs"] = {
        p.name: p.read_bytes().hex() for p in sorted((root / "spool/blobs").iterdir())
    }
    return result


def _row(box: MacOutbox, submission: str) -> dict[str, Any] | None:
    item = box._db.execute("SELECT * FROM items WHERE submission_id=?", (submission,)).fetchone()
    if item is None:
        return None
    return {
        key: (
            value.hex() if isinstance(value, bytes) else str(value) if key == "sequence" else value
        )
        for key, value in dict(item).items()
    }


def _reference(
    root: Path,
    steps: list[dict[str, Any]],
    opened: float,
    observed: float,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    clock = [opened]
    trace = []
    with monkeypatch.context() as patch:
        patch.setattr(time, "time_ns", lambda: NOW_NS)
        patch.setattr(time, "monotonic", lambda: clock[0])
        box = MacOutbox(root / "spool")
        try:
            for step in steps:
                operation = step["operation"]
                if operation == "recent":
                    (root / "artifacts/tickets/ticket/recent.txt").write_bytes(b"recent-B\n")
                    continue
                if operation == "orphan":
                    (root / "spool/blobs/.tmp-orphan").write_bytes(b"uncommitted")
                    continue
                if operation == "acknowledge":
                    held = box.lookup(step["submission_id"])
                    assert held is not None
                    receipt = AcceptanceReceipt(
                        box.sender_id,
                        step["submission_id"],
                        ReceiptDisposition.accepted
                        if step["accepted"]
                        else ReceiptDisposition.refused,
                        {"original": True},
                        1,
                    )
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
                    trace.append(_row(box, step["submission_id"]))
                    continue
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
                result = _row(box, envelope.submission_id)
                assert result is not None
                step["payload_hex"] = (
                    json.dumps(
                        {"payload": payload, "files": []}, sort_keys=True, separators=(",", ":")
                    )
                    .encode()
                    .hex()
                    if step.get("duplicate")
                    else result["payload_json"]
                )
                observation: dict[str, Any] = {"row": result}
                if step.get("capture"):
                    observation["captured"] = [
                        dict(
                            box._db.execute(
                                "SELECT * FROM ticket_files "
                                "WHERE ticket_id='ticket' AND relative_path=?",
                                (manifest.relative_path,),
                            ).fetchone()
                        )
                        for manifest in envelope.file_dependencies
                    ]
                    for file in observation["captured"]:
                        file["byte_count"], file["modified_ns"] = (
                            str(file["byte_count"]),
                            str(file["modified_ns"]),
                        )
                trace.append(observation)
            clock[0] = observed
            return {
                "sender": box.sender_id,
                "trace": trace,
                "pending": [_row(box, item.submission_id) for item in box.pending()],
                "conversations": [
                    [row[0], str(row[1])]
                    for row in box._db.execute(
                        "SELECT conversation_id,MIN(sequence) FROM items "
                        "WHERE receipt_json IS NULL "
                        "GROUP BY conversation_id ORDER BY MIN(sequence)"
                    )
                ],
                "count": str(box.pending_count()),
                "age": box.lease_backlog().oldest_item_age_ms,
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
            {
                "root": str(root),
                "steps": steps,
                "now_ns": str(NOW_NS),
                "opened_at": opened,
                "observed_at": observed,
            }
        )
    )
    provider_keys = {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key not in provider_keys
    }
    environment.update(homes)
    environment.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_EXECUTOR_ROOT": str(root),
            "PLAN_SERVER_URL": "http://127.0.0.1:1",
        }
    )
    process = subprocess.Popen(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs';"
            "const {spool_parity}=await import(process.argv[1]);"
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8'));"
            "process.stdout.write(JSON.stringify(await spool_parity(input)));",
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
        if process.returncode is None:  # No wait/poll reaped this owned leader.
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
        "columns": {
            "submission_id": submission,
            "conversation_id": conversation,
            "child_id": child,
            "kind": kind,
        },
        **extra,
    }


def test_unit22_mac_spool_rows_capture_receipts_and_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    tmp_path = tmp_path.resolve(strict=True)
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed"
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
        for name in ("py", "ts"):
            root = tmp_path / name
            shutil.copytree(seed, root)
            folder = root / "artifacts/tickets/ticket"
            folder.mkdir(parents=True)
            (folder / "old.txt").write_bytes("stable bytes 🦉\n".encode())
            (folder / "recent.txt").write_bytes(b"recent-A\n")
            os.utime(folder / "old.txt", ns=(NOW_NS, NOW_NS - 10_000_000_001))
            os.utime(folder / "recent.txt", ns=(NOW_NS, NOW_NS - 500_000_123))
            (root / "outside").write_bytes(b"do not capture symlink targets")
            (folder / "link.txt").symlink_to(root / "outside")
        py, ts = tmp_path / "py", tmp_path / "ts"
        first = [
            _append("capture_one", capture=True),
            _append("capture_one", duplicate=True),
            {"operation": "recent"},
            _append("capture_two", capture=True),
        ]
        expected = _reference(py, first, 100, 100.375, monkeypatch)
        assert _node(ts, first, homes, 100, 100.375) == expected
        before_receipts = _rows(py)
        assert _rows(ts) == before_receipts
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
        expected = _reference(py, second, 200, 200.5, monkeypatch)
        assert _node(ts, second, homes, 200, 200.5) == expected
        before_restart = _rows(py)
        assert _rows(ts) == before_restart
        committed = [_append("committed", "c_crash", "crash")]
        _reference(py, committed, 300, 300, monkeypatch)
        crash = _append("uncommitted", "c_crash", "crash")
        crash.update(operation="crash", payload_hex=committed[0]["payload_hex"])
        _node(ts, committed + [crash], homes, 300, 300, exit_code=71)
        expected = _reference(py, [], 400, 400.125, monkeypatch)
        assert _node(ts, [], homes, 400, 400.125) == expected
        recovered = _rows(py)
        assert _rows(ts) == recovered
        submissions = [row[1] for row in recovered["items"]]
        assert "committed" in submissions and "uncommitted" not in submissions
        assert "other_receipt" not in submissions
        assert ".tmp-orphan" not in recovered["blobs"]
        assert (ts / "outside").read_bytes() == b"do not capture symlink targets"
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-22-mac-spool.json").write_text(
            json.dumps(
                {
                    "kind": "direct",
                    "equal": True,
                    "before_receipts": before_receipts,
                    "before_restart": before_restart,
                    "recovered": recovered,
                    "crash_exit_code": 71,
                    "elapsed_seconds": time.perf_counter() - started,
                },
                indent=2,
            )
        )
