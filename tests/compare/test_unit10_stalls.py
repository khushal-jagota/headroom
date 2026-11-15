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


def test_named_machine_stall_aging_matches_python(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """One existing checker host proves routing and separate present-time histories."""
    started = time.monotonic()
    original = "ee17b608-a449-4966-a808-a791ba7df1c7"
    extra = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    seed = tmp_path / "named-seed.db"
    with sqlite3.connect(seed) as conn:
        conn.executescript("""
            CREATE TABLE stall_findings (
                finding_key TEXT PRIMARY KEY,kind TEXT,conversation_id TEXT,ticket_id TEXT,
                ticket_title TEXT,detail TEXT,stalled_since_ms INTEGER,
                last_seen_at_ms INTEGER,cleared_at_ms INTEGER
            );
            CREATE TABLE stall_checker_heartbeat(
                singleton INTEGER PRIMARY KEY,checked_at_ms INTEGER
            );
            CREATE TABLE tickets(id TEXT,title TEXT,conversation_id TEXT,machine_id TEXT);
            CREATE TABLE mac_executor_presence (
                machine_id TEXT PRIMARY KEY,executor_incarnation TEXT,identity_json TEXT,
                last_contact_at_ms INTEGER,last_lease_at_ms INTEGER,continuous_since_ms INTEGER,
                going_to_sleep_at_ms INTEGER,lease_json TEXT,refusal_json TEXT
            );
            CREATE TABLE executor_incarnations(executor_incarnation TEXT,machine_id TEXT);
            CREATE TABLE executor_child_assignments(executor_incarnation TEXT,conversation_id TEXT);
        """)
        conn.execute(
            "INSERT INTO tickets VALUES('named-ticket','Named machine','ticket-conversation',?)",
            (extra,),
        )
        conn.executemany(
            "INSERT INTO mac_executor_presence(machine_id) VALUES(?)", [(original,), (extra,)]
        )
        conn.execute("INSERT INTO executor_incarnations VALUES('assigned-executor',?)", (extra,))
        conn.execute(
            "INSERT INTO executor_child_assignments VALUES(?,?)",
            ("assigned-executor", "assigned-conversation"),
        )
    paths = {side: tmp_path / f"named-{side}.db" for side in ("python", "typescript")}
    for path in paths.values():
        shutil.copyfile(seed, path)
    base = 1_700_000_000_000
    waits: list[dict[str, Any]] = [
        {
            "kind": "mac_command_unfetched",
            "wait_key": key,
            "conversation_id": conversation,
            "detail": key,
            "started_at_ms": base,
            "machine_id": machine,
        }
        for key, conversation, machine in (
            ("ticket", "ticket-conversation", None),
            ("assigned", "assigned-conversation", None),
            ("override", "ticket-conversation", original),
            ("waiting", None, extra),
            ("waiting", None, None),
        )
    ]

    def presence(machine: str, since: int, now: int) -> dict[str, Any]:
        return {
            "machine_id": machine,
            "last_contact_at_ms": base + now,
            "last_lease_at_ms": base + now,
            "continuous_since_ms": base + since,
        }

    steps: list[dict[str, Any]] = [
        {
            "name": "extra-starts",
            "now_ms": base,
            "waits": waits,
            "presence": [presence(extra, 0, 0)],
        },
        {
            "name": "extra-overdue",
            "now_ms": base + 120_000,
            "waits": waits,
            "presence": [presence(extra, 0, 120_000)],
        },
        {
            "name": "restart-new-stretch",
            "now_ms": base + 600_000,
            "waits": waits,
            "restart": True,
            "presence": [presence(extra, 600_000, 600_000)],
        },
        {
            "name": "original-overdue",
            "now_ms": base + 720_000,
            "waits": waits,
            "presence": [presence(original, 600_000, 720_000)],
        },
        {"name": "clear", "now_ms": base + 720_001, "waits": [], "presence": []},
    ]
    now_ms = base
    tracked: tuple[TrackedWait, ...] = ()

    class Source:
        def tracked_waits(self, _conn: sqlite3.Connection, _now: int) -> tuple[TrackedWait, ...]:
            return tracked

    def checker() -> StallChecker:
        return StallChecker(
            str(paths["python"]), ProcessWaitRegistry(), lambda: (Source(),), lambda: now_ms
        )

    current = checker()
    expected = []
    for step in steps:
        now_ms = int(step["now_ms"])
        tracked = tuple(
            TrackedWait(**{**value, "kind": TrackedWaitKind(value["kind"])})
            for value in step["waits"]
        )
        if step.get("restart"):
            asyncio.run(current.close())
            current = checker()
        with sqlite3.connect(paths["python"]) as conn:
            for record in step["presence"]:
                conn.execute(
                    "UPDATE mac_executor_presence SET last_contact_at_ms=?,last_lease_at_ms=?,"
                    "continuous_since_ms=? WHERE machine_id=?",
                    (
                        record["last_contact_at_ms"],
                        record["last_lease_at_ms"],
                        record["continuous_since_ms"],
                        record["machine_id"],
                    ),
                )
        asyncio.run(current.check_once())
        with sqlite3.connect(paths["python"]) as conn:
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
        expected.append({"name": step["name"], "rows": rows})
    asyncio.run(current.close())
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        ["node", str(root / "server/stalls/testing_compare.ts")],
        input=json.dumps(
            {
                "db_path": str(paths["typescript"]),
                "state_dir": str(tmp_path),
                "checker_only": True,
                "steps": [{**step, "check": True, "muted": True} for step in steps],
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        cwd=root,
        env={key: value for key, value in os.environ.items() if not key.startswith("PLAN_")},
    )
    actual = json.loads(completed.stdout)
    report = {
        "seconds": time.monotonic() - started,
        "python": expected,
        "typescript": actual,
        "boundary": (
            "Real SQLite checker, five populated waits, two machine histories, existing Node host; "
            "no watchdog or notification sends"
        ),
    }
    destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "named-machine-stalls.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
    keys = [
        {row["finding_key"] for row in item["rows"]["findings"] if row["cleared_at_ms"] is None}
        for item in actual
    ]
    extra_keys = {
        "mac_command_unfetched:ticket",
        "mac_command_unfetched:assigned",
        f"mac_command_unfetched:machine={extra}:waiting",
    }
    assert keys == [
        set(),
        extra_keys,
        extra_keys,
        extra_keys | {"mac_command_unfetched:override", "mac_command_unfetched:waiting"},
        set(),
    ]
    assert all(
        row["stalled_since_ms"] == base for item in actual for row in item["rows"]["findings"]
    )


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
            CREATE TABLE tickets (id TEXT,title TEXT,conversation_id TEXT,
                machine_id TEXT DEFAULT 'ee17b608-a449-4966-a808-a791ba7df1c7');
            INSERT INTO tickets(id,title,conversation_id)
                VALUES ('t_stalltest','Stall proof','conversation-stall-proof');
            CREATE TABLE mac_executor_presence (
                machine_id TEXT,executor_incarnation TEXT,identity_json TEXT,
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
