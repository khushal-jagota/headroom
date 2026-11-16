"""Compare populated attention collection across pagination and historical gaps.

Existing attention read proofs do not prove collector output identities, labels,
missing-value behavior, or the coherent private read boundary. Reuse canonical
schema/ticket fixtures, one SQLite copy, and one Node companion without a host.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.attention.collection import collect_daily
from planner.attention.configuration import DAILY_METRICS
from planner.attention.contracts import Destination
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.data import create_ticket

ROOT = Path(__file__).resolve().parents[3]
NOW = int(datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp())


def test_unit13_attention_collection(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    database = tmp_path / "attention.sqlite"
    with (
        synthetic_home_environment(create_synthetic_homes(tmp_path)),
        closing(connect(str(database))) as conn,
    ):
        create_schema(conn)
        conn.execute(
            "UPDATE attention_history_metadata SET started_at=?", (NOW - 86400 * 10,)
        )
        conn.execute(
            (
                "INSERT INTO "
                "conversations(conversation_id,backend_key,workspace_folder,access,"
                "created_at) VALUES('collector','codex',?,'full',?)"
            ),
            (str(tmp_path), NOW - 86400 * 10),
        )
        for index in range(52):
            ticket = create_ticket(
                conn,
                title=f"Attention destination {index}",
                principal=OWNER_PRINCIPAL,
                now=NOW - 86400,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type="coding",
                project_id="project_vylo",
            )
            snapshot = Destination(
                "ticket",
                ticket.id,
                ticket.title,
                f"/tickets/{ticket.id}",
                "project_vylo",
            )
            conn.execute(
                (
                    "INSERT INTO "
                    "attention_approval_events(ticket_id,field,actor_kind,actor_id,method,"
                    "occurred_at,destination_json) "
                    "VALUES(?, 'brief','owner','owner','explicit_accept',?,?)"
                ),
                (ticket.id, NOW - 3600, json.dumps(asdict(snapshot))),
            )
            conn.execute(
                (
                    "INSERT INTO "
                    "conversation_events(conversation_id,sequence,kind,payload,created_at) "
                    "VALUES('collector',?,'prompt',?,?)"
                ),
                (
                    index + 1,
                    json.dumps(
                        {
                            "content": [],
                            "sender": {"kind": "owner", "id": "owner"},
                            "recipient": {"kind": "ticket", "id": ticket.id},
                            "sender_message_id": f"attention-{index}",
                            "mode": "steer",
                        }
                    ),
                    NOW - 3600,
                ),
            )
        conn.commit()
    dataset_ids = {metric: "dataset_" + metric for metric in DAILY_METRICS}
    measure_ids = {metric: "measure_" + metric for metric in DAILY_METRICS}
    histories: list[dict[str, str] | None] = [
        None,
        {"first_date": "2026-11-04", "last_date": "2026-11-04"},
        {"first_date": "2026-10-14", "last_date": "2026-10-14"},
    ]
    expected: list[list[dict[str, Any]]] = []
    for history in histories:
        output: list[dict[str, Any]] = []

        def record(
            identifier: str,
            day: str,
            value: float | None,
            unavailable_reason: str | None = None,
            _output: list[dict[str, Any]] = output,
            **kwargs: object,
        ) -> None:
            _output.append(
                {
                    "kind": "scalar",
                    "id": identifier,
                    "date": day,
                    "value": value,
                    "unavailable_reason": unavailable_reason,
                    **kwargs,
                }
            )

        def record_dataset(
            identifier: str,
            day: str,
            rows: list[dict[str, Any]],
            _output: list[dict[str, Any]] = output,
            **kwargs: object,
        ) -> None:
            _output.append(
                {
                    "kind": "dataset",
                    "id": identifier,
                    "date": day,
                    "rows": rows,
                    **kwargs,
                }
            )

        collect_daily(
            database,
            dataset_ids,
            measure_ids,
            context={"frozen_at": NOW, "history": history},
            record=record,
            record_dataset=record_dataset,
        )
        expected.append(output)
    current = expected[1]
    messages = next(
        row
        for row in current
        if row["kind"] == "scalar" and row["id"] == measure_ids["owner_messages"]
    )
    assert messages["value"] == 52
    assert (
        len(
            next(
                row
                for row in current
                if row["kind"] == "dataset"
                and row["id"] == dataset_ids["owner_approvals"]
            )["rows"]
        )
        >= 52
    )
    target = tmp_path / "typescript.sqlite"
    with sqlite3.connect(database) as source, sqlite3.connect(target) as other:
        source.backup(other)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "db_path": str(target),
                "now": NOW,
                "dataset_ids": dataset_ids,
                "measure_ids": measure_ids,
                "histories": histories,
            }
        )
    )
    actual = json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                (
                    "import {readFileSync} from 'node:fs'; import {pathToFileURL} from "
                    "'node:url'; const {run}=await "
                    "import(pathToFileURL(process.argv[2]).href); "
                    "process.stdout.write(JSON.stringify(await "
                    "run(JSON.parse(readFileSync(process.argv[1],'utf8')))));"
                ),
                str(spec),
                str(ROOT / "server/attention/testing/collection.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    expected_result = {
        "results": expected,
        "readonly": True,
        "no_write_capability": True,
        "path_inside_temp": True,
    }
    report = {
        "kind": "direct",
        "case": "unit13-attention-collection",
        "equal": actual == expected_result,
        "python": expected_result,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit13-attention-collection.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected_result
