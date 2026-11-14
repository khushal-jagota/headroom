"""One private-file state sequence protects pinned Mac bytes and retained authority."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit21_backups import _report
from tests.compare.direct.test_unit21_updates import _environment
from tests.unit.test_mac_updates import MacProvider, attempt

from planner.core import change_signal
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    parse_attempt,
    parse_release,
)
from planner.environments.update_contracts import PublishedUpdate, UpdateAttemptState
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.contracts import ExecutorHealthRead
from planner.executor.update_contracts import MacUpdateReport

ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "server/environments/updates/mac/testing/compare.ts"


def _python(request: dict[str, Any]) -> dict[str, Any]:
    emitted: list[None] = []
    downloads: list[int] = []
    ready: PublishedUpdate | None = None
    root = Path(request["root"]) / "mac-updates"
    attempts = FileUpdateAttemptStore(root.parent / "update-attempt.json")
    releases = {key: parse_release(value) for key, value in request["releases"].items()}
    records = {key: parse_attempt(value) for key, value in request["attempts"].items()}

    class Provider:
        def available(self) -> None:
            return None

        def download_artifact(self, artifact_id: int, destination: Path) -> None:
            downloads.append(artifact_id)
            shutil.copyfile(request["artifacts"][str(artifact_id)], destination)

    provider = Provider()

    def make(sha: str, available: bool) -> MacUpdateService:
        return MacUpdateService(
            root, attempts, provider if available else None, sha, lambda: ready
        )

    output: list[dict[str, Any]] = []
    with patch.object(change_signal, "emit", lambda: emitted.append(None)):
        service = make("a" * 40, True)
        for op in request["operations"]:
            try:
                result: Any = None
                action = op["action"]
                if action == "prepare":
                    service.prepare(releases[op["key"]])
                elif action == "write":
                    attempts.path.unlink(missing_ok=True)
                    attempts.create(records[op["key"]])
                elif action == "ready":
                    ready = None if op["key"] is None else releases[op["key"]]
                elif action == "restart":
                    service = make(op["sha"], op["provider"])
                elif action == "check":
                    result = asdict(service.check())
                elif action == "status":
                    status = service.status()
                    result = None if status is None else asdict(status)
                elif action == "report":
                    report = {**op["report"]}
                    if report.get("identity") is not None:
                        report["identity"] = ExecutorIdentity(**report["identity"])
                    health = None
                    if op.get("health") is not None:
                        value = op["health"]
                        health = ExecutorHealthRead(
                            presence=value["presence"],
                            since_ms=None,
                            identity=ExecutorIdentity(**value["identity"]),
                            last_contact_at_ms=None,
                            refusal=None,
                            queue_depth=None,
                            oldest_item_age_ms=None,
                            admission_stopped=False,
                        )
                    result = asdict(service.report(MacUpdateReport(**report), health))
                elif action == "phase":
                    result = service.phase(op["id"])
                elif action == "report_evidence":
                    path = (
                        root
                        / "reports"
                        / (hashlib.sha256(op["id"].encode()).hexdigest() + ".json")
                    )
                    os.utime(path, (1_800_000_000, 1_800_000_000))
                    result = {
                        "bytes": path.read_text(),
                        "reported_at": service.reported_at(op["id"]),
                    }
                elif action == "archive":
                    result = hashlib.sha256(
                        service.archive(op["id"]).read_bytes()
                    ).hexdigest()
                elif action == "prefetch":
                    result = hashlib.sha256(
                        service.prefetch_archive(op["id"]).read_bytes()
                    ).hexdigest()
                elif action == "expire_applied":
                    path = root / "applied.json"
                    value = json.loads(path.read_text())
                    value["release"]["expires_at"] = "2000-01-01T00:00:00Z"
                    path.write_text(json.dumps(value))
                elif action == "archives":
                    result = sorted(path.name for path in (root / "archives").iterdir())
                else:
                    raise AssertionError(action)
                output.append({"result": result})
            except Exception as error:
                output.append({"error": type(error).__name__, "message": str(error)})
    return {"output": output, "downloads": downloads, "emitted": len(emitted)}


def test_unit21_mac_updates_retained_archive_and_leased_report_sequence(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Gap: shared attempts must retain target authority/bytes across VPS restart and failure.

    Reuse Python's Mac archive/provider fixture. No transport, listener, Mac or agent runs.
    """
    began = time.perf_counter()
    fixture = MacProvider()
    first = fixture.publish()
    successor = fixture.publish("c" * 40)
    bad = fixture.publish("d" * 40, manifest_revision="e" * 40)
    # Exercise the exact persisted integer kind and ID, not a rounded JS projection.
    huge = 9_007_199_254_740_993
    assert first.mac is not None
    first = replace(first, mac=replace(first.mac, artifact_id=huge))
    assert first.mac is not None
    assert successor.mac is not None
    extra = replace(successor, mac=replace(successor.mac, artifact_id=7))
    fixtures = tmp_path / "artifacts"
    fixtures.mkdir()
    artifacts = {}
    for artifact_id, archive_bytes in [
        (huge, fixture.archives[4]),
        (5, fixture.archives[5]),
        (6, fixture.archives[6]),
    ]:
        path = fixtures / f"{artifact_id}.zip"
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr("panels-mac-runtime.tar.gz", archive_bytes)
            zipped.writestr("mac-runtime-publication.json", "{}")
        artifacts[str(artifact_id)] = str(path)
    extra_path = fixtures / "7.zip"
    shutil.copyfile(artifacts["5"], extra_path)
    with zipfile.ZipFile(extra_path, "a") as zipped:
        zipped.writestr("unexpected-member", b"extra")
    artifacts["7"] = str(extra_path)
    states: tuple[UpdateAttemptState, ...] = ("waiting", "preparing", "done")
    records: dict[str, dict[str, Any]] = {
        state: asdict(attempt(first, state, mode="idle")) for state in states
    }
    records["retry"] = {**records["done"], "mac_attempt_id": "mac_two"}
    records["failed"] = asdict(attempt(successor, "failed", mac_attempt_id="mac_next"))
    records["no_mac"] = asdict(attempt(replace(successor, mac=None), "preparing"))
    floating = asdict(first)
    floating["mac"]["artifact_id"] = 1.0
    identity = {
        "protocol_version": PROTOCOL_VERSION,
        "spool_format_version": SPOOL_FORMAT_VERSION,
        "application_revision": "b" * 40,
        "executor_incarnation": "fixture-mac",
        "connection_generation": 1,
    }
    operations: list[dict[str, Any]] = []

    def add(action: str, **values: Any) -> None:
        operations.append({"action": action, **values})

    def report(phase: str, *, present: bool, request_id: str = "mac_one") -> None:
        add(
            "report",
            report={
                "request_id": request_id,
                "application_revision": "b" * 40,
                "phase": phase,
                "detail": "préparé 😀",
                "identity": identity,
            },
            health={
                "presence": "present" if present else "not_heard_from",
                "identity": identity,
            },
        )

    add("prepare", key="floating")
    add("prepare", key="first")
    add("prepare", key="first")
    add("ready", key="first")
    add("check")
    add("prefetch", id=first.mac.archive_sha256)
    add("write", key="waiting")
    add("check")
    add("write", key="preparing")
    add("check")
    report("current", present=True)
    report("ready_to_restart", present=False)
    report("ready_to_restart", present=True)
    add("phase", id="mac_one")
    add("report_evidence", id="mac_one")
    add("write", key="done")
    add("check")
    add("restart", sha="b" * 40, provider=True)
    add("check")
    report("current", present=True)
    operations[-1]["report"].pop("detail")
    add("status")
    add("archive", id="mac_one")
    add("write", key="retry")
    add("check")
    report("waiting", present=True)
    add("phase", id="mac_two")
    report("waiting", present=True, request_id="mac_two")
    operations[-1]["report"].pop("identity")
    add("prepare", key="bad")
    add("prepare", key="extra")
    add("prepare", key="successor")
    add("ready", key="successor")
    add("check")
    add("write", key="failed")
    add("expire_applied")
    add("ready", key=None)
    add("restart", sha="b" * 40, provider=False)
    add("check")
    add("archive", id="mac_two")
    add("prefetch", id=successor.mac.archive_sha256 if successor.mac else "")
    add("write", key="no_mac")
    add("status")
    add("archives")
    specification = {
        "releases": {
            "first": asdict(first),
            "successor": asdict(successor),
            "bad": asdict(bad),
            "floating": floating,
            "extra": asdict(extra),
        },
        "attempts": records,
        "artifacts": artifacts,
        "operations": operations,
    }
    manifest = json.dumps(
        {
            "schema_version": 1,
            "application_revision": "b" * 40,
            "platform": "darwin",
            "architecture": "arm64",
        }
    ).encode()

    def member(name: str, data: bytes = b"", **fields: Any) -> bytes:
        item = tarfile.TarInfo(name)
        item.size, item.mode = len(data), 0o755
        for key, value in fields.items():
            setattr(item, key, value)
        return item.tobuf(format=tarfile.PAX_FORMAT) + data + b"\0" * (-len(data) % 512)

    def archive(*members: bytes) -> bytes:
        return gzip.compress(b"".join(members) + b"\0" * 1024)

    valid = archive(
        member("mac-runtime.json", manifest), member("python/bin/python3", b"x")
    )
    inspections: dict[str, dict[str, Any]] = {}

    def inspect(
        name: str, payload: bytes, *, mode: str | None = None
    ) -> None:
        assert first.mac is not None
        path = fixtures / (name + ".zip")
        with zipfile.ZipFile(path, "w") as zipped:
            zipped.writestr("panels-mac-runtime.tar.gz", payload)
        publication = replace(
            first,
            mac=replace(
                first.mac,
                archive_sha256=hashlib.sha256(payload).hexdigest(),
                archive_size=len(payload),
            ),
        )
        inspections[name] = {
            "release": asdict(publication),
            "artifact": str(path),
            "mode": mode,
        }

    # One compatible bundle protects the inspection/extraction policy split.
    inspect(
        "odd",
        archive(
            member("mac-runtime.json", b"\xef\xbb\xbf" + manifest),
            member("python/bin/python3", b"x"),
            member("duplicate", b"a", mode=0o4755),
            member("duplicate", b"b"),
            member("outside", type=tarfile.SYMTYPE, linkname="/outside"),
            member("cycle-a", type=tarfile.SYMTYPE, linkname="cycle-b"),
            member("cycle-b", type=tarfile.SYMTYPE, linkname="cycle-a"),
            member("missing", type=tarfile.LNKTYPE, linkname="absent"),
            member("directory", type=tarfile.DIRTYPE, size=17),
        ),
    )
    inspect(
        "duplicate-manifest",
        archive(
            member("mac-runtime.json", manifest), member("mac-runtime.json", manifest)
        ),
    )
    inspect(
        "python-last",
        archive(
            member("mac-runtime.json", manifest),
            member("python/bin/python3", b"x"),
            member("python/bin/python3", type=tarfile.DIRTYPE),
        ),
    )
    inspect("gzip", b"\x1f\x8b" + b"\0" * 18)
    inspect(
        "json",
        archive(member("mac-runtime.json", b"{"), member("python/bin/python3", b"x")),
    )
    for mode in ("ebadf", "unexpected", "eof"):
        inspect(mode, valid, mode=mode)
    # A valid gzip-header mutation after hash pass one must refuse before acceptance.
    inspect("mutation", valid, mode="mutation")
    inspect("abort", valid, mode="abort")
    # Manifest encoding and metadata budgets remain explicit policy around node-tar.
    inspect(
        "utf16",
        archive(
            member("mac-runtime.json", manifest.decode().encode("utf-16")),
            member("python/bin/python3", b"x"),
        ),
    )
    metadata = tarfile.TarInfo("metadata")
    metadata.type, metadata.size = tarfile.XHDTYPE, 1024**2 + 1
    inspect(
        "metadata-limit", gzip.compress(metadata.tobuf() + b"\0" * 1024)
    )
    specification["inspections"] = inspections
    # Both implementations receive the same wire form, including platform lists.
    specification = json.loads(json.dumps(specification))
    inspections = specification["inspections"]
    py = tmp_path / "python"
    py.mkdir()
    expected = _python({**specification, "root": str(py)})
    node = tmp_path / "node"
    node.mkdir()
    input_path = node / "request.json"
    input_path.write_text(json.dumps({**specification, "root": str(node)}))
    script = """import {readFileSync} from 'node:fs';
const codec=await import(process.argv[3]);const module=await import(process.argv[1]);
const input=codec.parse_attempt_json(readFileSync(process.argv[2],'utf8'));
const result=await module.mac_update_sequence(input);
process.stdout.write(codec.canonical_json(result));"""
    process = subprocess.Popen(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            COMPANION.as_uri(),
            str(input_path),
            (ROOT / "server/environments/updates/codec.ts").as_uri(),
        ],
        cwd=ROOT,
        env={
            **_environment(node),
            "TMPDIR": str(node),
            "TMP": str(node),
            "TEMP": str(node),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    actual = json.loads(stdout)
    assert actual["parity"] == expected
    assert actual["children"]["spawned"] == actual["children"]["closed"] > 0
    observed = {}
    for name, spec in inspections.items():
        if spec["mode"] is not None:
            continue
        reference = _python(
            {
                "root": str(tmp_path / ("python-" + name)),
                "releases": {name: spec["release"]},
                "attempts": {},
                "artifacts": {str(huge): spec["artifact"]},
                "operations": [{"action": "prepare", "key": name}],
            }
        )["output"][0]
        outcome = actual["inspections"][name]["outcome"]
        if name in ("utf16", "json"):
            observed[name] = {
                "python": reference,
                "typescript": outcome,
                "reason": "UTF-8 manifest policy" if name == "utf16" else "Native JSON diagnostics",
            }
            if name != "json":
                assert outcome == {
                    "error": "UpdateConflict",
                    "message": "The Mac runtime archive is invalid.",
                }, name
        else:
            assert outcome == reference, name
    assert actual["inspections"]["json"]["outcome"]["error"] == "SyntaxError"
    operating = actual["inspections"]["ebadf"]["outcome"]
    assert operating["code"] == "EBADF" and abs(operating["errno"]) == 9
    assert operating["error"] != "UpdateConflict"
    assert actual["inspections"]["unexpected"]["outcome"] == {
        "error": "Error",
        "message": "Recorded inspection failure",
    }
    assert actual["inspections"]["mutation"]["outcome"] == {
        "error": "UpdateConflict",
        "message": "The Mac runtime archive changed during inspection.",
    }
    eof = actual["inspections"]["eof"]
    assert "error" in eof["outcome"] and eof["evidence"]["entered"]
    assert eof["evidence"]["identity"] and not eof["evidence"]["forced"]
    assert eof["evidence"]["error"] is None
    aborted = actual["inspections"]["abort"]
    assert aborted["outcome"] == {
        "error": "Error",
        "message": "One-shot processes stopped at shutdown",
    }
    assert aborted["evidence"]["entered"] and aborted["evidence"]["identity"]
    assert not aborted["evidence"]["forced"] and aborted["evidence"]["error"] is None
    assert aborted["temporary_removed"]
    print(
        "Mac native manifest policy/JSON diagnostics: "
        + json.dumps(observed, ensure_ascii=False)
    )
    errors = [entry for entry in expected["output"] if "error" in entry]
    assert [entry["message"] for entry in errors] == [
        "The Mac publication identity is invalid.",
        "The target Mac runtime is not the present, leased one.",
        "The Mac stop report does not match its present process.",
        "The Mac update report does not match its target.",
        "The Mac runtime does not match its source and platform.",
        "The Mac runtime artifact is invalid.",
        "The requested Mac runtime is not the ready release.",
    ]
    assert expected["downloads"] == [huge, 6, 7, 5]
    assert expected["output"][-1]["result"] == sorted(
        [first.mac.archive_sha256 + ".tar.gz", successor.mac.archive_sha256 + ".tar.gz"]
    )
    print(
        f"mac retained/report sequence {len(operations)} operations: "
        f"{time.perf_counter() - began:.6f}s"
    )
    _report(
        request,
        "unit21-mac-updates.json",
        began,
        operation_count=len(operations),
        actual_children=actual["children"],
        native_differences=observed,
        hosted_producer_compatibility="pending",
    )
