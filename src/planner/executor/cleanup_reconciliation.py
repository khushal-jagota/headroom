"""Per-source cleanup observations, including a conservative legacy handoff."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from planner.execution_identity import ChildReference
from planner.executor.configuration import ExecutorPaths
from planner.executor.legacy_worker_cleanup import RECEIPT, legacy_notice
from planner.executor.protocol import (
    ChildOperation,
    CommandDisposition,
    CommandResult,
    MailboxCommand,
    StartRun,
    WorkerCleanupUncertainty,
    type_adapter,
)
from planner.executor.runtime_bootstrap import read_record
from planner.worker_supervision import cleanup_uncertainties, records


@dataclass(frozen=True)
class ExecutorCleanupSnapshot:
    uncertainties: tuple[WorkerCleanupUncertainty, ...]
    sources: dict[ChildReference, str]
    notice: str | None


def _sources(paths: ExecutorPaths) -> dict[ChildReference, str]:
    sources: dict[ChildReference, str] = {}
    journal = paths.spool / "commands.sqlite3"
    if journal.exists():
        with contextlib.closing(sqlite3.connect(
            f"{journal.absolute().as_uri()}?mode=ro", uri=True,
        )) as conn:
            for (raw,) in conn.execute(
                "SELECT mailbox_command_json FROM commands WHERE length(mailbox_command_json)>0"
            ):
                command = type_adapter(MailboxCommand).validate_json(raw).child_command
                if command is not None and isinstance(command.arguments, StartRun):
                    materials = command.arguments.resolved_start.role_materials
                    if materials is not None:
                        ticket_id = dict(materials.identity_environment_variables).get(
                            "PLAN_TICKET_ID"
                        )
                        if ticket_id:
                            sources[command.child] = ticket_id
    for path in (paths.run / "worker-processes").glob("*.json"):
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            record = records.read(path)
            if all(record.get(key) for key in (
                "source_child_id", "source_incarnation", "conversation_id", "ticket_id"
            )):
                sources[ChildReference(
                    record["source_incarnation"], record["source_child_id"],
                    record["conversation_id"]
                )] = record["ticket_id"]
    return sources


def _stopped_sources(paths: ExecutorPaths) -> set[ChildReference]:
    proven: set[ChildReference] = set()
    journal = paths.spool / "commands.sqlite3"
    if not journal.exists():
        return proven
    with contextlib.closing(sqlite3.connect(
        f"{journal.absolute().as_uri()}?mode=ro", uri=True,
    )) as conn:
        for raw, result in conn.execute(
            "SELECT mailbox_command_json,result_json FROM commands "
            "WHERE length(mailbox_command_json)>0 AND result_json IS NOT NULL"
        ):
            command = type_adapter(MailboxCommand).validate_json(raw).child_command
            outcome = type_adapter(CommandResult).validate_json(result).child_outcome
            if command is not None and outcome is not None:
                stopped = (command.operation is ChildOperation.stop
                           and outcome.disposition is CommandDisposition.succeeded)
                never_started = (command.operation is ChildOperation.start
                                 and outcome.disposition is CommandDisposition.refused)
                if stopped or never_started:
                    proven.add(command.child)
    return proven


def _record_source(path: Path) -> ChildReference | None:
    try:
        record = records.read(path)
        if all(record.get(key) for key in (
            "source_incarnation", "source_child_id", "conversation_id",
        )):
            return ChildReference(
                record["source_incarnation"], record["source_child_id"], record["conversation_id"],
            )
    except (OSError, ValueError, TypeError):
        pass
    return None


def _modern_sources(paths: ExecutorPaths) -> set[ChildReference]:
    modern: set[ChildReference] = set()
    journal = paths.spool / "commands.sqlite3"
    if not journal.exists():
        return modern
    with contextlib.closing(sqlite3.connect(
        f"{journal.absolute().as_uri()}?mode=ro", uri=True,
    )) as conn:
        for (raw,) in conn.execute(
            "SELECT mailbox_command_json FROM commands WHERE length(mailbox_command_json)>0"
        ):
            body = json.loads(raw)
            child = body.get("child_command")
            if (isinstance(child, dict)
                    and child.get("operation") == "start"
                    and child.get("arguments", {}).get("supervision_protocol") == 24):
                command = type_adapter(MailboxCommand).validate_json(raw).child_command
                if command is not None:
                    modern.add(command.child)
    return modern


def snapshot_cleanup(
    paths: ExecutorPaths, *, current_incarnation: str, revoke_pending: bool = False,
    healthy_sources: frozenset[ChildReference] = frozenset(),
) -> ExecutorCleanupSnapshot:
    """Observation can clear an old fence. It never signals an ambiguous legacy PID."""
    notices: list[str] = []
    try:
        sources = _sources(paths)
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        sources = {}
        notices.append(f"Worker source evidence is unavailable: {error}")
    try:
        modern = _modern_sources(paths)
    except (OSError, ValueError, sqlite3.DatabaseError):
        modern = set(sources)
    found: dict[tuple[str, str], WorkerCleanupUncertainty] = {}
    observed_records = {
        source for path in (paths.run / "worker-processes").glob("*.json")
        if (source := _record_source(path)) is not None
    }
    for uncertain in cleanup_uncertainties(
        paths.run / "worker-processes", revoke_pending=revoke_pending,
    ):
        if (uncertain.reason == "Recorded Worker processes remain"
                and _record_source(uncertain.record_path) in healthy_sources):
            continue
        if uncertain.ticket_id is not None and uncertain.conversation_id is not None:
            key = (uncertain.ticket_id, uncertain.conversation_id)
            found[key] = WorkerCleanupUncertainty(*key, uncertain.reason)
        elif uncertain.ticket_id is not None:
            candidates = [s for s, ticket in sources.items() if ticket == uncertain.ticket_id]
            for source in candidates:
                key = (uncertain.ticket_id, source.conversation_id)
                found[key] = WorkerCleanupUncertainty(*key, uncertain.reason)
            if not candidates:
                key = (uncertain.ticket_id, "")
                found[key] = WorkerCleanupUncertainty(*key, uncertain.reason)
                notices.append(uncertain.reason)
        else:
            notices.append(uncertain.reason)
            for source, ticket_id in sources.items():
                if source in modern:
                    key = (ticket_id, source.conversation_id)
                    found[key] = WorkerCleanupUncertainty(*key, uncertain.reason)
    legacy_attempted = False
    try:
        receipt = read_record(paths.run / RECEIPT)
        legacy_attempted = (receipt is not None and receipt.get("version") == 1
                            and receipt.get("attempted") is True)
        notice = legacy_notice(paths.run)
        if notice is not None:
            notices.append(notice)
        modern = _modern_sources(paths)
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        notices.append(str(error))
        modern = set(sources)
    try:
        stopped = _stopped_sources(paths)
    except (OSError, ValueError, sqlite3.DatabaseError) as error:
        stopped = set()
        notices.append(f"Worker stop evidence is unavailable: {error}")
    for source, ticket_id in sources.items():
        if (source in healthy_sources or source in observed_records or source in stopped
                or (source not in modern and legacy_attempted)):
            continue
        key = (ticket_id, source.conversation_id)
        found[key] = WorkerCleanupUncertainty(
            *key, "Exact Worker cleanup evidence is missing. "
            "Recheck after source evidence is restored.",
        )
    return ExecutorCleanupSnapshot(
        tuple(found[key] for key in sorted(found)), sources,
        " ".join(dict.fromkeys(notices)) if notices else None,
    )
