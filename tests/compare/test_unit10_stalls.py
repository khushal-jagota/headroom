"""Compare the durable checker and separate watchdog, without providers or a server.

Risk/gap: rule tests do not compare finding/heartbeat writes, watchdog episode state,
transport arguments, or summary text against Python. One copied synthetic database
and one Node child exercise that boundary. All send adapters and the Chief are records.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from planner.notifications.contracts import APNsResult, WebPushResult
from planner.stalls.checker import StallChecker
from planner.stalls.contracts import TrackedWait, TrackedWaitKind
from planner.stalls.registry import ProcessWaitRegistry
from planner.stalls.watchdog import WATCHDOG_STATE_FILE_NAME, run_watchdog


class RecordingPush:
    def __init__(self, sends: list[dict[str, Any]], transport: str) -> None:
        self.sends = sends
        self.transport = transport

    def send(self, *args: Any, **options: Any) -> Any:
        if self.transport == "web":
            subscription, intent, identity = args
            self.sends.append(
                {
                    "transport": "web",
                    "subscription": asdict(subscription),
                    "intent": asdict(intent),
                    "identity": asdict(identity),
                    **options,
                }
            )
            return WebPushResult(True)
        self.sends.append({"transport": "apns", "delivery": asdict(args[0]), **options})
        return APNsResult(True)

    def close(self) -> None:
        pass


def test_checker_and_watchdog_match_python(tmp_path: Path) -> None:
    started = time.monotonic()
    seed = tmp_path / "seed.db"
    with sqlite3.connect(seed) as conn:
        conn.executescript("""
            CREATE TABLE stall_findings (
                finding_key TEXT PRIMARY KEY,kind TEXT,conversation_id TEXT,ticket_id TEXT,
                ticket_title TEXT,detail TEXT,stalled_since_ms INTEGER,
                last_seen_at_ms INTEGER,cleared_at_ms INTEGER
            );
            CREATE TABLE stall_checker_heartbeat (
                singleton INTEGER PRIMARY KEY,checked_at_ms INTEGER
            );
            CREATE TABLE tickets (id TEXT,title TEXT,conversation_id TEXT);
            INSERT INTO tickets VALUES ('t_stalltest','Stall proof','conversation-stall-proof');
            CREATE TABLE mac_executor_presence (
                singleton INTEGER,executor_incarnation TEXT,identity_json TEXT,
                last_contact_at_ms INTEGER,last_lease_at_ms INTEGER,continuous_since_ms INTEGER,
                going_to_sleep_at_ms INTEGER,lease_json TEXT,refusal_json TEXT
            );
            CREATE TABLE notification_preferences (
                subject_key TEXT,notification_type TEXT,enabled INTEGER
            );
            INSERT INTO notification_preferences VALUES ('panels','stall_alarm',1);
            CREATE TABLE notification_web_push_identity (
                singleton INTEGER,private_key TEXT,public_key TEXT
            );
            INSERT INTO notification_web_push_identity
                VALUES (1,'synthetic-private','synthetic-public');
            CREATE TABLE notification_push_subscriptions (
                subscription_id TEXT,endpoint TEXT,p256dh TEXT,auth TEXT,
                created_at INTEGER,disabled_at INTEGER
            );
            INSERT INTO notification_push_subscriptions VALUES (
                'subscription-stall-proof','https://push.comparison.invalid/stall',
                'synthetic-p256dh','synthetic-auth',1,NULL
            );
            CREATE TABLE notification_apns_devices (
                device_id TEXT,device_token TEXT,bundle_id TEXT,environment TEXT,
                registration_version INTEGER,updated_at INTEGER,created_at INTEGER
            );
            INSERT INTO notification_apns_devices VALUES (
                'device-stall-proof','0011aaff','invalid.panels.ios','development',2,1,1
            );
        """)
    seed_state = tmp_path / "seed-state"
    seed_state.mkdir()
    (seed_state / WATCHDOG_STATE_FILE_NAME).write_text('{"alarmed":[]}')
    for side in ("python", "typescript"):
        directory = tmp_path / side
        directory.mkdir()
        shutil.copyfile(seed, directory / "test.db")
        shutil.copytree(seed_state, directory / "state")

    base = 1_700_000_000_000
    wait = {
        "kind": "vps_save_retrying",
        "wait_key": "save-proof",
        "conversation_id": "conversation-stall-proof",
        "detail": "stuck at write 1",
        "started_at_ms": base - 120_000,
    }
    steps: list[dict[str, Any]] = [
        {"name": "no-stall", "now_ms": base, "waits": [], "muted": False, "check": True},
        {
            "name": "new-stall",
            "now_ms": base + 1000,
            "waits": [wait],
            "muted": False,
            "check": True,
        },
        {
            "name": "continuing",
            "now_ms": base + 2000,
            "waits": [{**wait, "detail": "stuck at write 2"}],
            "muted": False,
            "check": True,
        },
        {"name": "recovered", "now_ms": base + 3000, "waits": [], "muted": False, "check": True},
        {"name": "muted-new", "now_ms": base + 4000, "waits": [wait], "muted": True, "check": True},
        {
            "name": "muted-continuing",
            "now_ms": base + 5000,
            "waits": [wait],
            "muted": True,
            "check": True,
        },
        {
            "name": "checker-stopped",
            "now_ms": base + 200_000,
            "waits": [],
            "muted": False,
            "check": False,
        },
    ]
    python_database = tmp_path / "python" / "test.db"
    python_state = tmp_path / "python" / "state"
    now_ms = base
    waits: tuple[TrackedWait, ...] = ()

    class Source:
        def tracked_waits(self, _conn: sqlite3.Connection, _now: int) -> tuple[TrackedWait, ...]:
            return waits

    checker = StallChecker(
        str(python_database),
        ProcessWaitRegistry(),
        lambda: (Source(),),
        lambda: now_ms,
    )
    expected = []
    for step in steps:
        now_ms = int(step["now_ms"])
        waits = tuple(
            TrackedWait(**{**value, "kind": TrackedWaitKind(value["kind"])})
            for value in step["waits"]
        )
        with sqlite3.connect(python_database) as conn:
            conn.execute("UPDATE notification_preferences SET enabled=?", (not step["muted"],))
        if step["check"]:
            asyncio.run(checker.check_once())
        sends: list[dict[str, Any]] = []
        chief: list[str] = []

        def accept_chief(text: str, messages: list[str] = chief) -> bool:
            messages.append(text)
            return True

        run = run_watchdog(
            str(python_database),
            python_state,
            now_ms=now_ms,
            web_push=RecordingPush(sends, "web"),
            apns=RecordingPush(sends, "apns"),
            push_origin="https://stalls.comparison.invalid",
            message_chief=accept_chief,
        )
        # Python's CLI has no pure formatter. Preserve its exact expression here;
        # the TS driver calls the same formatter as its real command.
        chief_status = (
            "accepted" if run.chief_accepted else "not reached" if run.raised else "not needed"
        )
        summary = (
            f"{len(run.due)} due, {len(run.raised)} raised, "
            f"{run.pushes_delivered} pushes delivered, Chief {chief_status}"
        )
        with sqlite3.connect(python_database) as conn:
            conn.row_factory = sqlite3.Row
            rows = {
                "findings": [
                    dict(row)
                    for row in conn.execute("SELECT * FROM stall_findings ORDER BY finding_key")
                ],
                "heartbeat": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM stall_checker_heartbeat ORDER BY singleton"
                    )
                ],
            }
        expected.append(
            {
                "name": step["name"],
                "run": asdict(run),
                "summary": summary,
                "sends": sends,
                "chief": chief,
                "state": json.loads((python_state / WATCHDOG_STATE_FILE_NAME).read_text()),
                "rows": rows,
            }
        )
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        ["node", str(root / "server/stalls/testing_compare.ts")],
        input=json.dumps(
            {
                "db_path": str(tmp_path / "typescript" / "test.db"),
                "state_dir": str(tmp_path / "typescript" / "state"),
                "steps": steps,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        cwd=root,
        env={key: value for key, value in os.environ.items() if not key.startswith("PLAN_")},
    )
    actual = json.loads(completed.stdout)
    report = {"seconds": time.monotonic() - started, "passes": actual}
    report_path = tmp_path / "unit10-stalls-comparison.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    assert actual == json.loads(json.dumps(expected))
    print(
        json.dumps(
            {"seconds": report["seconds"], "phases": len(actual), "report": str(report_path)}
        )
    )
