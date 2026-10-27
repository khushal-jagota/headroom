"""Private observer limits and domain isolation, with no retained case connection."""

import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from planner.executor.capture import (
    MAX_BYTES,
    MAX_RECORD_BYTES,
    MAX_RECORDS,
    WINDOW_NS,
    Capture,
    CaptureConfig,
    capture_request,
    get_capture,
    observe,
)

TARGET: dict[str, Any] = {
    "sender_id": "c819dff02dc340df84e8179f61d26fc2",
    "conversation_id": "conv_2ea03c97da924b61abf1a0a8c4ec724d",
    "submission_id": "80c2ef60ce3947e4ad2c146eb343a45d",
    "sequence": 80,
    "kind": "backend_event",
    "method": "tool_call_started",
    "executor_incarnation": "2d89b304-9f78-434f-b5ae-8b1091f55c41",
    "payload_sha256": "cfb7edf8402904d5ad8858c061997a7934660332b7dccb701744f59e3692b431",
}


def identity(generation: int) -> Any:
    return SimpleNamespace(
        executor_incarnation=TARGET["executor_incarnation"], connection_generation=generation
    )


def make_capture(tmp_path: Path) -> Capture:
    return Capture(
        CaptureConfig(
            tmp_path / "capture.jsonl",
            tmp_path / "status.json",
            tmp_path / "startup.json",
            "a" * 40,
            "b" * 40,
            dict(TARGET),
        ),
        timer=False,
    )


def records(capture: Capture) -> list[Any]:
    return [json.loads(line) for line in capture.config.sink.read_bytes().splitlines()]


def start(capture: Capture, stamp: int) -> None:
    capture.config.startup_receipt.write_text(
        json.dumps(
            {
                "process_id": os.getpid(),
                "diagnostic_revision": "a" * 40,
                "monotonic_ns": stamp,
            }
        )
    )
    capture.tick()


def test_default_disabled_and_broken_observer_cannot_replace_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PANELS_DIAGNOSTIC_CAPTURE_CONFIG", raising=False)
    assert get_capture() is None

    class Broken:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("observer secret")

    error = ValueError("domain error")
    with pytest.raises(ValueError) as raised, observe(Broken(), "domain_effect"):  # type: ignore[arg-type]
        raise error
    assert raised.value is error


def test_caps_reserve_final_and_safe_exception_output(tmp_path: Path) -> None:
    capture = make_capture(tmp_path)
    observation = capture.hello(identity(1))
    assert observation is not None

    class SecretName(ValueError):
        pass

    observation.fail("handler_enter", SecretName("private credential"))
    for _ in range(400):
        observation.enter("handler_enter")
    data = capture.config.sink.read_bytes()
    result = records(capture)
    assert len(result) <= MAX_RECORDS and len(data) <= MAX_BYTES
    assert max(map(len, data.splitlines(keepends=True))) <= MAX_RECORD_BYTES
    assert result[-1]["phase"] == "capture_final" and result[-1]["truncated"]
    assert b"private credential" not in data and b"SecretName" not in data
    assert any(r.get("exception_class") == "other" for r in result)
    assert capture.config.sink.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("receipt", ["missing", "before", "future", "pid", "revision", "oversized"])
def test_invalid_or_missing_startup_never_selects_natural(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt: str,
) -> None:
    now = [10_000_000_000]
    monkeypatch.setattr("planner.executor.capture.time.monotonic_ns", lambda: now[0])
    capture = make_capture(tmp_path)
    if receipt != "missing":
        raw: dict[str, Any] = {
            "process_id": os.getpid(),
            "diagnostic_revision": "a" * 40,
            "monotonic_ns": now[0],
        }
        if receipt == "before":
            raw["monotonic_ns"] -= 1
        if receipt == "future":
            raw["monotonic_ns"] += 1
        if receipt == "pid":
            raw["process_id"] += 1
        if receipt == "revision":
            raw["diagnostic_revision"] = "b" * 40
        capture.config.startup_receipt.write_text(
            " " * 9000 if receipt == "oversized" else json.dumps(raw)
        )
    capture.tick()
    now[0] += WINDOW_NS
    capture.tick()
    assert capture.closed and capture.natural_generation is None
    assert records(capture)[-1]["capture_status"] == "inconclusive_setup"


def test_delayed_receipt_uses_original_deadline_and_requires_full_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100]
    monkeypatch.setattr("planner.executor.capture.time.monotonic_ns", lambda: now[0])
    capture = make_capture(tmp_path)
    now[0] = 200
    capture.hello(identity(1))
    now[0] = 300
    start(capture, 150)
    assert capture.deadline_ns == 150 + WINDOW_NS
    capture.hello(identity(2))
    assert capture.natural_generation == 2
    capture.finish("test_done")


def test_other_replay_is_counted_and_request_phases_do_not_cross(tmp_path: Path) -> None:
    capture = make_capture(tmp_path)
    target = capture_request(
        capture,
        identity(1),
        SimpleNamespace(
            kind="envelope",
            payload={
                **TARGET,
                "payload": {
                    "method": TARGET["method"],
                    "child": {
                        "executor_incarnation": TARGET["executor_incarnation"],
                        "child_id": "b67b9942-bcc5-4925-b3bd-ce551df037a3",
                        "conversation_id": TARGET["conversation_id"],
                    },
                },
            },
        ),
    )
    assert target is not None and target.target
    target.enter("conversation_lock_wait")
    for _ in range(1000):
        other = capture_request(capture, identity(1), SimpleNamespace(kind="envelope", payload={}))
        assert other is not None
        other.bind_envelope(SimpleNamespace(**{**TARGET, "sequence": 1, "submission_id": "other"}))
        other.enter("handler_enter")
        other.complete("response_send_enter")
    target.complete("conversation_lock_wait")
    capture.finish("test_done")
    assert len(records(capture)) < 10
    assert capture.other_frames == 1000
    assert len({r["request_id"] for r in records(capture) if "request_id" in r}) == 1


def test_commit_exception_resolves_once_by_narrow_readonly_receipt(tmp_path: Path) -> None:
    capture = make_capture(tmp_path)
    target = capture_request(
        capture,
        identity(1),
        SimpleNamespace(
            kind="envelope",
            payload={
                **TARGET,
                "payload": {
                    "method": TARGET["method"],
                    "child": {
                        "executor_incarnation": TARGET["executor_incarnation"],
                        "child_id": "b67b9942-bcc5-4925-b3bd-ce551df037a3",
                        "conversation_id": TARGET["conversation_id"],
                    },
                },
            },
        ),
    )
    assert target is not None
    database = tmp_path / "receipt.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE executor_receipts(sender_id TEXT, submission_id TEXT, sequence INTEGER, "
            "payload_sha256 TEXT, disposition TEXT, accepted_at_vps_ms INTEGER, result_json TEXT)"
        )
        db.execute(
            "CREATE UNIQUE INDEX receipt_identity ON executor_receipts(sender_id,submission_id)"
        )
        db.execute(
            "INSERT INTO executor_receipts VALUES(?,?,?,?,?,?,?)",
            (
                TARGET["sender_id"],
                TARGET["submission_id"],
                80,
                TARGET["payload_sha256"],
                "accepted",
                123,
                "secret result",
            ),
        )
    target.complete("commit_call_returned", role="preflight")
    assert target.commit_status == "unknown"
    target.fail("commit_enter", RuntimeError("postcommit secret"), role="acceptance")
    assert target.commit_status == "unknown"
    target.settled(str(database))
    assert target.commit_status == "durable_receipt" and target.disposition == "accepted"
    target.fail("response_send_enter", ValueError("secret"))
    target.settled(str(database))
    assert target.disposition == "accepted"
    assert sum(r["phase"] == "receipt_lookup" for r in records(capture)) == 1
    assert b"secret" not in capture.config.sink.read_bytes()
    capture.finish("test_done")


def test_sink_failure_has_private_separate_status_and_target_mismatch_stops(tmp_path: Path) -> None:
    capture = make_capture(tmp_path)
    capture.sink.close()
    observation = capture.hello(identity(1))
    assert observation is not None
    assert capture.closed
    assert json.loads(capture.config.status.read_text()) == {"capture_status": "sink_failure"}
    assert capture.config.status.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("case", ["incomplete", "expired", "identity", "envelope"])
def test_correlation_limits_never_follow_another_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    now = [100]
    monkeypatch.setattr("planner.executor.capture.time.monotonic_ns", lambda: now[0])
    capture = make_capture(tmp_path)
    if case == "incomplete":
        capture.hello(identity(1))
        now[0] = 200
        start(capture, 150)
    elif case == "expired":
        now[0] += WINDOW_NS + 10
        start(capture, 100)
    elif case == "identity":
        capture.hello(SimpleNamespace(executor_incarnation="different", connection_generation=1))
    else:
        capture_request(
            capture,
            identity(1),
            SimpleNamespace(
                kind="envelope",
                payload={
                    **TARGET,
                    "payload_sha256": "0" * 64,
                },
            ),
        )
    assert capture.closed and capture.natural_generation is None
    assert records(capture)[-1]["capture_status"] in {
        "inconclusive_setup",
        "identity_changed",
        "target_mismatch",
        "no_natural_opportunity",
    }


def test_initial_sink_failure_does_not_overwrite_existing_sink(tmp_path: Path) -> None:
    capture = make_capture(tmp_path)
    capture.finish("test_done")
    original = capture.config.sink.read_bytes()
    with pytest.raises(FileExistsError):
        Capture(capture.config, timer=False)
    assert capture.config.sink.read_bytes() == original
    assert json.loads(capture.config.status.read_text()) == {"capture_status": "sink_failure"}


@pytest.mark.parametrize("size", [1700, MAX_RECORD_BYTES + 1])
def test_byte_caps_and_oversize_still_reserve_final(tmp_path: Path, size: int) -> None:
    capture = make_capture(tmp_path)
    for _ in range(MAX_RECORDS):
        capture._record({"phase": "frame_decoded", "fixed_test_padding": "x" * size})
    result = records(capture)
    data = capture.config.sink.read_bytes()
    assert capture.closed and result[-1]["phase"] == "capture_final"
    assert result[-1]["truncated"] and result[-1]["dropped_records"] == 1
    assert len(data) <= MAX_BYTES
    assert max(map(len, data.splitlines(keepends=True))) <= MAX_RECORD_BYTES
    assert len(result) < MAX_RECORDS


def test_natural_generation_failure_waits_for_target_receipt_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100]
    monkeypatch.setattr("planner.executor.capture.time.monotonic_ns", lambda: now[0])
    capture = make_capture(tmp_path)
    start(capture, 100)
    capture.hello(identity(1))
    capture.hello(identity(2))
    observation = capture_request(
        capture,
        identity(2),
        SimpleNamespace(
            kind="envelope",
            payload={
                **TARGET,
                "payload": {
                    "method": TARGET["method"],
                    "child": {
                        "executor_incarnation": TARGET["executor_incarnation"],
                        "conversation_id": TARGET["conversation_id"],
                        "child_id": "b67b9942-bcc5-4925-b3bd-ce551df037a3",
                    },
                },
            },
        ),
    )
    assert observation is not None and observation.target
    database = tmp_path / "receipt.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE executor_receipts(sender_id TEXT, submission_id TEXT, sequence INTEGER, "
            "payload_sha256 TEXT, disposition TEXT, accepted_at_vps_ms INTEGER)"
        )
        db.execute(
            "CREATE UNIQUE INDEX receipt_identity ON executor_receipts(sender_id,submission_id)"
        )
        db.execute(
            "INSERT INTO executor_receipts VALUES(?,?,?,?,?,?)",
            (
                TARGET["sender_id"],
                TARGET["submission_id"],
                80,
                TARGET["payload_sha256"],
                "accepted",
                1,
            ),
        )
    observation.fail("commit_enter", RuntimeError("secret"), role="acceptance")
    observation.generation_closed(identity(2), ConnectionError("secret"))
    assert not capture.closed
    observation.settled(str(database))
    assert observation.disposition == "accepted"
    assert capture.closed and records(capture)[-1]["capture_status"] == "terminal_failure"
    assert any(
        r["phase"] == "receipt_lookup" and r["receipt_disposition"] == "accepted"
        for r in records(capture)
    )


def test_concurrent_receipt_resolution_and_command_keep_single_final_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    capture = make_capture(tmp_path)
    start(capture, capture.created_ns)
    capture.hello(identity(1))
    capture.hello(identity(2))
    message = SimpleNamespace(
        kind="envelope",
        payload={
            **TARGET,
            "payload": {
                "method": TARGET["method"],
                "child": {
                    "executor_incarnation": TARGET["executor_incarnation"],
                    "conversation_id": TARGET["conversation_id"],
                    "child_id": "b67b9942-bcc5-4925-b3bd-ce551df037a3",
                },
            },
        },
    )
    first = capture_request(capture, identity(2), message)
    second = capture_request(capture, identity(2), message)
    assert first is not None and second is not None
    database = tmp_path / "receipt.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE executor_receipts(sender_id TEXT, submission_id TEXT, sequence INTEGER, "
            "payload_sha256 TEXT, disposition TEXT, accepted_at_vps_ms INTEGER)"
        )
        db.execute(
            "CREATE UNIQUE INDEX receipt_identity ON executor_receipts(sender_id,submission_id)"
        )
        db.execute(
            "INSERT INTO executor_receipts VALUES(?,?,?,?,?,?)",
            (
                TARGET["sender_id"],
                TARGET["submission_id"],
                80,
                TARGET["payload_sha256"],
                "accepted",
                1,
            ),
        )
    entered, release = threading.Event(), threading.Event()
    connect = sqlite3.connect
    connections: list[int] = []

    def paused_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connections.append(1)
        entered.set()
        assert release.wait(5)
        return cast(sqlite3.Connection, connect(*args, **kwargs))

    monkeypatch.setattr("planner.executor.capture.sqlite3.connect", paused_connect)
    first.fail("handler_failed", OSError("secret"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        resolution = pool.submit(first.settled, str(database))
        assert entered.wait(5)
        second.receipt(
            SimpleNamespace(
                sender_id=TARGET["sender_id"],
                submission_id=TARGET["submission_id"],
                payload_sha256=TARGET["payload_sha256"],
                disposition="accepted",
            )
        )
        second.enter("response_send_enter")
        second.complete("response_send_done")
        pool.submit(second.settled, str(database)).result(timeout=5)
        assert not capture.closed
        # Collector I/O holds no lock required by request observations.
        first.command(
            "12345678-1234-1234-1234-123456789abc", "stop_child", TARGET["conversation_id"]
        )
        release.set()
        resolution.result(timeout=5)
    assert len(connections) == 1
    assert first.disposition == "accepted" and capture.closed
    original = capture.config.sink.read_bytes()
    first.command("12345678-1234-1234-1234-123456789abc", "stop_child", TARGET["conversation_id"])
    assert capture.config.sink.read_bytes() == original
    result = records(capture)
    assert sum(r["phase"] == "capture_final" for r in result) == 1
    assert result[-1]["phase"] == "capture_final"
    assert result[-1]["capture_status"] == "terminal_failure"
    assert any(
        r["phase"] == "receipt_lookup" and r["receipt_disposition"] == "accepted" for r in result
    )


def test_handled_domain_refusal_keeps_receipt_and_send_disposition(tmp_path: Path) -> None:
    capture = make_capture(tmp_path)
    observation = capture.hello(identity(1))
    assert observation is not None
    observation.target = True
    observation.fail("domain_effect", ValueError("private rejection"))
    observation.receipt(
        SimpleNamespace(
            sender_id=TARGET["sender_id"],
            submission_id=TARGET["submission_id"],
            payload_sha256=TARGET["payload_sha256"],
            disposition="refused",
        )
    )
    observation.enter("response_send_enter")
    observation.complete("response_send_done")
    assert not observation.failed
    assert observation.disposition == "refused" and observation.send_status == "complete"
    assert not observation.started and observation.request_id not in capture.active
    capture.finish("test_done")


def test_supported_launcher_can_opt_in_with_private_database_sibling_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PANELS_DIAGNOSTIC_CAPTURE_CONFIG", raising=False)
    database = tmp_path / "isolated.sqlite"
    assert get_capture(str(database)) is None
    (tmp_path / "diagnostic-capture.json").write_text(
        json.dumps(
            {
                "sink": str(tmp_path / "private.jsonl"),
                "status": str(tmp_path / "private-status.json"),
                "startup_receipt": str(tmp_path / "startup.json"),
                "diagnostic_revision": "a" * 40,
                "mac_revision": "b" * 40,
                "target": TARGET,
            }
        )
    )
    capture = get_capture(str(database))
    assert capture is not None
    capture.finish("test_done")


def test_idle_natural_read_is_no_target_ingress_and_unadmitted_identity_is_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100]
    monkeypatch.setattr("planner.executor.capture.time.monotonic_ns", lambda: now[0])
    capture = make_capture(tmp_path)
    session = capture.hello(None)
    assert session is not None
    session.enter("hello_decode")
    start(capture, 100)
    session.hello_admitted(identity(1))
    session.complete("hello_decode")
    natural = capture.hello(identity(2))
    assert natural is not None
    natural.enter("frame_decode")
    now[0] += WINDOW_NS
    capture.tick()
    result = records(capture)
    before = next(r for r in result if r["phase"] == "hello_decode")
    assert before["epoch"] == "setup"
    assert before["identity_evidence"] == "vps_cached" and "executor_incarnation" not in before
    assert result[-1]["capture_status"] == "no_target_ingress"
    assert result[-1]["completion"] == "unknown" and "frame_decode" in result[-1]["open_phases"]
