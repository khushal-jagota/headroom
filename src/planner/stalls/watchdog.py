"""`panels stalls watchdog`: one run of the separate unit that raises stall alarms.

A systemd timer runs it every WATCHDOG_INTERVAL_SECONDS, outside the server, so a stuck
server can still alarm. Each run:

1. opens the database read-only and reads the open stall findings, the checker's
   heartbeat, the stall_alarm preference and the push targets;
2. alarms on each open finding, and on a heartbeat older than
   CHECKER_HEARTBEAT_STALE_SECONDS (the checker, or the whole server, has stopped);
3. alarms only on keys its state file has not recorded, so each stall episode alarms
   once. A key that is no longer due leaves the file, so a stall that comes back alarms
   again;
4. pushes (when the preference allows) and messages the Chief. The keys are recorded
   once either channel took the alarm; if both failed, the next run tries again.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from planner.notifications import data as notifications_data
from planner.notifications.apns import APNsAdapter
from planner.notifications.contracts import (
    PANELS_NOTIFICATION_SUBJECT_KEY,
    STALL_ALARM_NOTIFICATION_TYPE,
    APNsDevice,
    PushSubscription,
)
from planner.notifications.web_push import WebPushAdapter
from planner.stalls import configuration
from planner.stalls.alarms import (
    PushTargets,
    StallAlarm,
    push_intent,
    send_push,
    since_utc,
    stall_text,
)
from planner.stalls.checker import read_open_findings
from planner.stalls.contracts import StallFinding

WATCHDOG_STATE_FILE_NAME: Final = "stall-watchdog-state.json"
_READ_BUSY_TIMEOUT_SECONDS: Final = 5

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WatchdogReading:
    findings: tuple[StallFinding, ...]
    heartbeat_at_ms: int | None
    push_enabled: bool
    push_targets: PushTargets


@dataclass(frozen=True, slots=True)
class WatchdogRun:
    """due: every alarm due now; raised: the new ones this run tried to send."""

    due: tuple[StallAlarm, ...]
    raised: tuple[StallAlarm, ...]
    pushes_delivered: int
    chief_accepted: bool


def read_database(db_path: str) -> WatchdogReading:
    """One read-only snapshot. The connection cannot write: mode=ro and query_only."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(
        sqlite3.connect(uri, uri=True, isolation_level=None, timeout=_READ_BUSY_TIMEOUT_SECONDS)
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        try:
            heartbeat = conn.execute(
                "SELECT checked_at_ms FROM stall_checker_heartbeat WHERE singleton=1"
            ).fetchone()
            preferences = notifications_data.resolved_preferences(conn)
            try:
                identity = notifications_data.read_web_push_identity(conn)
            except RuntimeError:
                identity = None
            return WatchdogReading(
                findings=read_open_findings(conn),
                heartbeat_at_ms=None if heartbeat is None else int(heartbeat[0]),
                push_enabled=preferences[
                    (PANELS_NOTIFICATION_SUBJECT_KEY, STALL_ALARM_NOTIFICATION_TYPE)
                ],
                push_targets=PushTargets(
                    web_push_identity=identity,
                    subscriptions=_subscriptions(conn),
                    apns_devices=_apns_devices(conn),
                ),
            )
        finally:
            conn.execute("ROLLBACK")


def _subscriptions(conn: sqlite3.Connection) -> tuple[PushSubscription, ...]:
    return tuple(
        PushSubscription(
            str(row["subscription_id"]), str(row["endpoint"]), str(row["p256dh"]), str(row["auth"])
        )
        for row in conn.execute(
            "SELECT subscription_id,endpoint,p256dh,auth FROM notification_push_subscriptions "
            "WHERE disabled_at IS NULL ORDER BY created_at,subscription_id"
        )
    )


def _apns_devices(conn: sqlite3.Connection) -> tuple[APNsDevice, ...]:
    return tuple(
        APNsDevice(
            str(row["device_id"]),
            str(row["device_token"]),
            str(row["bundle_id"]),
            cast(Literal["development", "production"], row["environment"]),
            int(row["registration_version"]),
            int(row["updated_at"]),
        )
        for row in conn.execute(
            "SELECT device_id,device_token,bundle_id,environment,registration_version,"
            "updated_at FROM notification_apns_devices ORDER BY created_at,device_id"
        )
    )


def due_alarms(reading: WatchdogReading, now_ms: int) -> tuple[StallAlarm, ...]:
    alarms: list[StallAlarm] = []
    heartbeat = reading.heartbeat_at_ms
    if (
        heartbeat is None
        or now_ms - heartbeat > configuration.CHECKER_HEARTBEAT_STALE_SECONDS * 1000
    ):
        stopped = (
            "has never run"
            if heartbeat is None
            else f"has not run since {since_utc(heartbeat)} UTC"
        )
        alarms.append(
            StallAlarm(
                alarm_key=f"checker_heartbeat_stale:{heartbeat}",
                text=(
                    f"Panels: the stall checker {stopped}, so nothing is checked for stalls. "
                    "The Panels server may be stuck or stopped."
                ),
                ticket_id=None,
            )
        )
    for finding in reading.findings:
        alarms.append(
            StallAlarm(
                # A finding that clears and comes back is due again, and alarms again.
                alarm_key=finding.finding_key,
                text=stall_text(
                    kind=finding.kind,
                    conversation_id=finding.conversation_id,
                    ticket_title=finding.ticket_title,
                    detail=finding.detail,
                    stalled_since_ms=finding.stalled_since_ms,
                ),
                ticket_id=finding.ticket_id,
            )
        )
    return tuple(alarms)


def run_watchdog(
    db_path: str,
    state_dir: Path,
    *,
    now_ms: int,
    web_push: WebPushAdapter,
    apns: APNsAdapter | None,
    push_origin: str | None,
    message_chief: Callable[[str], bool],
) -> WatchdogRun:
    reading = read_database(db_path)
    due = due_alarms(reading, now_ms)
    state_path = state_dir / WATCHDOG_STATE_FILE_NAME
    already_alarmed = _read_state(state_path)
    due_keys = {alarm.alarm_key for alarm in due}
    raised = tuple(alarm for alarm in due if alarm.alarm_key not in already_alarmed)
    recorded = already_alarmed & due_keys
    pushes_delivered = 0
    chief_accepted = False
    if raised:
        if reading.push_enabled:
            try:
                pushes_delivered = send_push(
                    push_intent(raised),
                    reading.push_targets,
                    web_push=web_push,
                    apns=apns,
                    origin=push_origin,
                    now_s=now_ms // 1000,
                )
            except Exception:
                _LOG.exception("stall alarm push failed")
        try:
            chief_accepted = message_chief("\n".join(alarm.text for alarm in raised))
        except Exception:
            _LOG.exception("stall alarm Chief message failed")
        if pushes_delivered or chief_accepted:
            recorded |= {alarm.alarm_key for alarm in raised}
    if recorded != already_alarmed:
        _write_state(state_path, recorded)
    return WatchdogRun(due, raised, pushes_delivered, chief_accepted)


def _read_state(path: Path) -> frozenset[str]:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return frozenset()
    except (OSError, ValueError):
        # Losing the record costs at most one repeated alarm per open stall.
        _LOG.exception("stall watchdog state unreadable; starting again")
        return frozenset()
    keys = stored.get("alarmed") if isinstance(stored, dict) else None
    return frozenset(key for key in keys or () if isinstance(key, str))


def _write_state(path: Path, alarmed: set[str] | frozenset[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"alarmed": sorted(alarmed)}), encoding="utf-8")
    os.replace(temporary, path)
