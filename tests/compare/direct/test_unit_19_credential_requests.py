"""Durable credential result receipts survive queue races and process loss.

HTTP creates and delivers requests, but cannot plant a lost intent or an obsolete
attempt beside a newer card. This direct case compares the existing transaction
participants on one tiny synthetic SQLite seed. It needs no listener or provider.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.conversation.events import (
    ConversationEventKind,
    ConversationEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_to_canonical_json,
)
from planner.core.db import connect, create_schema
from planner.credentials.requests import data
from planner.credentials.requests.contracts import CredentialRequestRowBody

ROOT = Path(__file__).resolve().parents[3]
NOW = 200
CONVERSATION = "credential-origin"


def event(kind: str, sender: str, reason: str | None = None) -> dict[str, str]:
    raw: dict[str, Any] = {
        "text": "Synthetic credential result.",
        "sender_label": "Panels credential request result",
        "mode": "steer",
        "sender_message_id": sender,
    }
    if reason is not None:
        raw.update(refusal_reason=reason, refusal_detail=None)
    payload = conversation_event_payload_from_canonical_json(
        ConversationEventKind(kind), json.dumps(raw)
    )
    return {
        "kind": kind,
        "payload": conversation_event_payload_to_canonical_json(payload),
    }


def event_json(payload: ConversationEventPayload) -> dict[str, str]:
    return {
        "kind": str(payload.kind),
        "payload": conversation_event_payload_to_canonical_json(payload),
    }


def seed_rows(conn: sqlite3.Connection) -> None:
    for conversation_id in (CONVERSATION, "different-origin"):
        conn.execute(
            "INSERT INTO conversations("
            "conversation_id,backend_key,workspace_folder,access,created_at) "
            "VALUES (?,'codex','/synthetic','full',0)",
            (conversation_id,),
        )
    cases = {
        "observed": "intent",
        "write-failed": "intent",
        "discarded": "intent",
        "refused": "intent",
        "latest": "queued",
        "lost": "intent",
        "startup-write-failed": "queued",
        "startup-discard": "intent",
        "startup-refused": "queued",
        "rotated-new": "intent",
    }
    for name, status in cases.items():
        request_id = "request:" + name
        attempt_id = "attempt:" + name
        metadata: dict[str, Any] = {
            "request_id": request_id,
            "name": "Synthetic API key",
            "provider": "Synthetic",
            "use": "Resume the synthetic task",
            "auth_kind": "bearer",
            "header_name": None,
            "help": None,
            "help_url": None,
            "replacement": None,
        }
        body = cast(
            CredentialRequestRowBody,
            {
                "kind": "credential_request",
                **metadata,
                "state": "rejected",
                "credential": None,
                "delivery": {
                    "attempt_id": attempt_id,
                    "status": "pending",
                    "refusal_reason": None,
                },
            },
        )
        conn.execute(
            "INSERT INTO credential_requests VALUES (?,?,?,?,?,?,?,?)",
            (
                request_id,
                CONVERSATION,
                "chief",
                "chief",
                data.encoded(metadata),
                data.encoded(body),
                100,
                100,
            ),
        )
        conn.execute(
            "INSERT INTO credential_request_delivery_attempts VALUES (?,?,?,?,?,?,?)",
            (
                attempt_id,
                request_id,
                "sender:" + name,
                "Synthetic credential result.",
                status,
                None,
                100,
            ),
        )
    conn.execute(
        "INSERT INTO credential_request_delivery_attempts VALUES (?,?,?,?,?,?,?)",
        (
            "attempt:rotated-old",
            "request:rotated-new",
            "sender:rotated-old",
            "Obsolete result.",
            "intent",
            None,
            99,
        ),
    )
    receipts = [
        event("prompt_delivery_refused", "sender:latest", "backend_cannot_steer"),
        event("prompt", "sender:latest"),
        event(
            "prompt_delivery_refused",
            "sender:startup-write-failed",
            "write_to_backend_failed",
        ),
        event("prompt_discarded", "sender:startup-discard"),
        event(
            "prompt_delivery_refused",
            "sender:startup-refused",
            "backend_rejected_prompt",
        ),
    ]
    for sequence, receipt in enumerate(receipts, 1):
        conn.execute(
            "INSERT INTO conversation_events VALUES (?,?,?,?,?)",
            (
                CONVERSATION,
                sequence,
                receipt["kind"],
                receipt["payload"],
                100 + sequence,
            ),
        )


def operations() -> list[dict[str, Any]]:
    started = event("prompt", "sender:observed")
    return [
        {
            "name": "other conversation cannot settle",
            "op": "observe",
            "conversation_id": "different-origin",
            "events": [started],
        },
        {
            "name": "exact prompt receipt",
            "op": "observe",
            "conversation_id": CONVERSATION,
            "events": [started],
        },
        {
            "name": "late queued response",
            "op": "settle",
            "attempt_id": "attempt:observed",
            "status": "queued",
        },
        {
            "name": "duplicate prompt receipt",
            "op": "observe",
            "conversation_id": CONVERSATION,
            "events": [started],
        },
        {
            "name": "write failure is uncertain",
            "op": "observe",
            "conversation_id": CONVERSATION,
            "events": [
                event(
                    "prompt_delivery_refused",
                    "sender:write-failed",
                    "write_to_backend_failed",
                )
            ],
        },
        {
            "name": "discard cancels result",
            "op": "observe",
            "conversation_id": CONVERSATION,
            "events": [event("prompt_discarded", "sender:discarded")],
        },
        {
            "name": "safe refusal permits retry",
            "op": "observe",
            "conversation_id": CONVERSATION,
            "events": [
                event(
                    "prompt_delivery_refused", "sender:refused", "backend_cannot_steer"
                )
            ],
        },
        {
            "name": "obsolete attempt cannot replace card",
            "op": "settle",
            "attempt_id": "attempt:rotated-old",
            "status": "started",
        },
        {"name": "restart resolves exact durable receipts", "op": "reconcile"},
        {"name": "second restart is quiet", "op": "reconcile"},
        {
            "name": "late startup queued response",
            "op": "settle",
            "attempt_id": "attempt:latest",
            "status": "queued",
        },
    ]


def python_answers(path: Path, steps: list[dict[str, Any]]) -> dict[str, Any]:
    answers: list[dict[str, Any]] = []
    with closing(connect(str(path))) as conn:
        conn.create_function("unixepoch", 0, lambda: NOW)
        for step in steps:
            conn.execute("BEGIN IMMEDIATE")
            if step["op"] == "observe":
                payloads = tuple(
                    conversation_event_payload_from_canonical_json(
                        ConversationEventKind(value["kind"]),
                        value["payload"],
                    )
                    for value in step["events"]
                )
                result: Any = [
                    event_json(payload)
                    for payload in data.observe_events(
                        conn, step["conversation_id"], payloads
                    )
                ]
            elif step["op"] == "settle":
                payload = data.settle_attempt(
                    conn, step["attempt_id"], step["status"], step.get("reason"), NOW
                )
                result = None if payload is None else event_json(payload)
            else:
                result = {
                    key: [event_json(payload) for payload in payloads]
                    for key, payloads in data.reconcile_deliveries(conn).items()
                }
            conn.commit()
            answers.append(
                {
                    "name": step["name"],
                    "result": result,
                    "requests": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM credential_requests ORDER BY request_id"
                        )
                    ],
                    "attempts": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM credential_request_delivery_attempts ORDER BY attempt_id"
                        )
                    ],
                }
            )
    return {"answers": answers}


def private_body_cases() -> list[dict[str, str]]:
    body = json.dumps({"name": "café 😀", "expected_revision": 1.0}, ensure_ascii=False)
    encoded = [
        ("utf8", body.encode("utf-8")),
        ("utf8-bom", b"\xef\xbb\xbf" + body.encode("utf-8")),
        ("utf16le-bom", b"\xff\xfe" + body.encode("utf-16-le")),
        ("utf16be-bom", b"\xfe\xff" + body.encode("utf-16-be")),
        ("utf32le-bom", b"\xff\xfe\x00\x00" + body.encode("utf-32-le")),
        ("utf32be-bom", b"\x00\x00\xfe\xff" + body.encode("utf-32-be")),
        ("utf16-inferred", body.encode("utf-16-le")),
        ("utf32-inferred", body.encode("utf-32-be")),
        ("malformed-utf8", b'{"name":"\xff"}'),
        ("truncated-utf16", b"\xff\xfe" + body.encode("utf-16-le")[:-1]),
    ]
    return [{"name": name, "hex": value.hex()} for name, value in encoded]


def private_body_answers(cases: list[dict[str, str]]) -> list[dict[str, Any]]:
    answers: list[dict[str, Any]] = []
    for case in cases:
        try:
            value = json.loads(bytes.fromhex(case["hex"]))
        except (ValueError, UnicodeError):
            answers.append({"name": case["name"], "error": "invalid credential input"})
        else:
            assert isinstance(value, dict)
            answers.append(
                {
                    "name": case["name"],
                    "value": value,
                    "revision_kind": (
                        "float"
                        if type(value["expected_revision"]) is float
                        else "integer"
                    ),
                }
            )
    return answers


def test_unit19_credential_receipt_and_restart_state(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        seed_rows(conn)
        conn.commit()
    paths = [tmp_path / f"{side}.sqlite" for side in ("python", "typescript")]
    for path in paths:
        with (
            closing(sqlite3.connect(seed)) as source,
            closing(sqlite3.connect(path)) as target,
        ):
            source.backup(target)
    steps = operations()
    private_bodies = private_body_cases()
    with synthetic_home_environment(homes):
        expected = python_answers(paths[0], steps)
        expected["private_bodies"] = private_body_answers(private_bodies)
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; "
                    "import {pathToFileURL} from 'node:url'; "
                    "const {run}=await import(pathToFileURL(process.argv[1]).href); "
                    "const spec=JSON.parse(readFileSync(0,'utf8')); "
                    "process.stdout.write(JSON.stringify(run(spec)));",
                    str(ROOT / "server/credentials/requests/testing/direct.ts"),
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "db_path": str(paths[1]),
                        "now": NOW,
                        "operations": steps,
                        "private_bodies": private_bodies,
                    }
                ),
                text=True,
            )
        )
    report = {
        "kind": "direct",
        "case": "unit19-credential-receipts",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit19-credential-receipts.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    answers = expected["answers"]
    assert answers[0]["result"] == []
    assert answers[2]["result"] is None
    assert answers[3]["result"] == []
    assert answers[7]["result"] is None
    assert len(answers[8]["result"][CONVERSATION]) == 6
    assert answers[9]["result"] == {}
    final = {row["attempt_id"]: row for row in answers[-1]["attempts"]}
    assert final["attempt:latest"]["status"] == "started"
    assert final["attempt:lost"]["status"] == "uncertain"
    assert final["attempt:startup-write-failed"]["status"] == "uncertain"
    assert final["attempt:startup-discard"]["status"] == "canceled"
    assert actual == expected
