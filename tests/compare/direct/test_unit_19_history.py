"""History HTTP paging and locale labels compare on a tiny synthetic database.

Existing workspace tests prove Python's reader alone. This case covers the new
ordinary TS route, rank and wrap, UTF-8 byte admission, and raw locale codepoints.
It reuses native homes and one schema seed, without a listener or conversation core.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.conversation.history.api import router
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.core.server import handle_planner_error
from planner.tickets.data import create_ticket

ROOT = Path(__file__).resolve().parents[3]

# Only these requests use en-US (explicitly or by the recorded fallback).
# German (15/16) and empty-locale root formatting (19) remain byte-exact.
EN_US_LABEL_REQUESTS = frozenset((0, 1, 2, 11, 13, 14, 17, 18, 20, 21))
LABEL_RULE = "history_icu_day_period_separator"


def compare_history(
    expected: dict[str, Any], actual: dict[str, Any]
) -> tuple[bool, list[dict[str, Any]]]:
    """Admit one known ICU-data difference at explicit date-label paths only."""
    compared = deepcopy(actual)
    captures: list[dict[str, Any]] = []
    for answer_index in sorted(EN_US_LABEL_REQUESTS):
        left = expected["answers"][answer_index]
        right = actual["answers"][answer_index]
        for entry_index, (python_entry, ts_entry) in enumerate(
            zip(
                left["body"].get("entries", []),
                right["body"].get("entries", []),
                strict=False,
            )
        ):
            python_label = python_entry["label"]
            ts_label = ts_entry["label"]
            if python_label == ts_label:
                continue
            python_date, separator, python_id = python_label.rpartition(" · ")
            ts_date, ts_separator, ts_id = ts_label.rpartition(" · ")
            if separator != " · " or ts_separator != separator or python_id != ts_id:
                continue
            # The full date/time and ID must agree. Exactly one separator code
            # point before the terminal AM/PM differs. No regex rewrites labels.
            suffix = python_date[-2:]
            if (
                suffix not in ("AM", "PM")
                or ts_date[-2:] != suffix
                or python_date[:-3] != ts_date[:-3]
                or {python_date[-3:-2], ts_date[-3:-2]} != {" ", "\u202f"}
            ):
                continue
            python_points = [ord(char) for char in python_date]
            ts_points = [ord(char) for char in ts_date]
            if (
                left["codepoints"][entry_index] != python_points
                or right["codepoints"][entry_index] != ts_points
            ):
                continue
            path = f"answers[{answer_index}].body.entries[{entry_index}].label"
            captures.append(
                {
                    "path": path,
                    "python_original": python_label,
                    "typescript_original": ts_label,
                    "python_date_codepoints": python_points,
                    "typescript_date_codepoints": ts_points,
                }
            )
            compared["answers"][answer_index]["body"]["entries"][entry_index][
                "label"
            ] = python_label
            compared["answers"][answer_index]["codepoints"][entry_index] = python_points
    return compared == expected, captures


def seed_rows(conn: sqlite3.Connection) -> str:
    ticket = create_ticket(
        conn,
        title="History proof",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    ).id
    identity = json.dumps(
        [["PLAN_ACTOR", "manager"], ["PLAN_MANAGER_ID", "history-manager"]]
    )
    # One tied timestamp proves the binary ID tie-break. AM and PM remain in the first page.
    for index in range(64):
        conversation = f"history-{index:03}"
        created = 1790035200 + (max(index - 1, 0) * 3600)
        conn.execute(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,"
            "access,created_at,identity_environment_variables) "
            "VALUES(?,'codex','/synthetic','full',?,?)",
            (conversation, created, identity),
        )
        conn.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            (conversation, ticket),
        )
    conn.execute(
        "UPDATE tickets SET conversation_id='history-063' WHERE id=?", (ticket,)
    )
    conn.execute("INSERT INTO agents VALUES('manager_history-manager','history-062')")
    for owner, ids in (
        ("budget", ["a" * 30000, "b" * 30000]),
        ("oversize", ["é" * 17000]),
    ):
        for conversation in ids:
            conn.execute(
                "INSERT INTO conversations("
                "conversation_id,backend_key,workspace_folder,access,created_at) "
                "VALUES(?,'codex','/synthetic','full',1790035200)",
                (conversation,),
            )
            conn.execute(
                "INSERT INTO conversation_history_catalog VALUES('manager',?,?,1790035200)",
                (owner, conversation),
            )
    conn.commit()
    return ticket


def url(kind: str, owner: str, **query: Any) -> str:
    return f"/api/conversation-history/{kind}/{owner}?" + urlencode(query)


def python_answers(path: Path, urls: list[str]) -> dict[str, Any]:
    app = FastAPI()
    app.state.conn_factory = lambda: connect(str(path))
    app.add_exception_handler(PlannerError, handle_planner_error)
    app.include_router(router, prefix="/api")

    def snapshot() -> list[list[tuple[Any, ...]]]:
        with closing(sqlite3.connect(path)) as conn:
            return [
                list(conn.execute(query))
                for query in (
                    "SELECT * FROM conversation_history_catalog "
                    "ORDER BY owner_kind,owner_id,conversation_id",
                    "SELECT * FROM resource_source_versions ORDER BY source_kind,source_id",
                )
            ]

    before = snapshot()
    answers = []
    with TestClient(app) as client:
        for target in urls:
            response = client.get(target)
            body = response.json()
            answers.append(
                {
                    "status": response.status_code,
                    "body": body,
                    "codepoints": [
                        [ord(char) for char in row["label"].split(" · ")[0]]
                        for row in body.get("entries", [])
                    ],
                }
            )
    return {"answers": answers, "unchanged": before == snapshot()}


def test_unit19_history(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        ticket = seed_rows(conn)
    paths = [tmp_path / (side + ".sqlite") for side in ("python", "typescript")]
    for path in paths:
        with (
            closing(sqlite3.connect(seed)) as source,
            closing(sqlite3.connect(path)) as target,
        ):
            source.backup(target)
    urls = [
        url("ticket", ticket),
        url("ticket", ticket, selected="history-061"),
        url("manager", "history-manager", selected="history-063", limit=3),
        url("manager", "absent"),
        url("ticket", "absent"),
        url("chief", "chief"),
        url("unknown", "owner", offset="bad"),
        url("ticket", ticket, offset=-1),
        url("ticket", ticket, limit=61),
        url("ticket", ticket, locale="a" * 81),
        url("ticket", ticket, prefix="x" * 201),
        url("manager", "budget"),
        url("manager", "oversize"),
    ]
    for locale in ("en-US", "de-DE"):
        for zone in ("UTC", "Europe/Berlin"):
            urls.append(url("ticket", ticket, locale=locale, timezone=zone, limit=20))
    urls += [
        url("ticket", ticket, locale="en-US", timezone="GMT+05:30", limit=2),
        url(
            "ticket",
            ticket,
            locale="not-a-real-locale",
            timezone="Unknown/Zone",
            limit=2,
        ),
        url("ticket", ticket, locale="", timezone="", limit=2),
    ]
    with synthetic_home_environment(homes):
        # Derive the real localized search text from Python, then exercise the same wire input.
        preview = python_answers(paths[0], [url("ticket", ticket, limit=2)])
        date_time = preview["answers"][0]["body"]["entries"][0]["label"].split(" · ")[0]
        # Keep the identical request meaningful across the accepted AM/PM space drift.
        prefix = date_time.rsplit(", ", 1)[0]
        urls += [
            url(
                "ticket",
                ticket,
                prefix=prefix.upper(),
                after=61,
                selected="history-061",
                limit=3,
            ),
            url("ticket", ticket, prefix="no matching label", selected="history-061"),
        ]
        expected = python_answers(paths[0], urls)
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; "
                    "import {pathToFileURL} from 'node:url'; "
                    "const {run}=await import(pathToFileURL(process.argv[1]).href); "
                    "process.stdout.write(JSON.stringify("
                    "await run(JSON.parse(readFileSync(0,'utf8')))));",
                    str(ROOT / "server/conversation/history/testing/direct.ts"),
                ],
                cwd=ROOT,
                input=json.dumps({"db_path": str(paths[1]), "urls": urls}),
                text=True,
            )
        )
    equal, captures = compare_history(expected, actual)
    report = {
        "kind": "direct",
        "case": "unit19-history",
        "equal": equal,
        "exact_equal": actual == expected,
        "comparison_rules": [
            {
                "name": LABEL_RULE,
                "request_indices": sorted(EN_US_LABEL_REQUESTS),
                "difference": "one U+202F versus U+0020 immediately before terminal AM or PM",
                "captures": captures,
            }
        ],
        "requests": urls,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit19-history.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    answers = expected["answers"]
    assert answers[0]["body"]["total"] == 63
    assert len(answers[0]["body"]["entries"]) == 60
    assert answers[1]["body"]["active_index"] == 61
    assert answers[1]["body"]["offset"] == 60
    assert answers[2]["body"]["active_index"] == 62
    assert len(answers[11]["body"]["entries"]) == 1
    assert answers[12]["status"] == 400
    assert answers[-2]["body"]["active_index"] == 0
    assert answers[-1]["body"]["active_index"] is None
    assert any("AM" in row["label"] for row in answers[13]["body"]["entries"])
    assert any("PM" in row["label"] for row in answers[13]["body"]["entries"])
    assert expected["unchanged"]
    assert equal
