"""Fresh Python and TS inbox custody on one populated, copied synthetic database.

File intake's existing phased proof ends at staging. This continuation proves
bounded canonical publication, receipt replay and domain rollback without a peer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.test_unit22_files import IDENTITY, ROOT
from tests.support.executor_presence import make_current
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.vps_inbox import InboxStore, MissingFiles


def test_populated_inbox_publication_and_receipts(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    began = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    left, right = tmp_path / "source", tmp_path / "candidate"
    left.mkdir()
    path = left / "state.sqlite"
    manifests = [
        wire.FileManifest(
            wire.FileScope.ticket,
            "ticket",
            f"out/{index}.txt",
            hashlib.sha256(f"payload-{index}".encode()).hexdigest(),
            len(f"payload-{index}"),
        )
        for index in range(34)
    ]
    child = ChildReference(IDENTITY.executor_incarnation, "child", "conversation")
    envelope = wire.QueuedEnvelope(
        sender_id="sender",
        conversation_id="conversation",
        sequence=2,
        submission_id="submission",
        kind=wire.EnvelopeKind.backend_event,
        payload={"child": wire.encode_value(child)},
        file_dependencies=tuple(manifests),
    )
    with synthetic_home_environment(homes):
        with closing(connect(str(path))) as sql:
            create_schema(sql)
        make_current(str(path), IDENTITY, at_ms=100)
        with closing(connect(str(path))) as sql:
            sql.execute(
                "INSERT INTO executor_child_assignments VALUES(?,?,?,?,?,?,NULL)",
                (
                    child.executor_incarnation,
                    child.child_id,
                    child.conversation_id,
                    "ticket",
                    "ticket",
                    1,
                ),
            )
            sql.commit()
        store = InboxStore(str(path))
        for index, manifest in enumerate(manifests):
            target = store.file_target(manifest)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"payload-{index}".encode() if index == 0 else b"old bytes")
            if index:
                staged = store._staged_file(manifest)
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(f"payload-{index}".encode())
        shutil.copytree(left, right)
        results: list[Any] = []
        published = store.publish_file_dependencies(envelope, IDENTITY)

        def writer(
            connection: sqlite3.Connection, envelope: wire.QueuedEnvelope
        ) -> wire.JsonObject:
            connection.execute("UPDATE executor_child_assignments SET created_at_ms=2")
            return {"saved": True}

        accepted = store.accept(envelope, writer, 101, identity=IDENTITY, published=published)
        results.append(wire.encode_value(accepted))

        def no_replay(
            connection: sqlite3.Connection, envelope: wire.QueuedEnvelope
        ) -> wire.JsonObject:
            raise AssertionError("replay ran its writer")

        results.append(wire.encode_value(store.accept(envelope, no_replay, 102, identity=IDENTITY)))
        results.append(
            wire.encode_value(
                store.accept(
                    replace(envelope, sequence=1, submission_id="older"),
                    no_replay,
                    103,
                    identity=IDENTITY,
                )
            )
        )
        try:
            store.accept(
                replace(envelope, submission_id="conflict"), no_replay, 104, identity=IDENTITY
            )
        except PlannerError as error:
            results.append(error.to_payload())

        def refuse(
            connection: sqlite3.Connection, envelope: wire.QueuedEnvelope
        ) -> wire.JsonObject:
            connection.execute("UPDATE executor_child_assignments SET created_at_ms=3")
            raise ValueError("domain refused")

        refused = store.accept(
            replace(envelope, sequence=3, submission_id="refused"),
            refuse,
            105,
            identity=IDENTITY,
            published=published,
        )
        results.append(wire.encode_value(refused))
        missing = replace(manifests[0], relative_path="missing.txt", sha256="0" * 64)
        try:
            store.publish_file_dependencies(
                replace(
                    envelope, sequence=4, submission_id="missing", file_dependencies=(missing,)
                ),
                IDENTITY,
            )
        except MissingFiles as error:
            results.append({"missing": [wire.encode_value(item) for item in error.manifests]})
        with closing(connect(str(path))) as sql:
            expected = {
                "results": results,
                "positions": [
                    dict(row)
                    for row in sql.execute(
                        "SELECT sender_id,conversation_id,sequence,submission_id,disposition,"
                        "result_json FROM mac_inbox_positions ORDER BY sender_id,conversation_id"
                    )
                ],
                "assignments": [
                    dict(row)
                    for row in sql.execute(
                        "SELECT * FROM executor_child_assignments "
                        "ORDER BY executor_incarnation,child_id"
                    )
                ],
                "files": {
                    str(file.relative_to(left / "files")): file.read_text()
                    for file in sorted((left / "files").rglob("*"))
                    if file.is_file()
                },
                "staged": [file.name for file in sorted((left / "executor-uploads").iterdir())],
            }
    environment = {
        **homes,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/inbox.ts")],
        input=json.dumps(
            {
                "path": str(right / "state.sqlite"),
                "identity": wire.encode_value(IDENTITY),
                "envelope": wire.encode_value(envelope),
                "missing": wire.encode_value(missing),
            }
        ),
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    for result in (actual, expected):
        for position in result["positions"]:
            position["result_json"] = json.loads(position["result_json"])
    assert actual == expected
    assert len(actual["files"]) == 34
    assert actual["staged"] == []
    assert actual["positions"][0]["disposition"] == "refused"
    assert actual["assignments"][0]["created_at_ms"] == 2
    destination = request.config.getoption("--compare-report-dir", default=None)
    if destination:
        report = Path(destination) / "unit22-inbox.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - began,
                    "files": 34,
                    "source": expected,
                    "candidate": actual,
                },
                indent=2,
            )
            + "\n"
        )
