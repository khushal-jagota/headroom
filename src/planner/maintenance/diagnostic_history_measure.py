"""Private-copy measurements and an independent, raw-row retention oracle.

This module never prepares or writes the source database. It imports no retention
classifier. The deliberately separate oracle gives the destructive writer a second
implementation to disagree with before anyone considers a live operation.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

EventKey = tuple[str, int]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f"SELECT * FROM {_quote(table)}")
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _object(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


_RAW_PAYLOAD_FIELDS = {
    "prompt": {
        "text",
        "content",
        "sender_label",
        "mode",
        "sender_message_id",
        "sent_at_unix_milliseconds",
        "sender",
        "recipient",
        "reconciles_sequence",
    },
    "prompt_delivery_refused": {
        "text",
        "content",
        "sender_label",
        "mode",
        "sender_message_id",
        "sent_at_unix_milliseconds",
        "sender",
        "recipient",
        "refusal_reason",
        "refusal_detail",
    },
    "prompt_delivery_uncertain": {
        "text",
        "content",
        "sender_label",
        "mode",
        "sender_message_id",
        "sent_at_unix_milliseconds",
        "sender",
        "recipient",
    },
    "prompt_discarded": {
        "text",
        "content",
        "sender_label",
        "sender_message_id",
        "sent_at_unix_milliseconds",
        "sender",
        "recipient",
    },
    "message_to_owner": {
        "text",
        "content",
        "sender_label",
        "sender_message_id",
        "sent_at_unix_milliseconds",
        "sender",
        "recipient",
    },
    "agent_message": {"text", "content", "markdown"},
    "explicit_reply_missing": {"prompt_sender"},
    "tool_call_started": {"tool_call_id", "title", "tool_kind", "detail"},
    "tool_call_finished": {"tool_call_id", "tool_call_status", "detail"},
    "permission_asked": {"ask_id", "title", "detail", "options"},
    "permission_answered": {"ask_id", "option_id"},
    "user_input_requested": {"request_id", "questions"},
    "user_input_answered": {"request_id", "answers"},
    "user_input_failed": {"request_id", "detail"},
    "plan_updated": {"entries"},
    "model_changed": {"model", "reasoning_effort"},
    "token_usage": {"input_tokens", "output_tokens", "cached_input_tokens", "cost_usd"},
    "context_compacted": set(),
    "turn_ended": {"ending", "error_summary", "automatic_compaction_result"},
}


def independent_removal_oracle(connection: sqlite3.Connection, cutoff_unix: int) -> set[EventKey]:
    """Calculate permitted keys directly from raw archive tables, without policy imports."""
    tickets = {row["id"]: row for row in _rows(connection, "tickets")}
    managers = {row["id"]: row for row in _rows(connection, "managers")}
    ownership: dict[str, set[str]] = {}
    for ticket in tickets.values():
        if ticket["conversation_id"]:
            ownership.setdefault(ticket["conversation_id"], set()).add(ticket["id"])
    for link in _rows(connection, "ticket_conversations"):
        ownership.setdefault(link["conversation_id"], set()).add(link["ticket_id"])
    agents: dict[str, set[str]] = {}
    for row in _rows(connection, "agents"):
        if row["conversation_id"]:
            agents.setdefault(row["conversation_id"], set()).add(row["agent_key"])
    acknowledgements = {
        row["conversation_id"]: row["through_sequence"]
        for row in _rows(connection, "conversation_error_acknowledgements")
    }
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    unresolved_native: set[str] = set()
    if "mac_runs" in tables:
        unresolved_native.update(
            row["conversation_id"]
            for row in _rows(connection, "mac_runs")
            if row["stop_state"] != "closed"
        )
    result: set[EventKey] = set()
    for conversation in _rows(connection, "conversations"):
        identity = conversation["conversation_id"]
        if identity in unresolved_native:
            continue
        try:
            pairs = json.loads(conversation["identity_environment_variables"])
            environment = dict(pairs)
            if len(environment) != len(pairs):
                continue
            legacy_id = environment.pop("PLAN_SPRINT_ITEM_ID", None)
            if legacy_id:
                if environment.get("PLAN_MANAGER_ID", legacy_id) != legacy_id:
                    continue
                environment["PLAN_MANAGER_ID"] = legacy_id
            if environment.get("PLAN_ACTOR") == "sprint_item_supervisor":
                environment["PLAN_ACTOR"] = "manager"
            if set(environment) not in (
                {"PLAN_ACTOR", "PLAN_TICKET_ID"},
                {"PLAN_ACTOR", "PLAN_MANAGER_ID"},
            ):
                continue
        except (ValueError, TypeError):
            continue
        role = environment.get("PLAN_ACTOR")
        linked_ticket_ids = ownership.get(identity, set())
        owners = set(linked_ticket_ids)
        manager_ids: set[str] = set()
        if role == "worker" and environment.get("PLAN_TICKET_ID"):
            if environment["PLAN_TICKET_ID"] not in linked_ticket_ids:
                continue
            owners.add(environment["PLAN_TICKET_ID"])
        elif role == "manager" and environment.get("PLAN_MANAGER_ID"):
            manager_ids.add(environment["PLAN_MANAGER_ID"])
        else:
            continue
        unknown_agent = False
        for key in agents.get(identity, set()):
            if key.startswith("manager_"):
                manager_ids.add(key.removeprefix("manager_"))
            elif key.startswith("sprint_item_supervisor_"):
                manager_ids.add(key.removeprefix("sprint_item_supervisor_"))
            else:
                unknown_agent = True
        if unknown_agent or not (owners or manager_ids):
            continue
        if any(
            owner not in tickets
            or tickets[owner]["stage"] != "done"
            or tickets[owner]["worker_step_claim"] != "none"
            for owner in owners
        ):
            continue
        if any(owner not in managers or not managers[owner]["settled"] for owner in manager_ids):
            continue
        terminal = {
            "turn": {"completed", "failed", "interrupted", "unavailable"},
            "request": {"answered", "unavailable"},
            "attempt": {"started", "injected", "accepted", "refused"},
        }
        lifecycle = connection.execute(
            "SELECT kind,status FROM conversation_lifecycle_facts WHERE conversation_id=?",
            (identity,),
        ).fetchall()
        if any(status not in terminal.get(kind, set()) for kind, status in lifecycle):
            continue
        cursor = connection.execute(
            "SELECT sequence,kind,payload,created_at FROM conversation_events "
            "WHERE conversation_id=? ORDER BY sequence",
            (identity,),
        )
        events = list(cursor)
        if not events:
            continue
        decoded = {sequence: _object(payload) for sequence, _, payload, _ in events}
        if any(
            value is None
            or kind not in _RAW_PAYLOAD_FIELDS
            or set(value) - (_RAW_PAYLOAD_FIELDS[kind] | {"markdown"})
            for sequence, kind, _, _ in events
            for value in [decoded[sequence]]
        ):
            continue
        # A prompt opens a turn. Only a turn end closes it, including steers.
        last_prompt = max((s for s, k, _, _ in events if k == "prompt"), default=0)
        last_end = max((s for s, k, _, _ in events if k == "turn_ended"), default=0)
        if last_prompt > last_end:
            continue
        read_through = conversation["owner_read_through_sequence"]
        ack = acknowledgements.get(identity, 0)
        if any(k == "message_to_owner" and s > read_through for s, k, _, _ in events):
            continue
        if any(k == "user_input_failed" for _, k, _, _ in events):
            continue
        uncertain: dict[str, int] = {}
        for sequence, kind, _, _ in events:
            payload = decoded[sequence] or {}
            if kind == "prompt_delivery_uncertain":
                sender_message_id = payload.get("sender_message_id")
                uncertain[
                    str(sender_message_id)
                    if sender_message_id is not None
                    else f"sequence:{sequence}"
                ] = sequence
            elif kind in {"prompt", "prompt_delivery_refused", "prompt_discarded"}:
                if isinstance(payload.get("sender_message_id"), str):
                    uncertain.pop(payload["sender_message_id"], None)
                uncertain = {
                    key: seq
                    for key, seq in uncertain.items()
                    if seq != payload.get("reconciles_sequence")
                }
        if uncertain:
            continue
        if any(
            k == "turn_ended"
            and (decoded[s] or {}).get("ending") == "failed"
            and s > ack
            and s == last_end
            for s, k, _, _ in events
        ):
            continue
        pending: set[tuple[str, str]] = set()
        for sequence, kind, _, _ in events:
            payload = decoded[sequence] or {}
            if kind == "permission_asked":
                pending.add(("permission", str(payload.get("ask_id"))))
            elif kind == "permission_answered":
                pending.discard(("permission", str(payload.get("ask_id"))))
            elif kind == "user_input_requested":
                pending.add(("input", str(payload.get("request_id"))))
            elif kind in {"user_input_answered", "user_input_failed"}:
                pending.discard(("input", str(payload.get("request_id"))))
        if pending:
            continue
        protected = {
            conversation.get(name, 0)
            for name in (
                "latest_sequence",
                "latest_agent_activity_sequence",
                "automatically_compacted_through_sequence",
                "owner_read_through_sequence",
                "automatic_compaction_attempted_through_sequence",
            )
        } | {ack}

        def referenced_sequences(value: Any) -> set[int]:
            if isinstance(value, dict):
                return {
                    item
                    for key, item in value.items()
                    if key.endswith("sequence") and type(item) is int
                } | set().union(*(referenced_sequences(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(referenced_sequences(item) for item in value))
            return set()

        protected.update(referenced_sequences(conversation))
        for value in decoded.values():
            protected.update(referenced_sequences(value))
        plans = [s for s, k, _, _ in events if k == "plan_updated"]
        if plans:
            protected.add(max(plans))
        newest = max(t for _, _, _, t in events)
        simple = {"agent_message", "token_usage", "context_compacted", "plan_updated"}
        eligible = {
            s
            for s, k, _, t in events
            if k in simple and t < cutoff_unix and t < newest and s not in protected
        }
        calls: dict[tuple[int, str], list[tuple[int, str, str, int]]] = {}
        turn = 0
        for event in events:
            sequence, kind, _, _ = event
            if kind == "turn_ended":
                turn += 1
            if kind not in {"tool_call_started", "tool_call_finished"}:
                continue
            call_id = (decoded[sequence] or {}).get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            calls.setdefault((turn, call_id), []).append(event)
        for pair in calls.values():
            if len(pair) != 2 or [event[1] for event in pair] != [
                "tool_call_started",
                "tool_call_finished",
            ]:
                continue
            if all(t < cutoff_unix and t < newest and s not in protected for s, _, _, t in pair):
                eligible.update(event[0] for event in pair)
        result.update((identity, sequence) for sequence in eligible)
    return result


def fresh_backup(source: Path, target: Path) -> None:
    """Take an online consistent backup through SQLite's read-only source door."""
    if target.exists() or source.resolve() == target.resolve():
        raise ValueError("Backup target must be a new private path")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.touch(mode=0o600)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)


def _same_table(connection: sqlite3.Connection, table: str) -> bool:
    name = _quote(table)
    columns = ",".join(
        _quote(str(row[1])) for row in connection.execute(f"PRAGMA main.table_info({name})")
    )
    before = f"SELECT {columns},count(*) FROM main.{name} GROUP BY {columns}"
    after = f"SELECT {columns},count(*) FROM after.{name} GROUP BY {columns}"
    return (
        connection.execute(f"{before} EXCEPT {after} LIMIT 1").fetchone() is None
        and connection.execute(f"{after} EXCEPT {before} LIMIT 1").fetchone() is None
    )


def verify_removal(
    before: Path,
    after: Path,
    permitted: set[EventKey],
    *,
    cutoff_unix: int | None = None,
    expired_at_unix: int | None = None,
) -> dict[str, Any]:
    """Reject extras, omissions, changed survivors, and unaccounted table effects."""
    with closing(sqlite3.connect(before.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("ATTACH DATABASE ? AS after", (after.resolve().as_uri() + "?mode=ro",))
        connection.execute(
            "CREATE TEMP TABLE permitted(conversation_id,sequence,"
            "PRIMARY KEY(conversation_id,sequence))"
        )
        connection.executemany("INSERT INTO permitted VALUES(?,?)", sorted(permitted))
        removed = {
            (str(r[0]), int(r[1]))
            for r in connection.execute(
                "SELECT conversation_id,sequence FROM main.conversation_events EXCEPT "
                "SELECT conversation_id,sequence FROM after.conversation_events"
            )
        }
        if removed != permitted:
            raise ValueError(
                f"Removal differs from independent oracle: extra={len(removed - permitted)}, "
                f"missing={len(permitted - removed)}"
            )
        if (
            connection.execute(
                "SELECT * FROM after.conversation_events EXCEPT "
                "SELECT * FROM main.conversation_events LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise ValueError("An event survivor changed or an unexpected event appeared")
        tables = {
            r[0]
            for r in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
        }
        before_schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name"
        ).fetchall()
        after_schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM after.sqlite_master ORDER BY type,name"
        ).fetchall()
        if before_schema != after_schema:
            raise ValueError("Apply changed schema or trigger definitions")
        after_tables = {
            r[0]
            for r in connection.execute("SELECT name FROM after.sqlite_master WHERE type='table'")
        }
        if tables != after_tables:
            raise ValueError("Apply changed the table inventory")
        special = {
            "conversation_events",
            "conversation_expired_ranges",
            "signal_publication_dirty",
            "navigation_meta",
            "navigation_journal",
            "navigation_commit_witnesses",
            "navigation_view_meta",
            "navigation_view_changes",
            "sqlite_sequence",
        }
        special.update(table for table in tables if table.startswith("conversation_view_"))
        _verify_projection_effects(connection, removed)
        _verify_ranges(connection, removed, cutoff_unix, expired_at_unix)
        changed = sorted(table for table in tables - special if not _same_table(connection, table))
        if changed:
            raise ValueError(f"Unaccounted table changes: {changed}")
        # Detailed publication checks are intentionally fail-closed, not table exemptions.
        _verify_publication_effects(connection, removed)
        before_controls = _derived_controls(connection)
        with closing(sqlite3.connect(after.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
            if before_controls != _derived_controls(reader):
                raise ValueError("Derived attention, memory notice, or first prompt state changed")
        return {
            "removed_event_count": len(removed),
            "unchanged_table_count": len(tables - special),
            "survivors_byte_identical": True,
            "independent_oracle_matches": True,
            "derived_attention_memory_and_first_prompt_unchanged": True,
            "receipt_and_pending_ask_source_rows_unchanged": True,
        }


def _verify_publication_effects(connection: sqlite3.Connection, removed: set[EventKey]) -> None:
    affected = {conversation for conversation, _ in removed}
    previous = {
        r[0]
        for r in connection.execute("SELECT conversation_id FROM main.signal_publication_dirty")
    }
    actual = {
        r[0]
        for r in connection.execute("SELECT conversation_id FROM after.signal_publication_dirty")
    }
    if actual != previous | affected:
        raise ValueError("Signal dirty identities differ from the exact expected union")
    before_meta = connection.execute("SELECT * FROM main.navigation_meta").fetchone()
    after_meta = connection.execute("SELECT * FROM after.navigation_meta").fetchone()
    if before_meta is None or after_meta is None:
        raise ValueError("Navigation metadata absent")
    expected_revision = before_meta[-1] + bool(removed)
    if after_meta != (*before_meta[:-1], expected_revision):
        raise ValueError("Unexpected navigation continuity change")
    previous_view = connection.execute("SELECT * FROM main.navigation_view_meta").fetchone()
    actual_view = connection.execute("SELECT * FROM after.navigation_view_meta").fetchone()
    from planner.navigation.views.ordering import ORDERING_GENERATION

    active_view = (
        previous_view is not None
        and previous_view[2] == before_meta[3]
        and previous_view[4] == ORDERING_GENERATION
    )
    expected_view = previous_view
    if removed and active_view:
        expected_view = (*previous_view[:3], expected_revision, previous_view[4])
    if expected_view != actual_view:
        raise ValueError("Unexpected navigation view continuity change")
    previous_changes = dict(connection.execute("SELECT * FROM main.navigation_view_changes"))
    actual_changes = dict(connection.execute("SELECT * FROM after.navigation_view_changes"))
    if removed and active_view:
        body = actual_changes.pop(expected_revision, None)
        if body is None or json.loads(body) != {"keys": [], "sections": [], "globals": False}:
            raise ValueError("Expiry changed navigation view values")
        from planner.navigation.views.configuration import DEFAULT_CONFIG

        previous_changes = {
            revision: body
            for revision, body in previous_changes.items()
            if revision >= max(0, expected_revision - DEFAULT_CONFIG.retained_revisions)
        }
    if previous_changes != actual_changes:
        raise ValueError("Unexpected navigation view journal change")
    previous_journal = dict(connection.execute("SELECT * FROM main.navigation_journal"))
    actual_journal = dict(connection.execute("SELECT * FROM after.navigation_journal"))
    if removed:
        body = actual_journal.pop(expected_revision, None)
        if body is None:
            raise ValueError("Missing navigation commit journal")
        batch = json.loads(body)
        for field, expected in (
            ("scope", before_meta[1]),
            ("schema_version", before_meta[2]),
            ("epoch", before_meta[3]),
        ):
            if batch.get(field) != expected:
                raise ValueError("Wrong navigation journal identity")
        if batch.get("upserts") != [] or batch.get("deletes") != []:
            raise ValueError("Expiry changed public navigation values")
        if batch.get("revision") != str(expected_revision) or batch.get("base_revision") != str(
            before_meta[-1]
        ):
            raise ValueError("Wrong navigation journal continuity")
    if previous_journal != actual_journal:
        raise ValueError("Existing navigation journal changed")
    witnesses = list(
        connection.execute("SELECT * FROM main.navigation_commit_witnesses ORDER BY sequence")
    )
    actual_witnesses = list(
        connection.execute("SELECT * FROM after.navigation_commit_witnesses ORDER BY sequence")
    )
    if removed:
        maximum = max((r[0] for r in witnesses), default=0) + 1
        new = [r for r in actual_witnesses if r not in witnesses]
        if (
            len(new) != 1
            or new[0][0] != maximum
            or len(new[0][1]) != 64
            or any(character not in "0123456789abcdef" for character in new[0][1])
        ):
            raise ValueError("Unexpected commit witness insertion")
        from planner.navigation.proof_limits import RETAINED_WITNESSES

        if actual_witnesses != [r for r in witnesses if r[0] > maximum - RETAINED_WITNESSES] + new:
            raise ValueError("Unexpected commit witness pruning")
    elif witnesses != actual_witnesses:
        raise ValueError("No-op expiry changed witnesses")
    before_sequences = dict(connection.execute("SELECT * FROM main.sqlite_sequence"))
    after_sequences = dict(connection.execute("SELECT * FROM after.sqlite_sequence"))
    if removed:
        before_sequences["navigation_commit_witnesses"] = max(
            (r[0] for r in actual_witnesses), default=0
        )
    if before_sequences != after_sequences:
        raise ValueError("Unexpected SQLite sequence watermark change")


def _verify_ranges(
    connection: sqlite3.Connection,
    removed: set[EventKey],
    cutoff_unix: int | None,
    expired_at_unix: int | None,
) -> None:
    previous = list(connection.execute("SELECT * FROM main.conversation_expired_ranges"))
    actual = list(connection.execute("SELECT * FROM after.conversation_expired_ranges"))
    for row in previous:
        if actual.count(row) != previous.count(row):
            raise ValueError("Existing omission metadata changed")
    added = [row for row in actual if row not in previous]
    described: set[EventKey] = set()
    for conversation, first, last, cutoff, expired_at, count in added:
        if (cutoff_unix is not None and cutoff != cutoff_unix) or (
            expired_at_unix is not None and expired_at != expired_at_unix
        ):
            raise ValueError("Omission metadata differs from execution cutoff or timestamp")
        if first > last or count != last - first + 1 or expired_at < cutoff:
            raise ValueError("Invalid omission range metadata")
        for sequence in range(first, last + 1):
            key = (conversation, sequence)
            if key in described:
                raise ValueError("Overlapping omission ranges")
            described.add(key)
    if described != removed:
        raise ValueError("Omission metadata does not describe exactly the removal set")


def _verify_projection_effects(connection: sqlite3.Connection, removed: set[EventKey]) -> None:
    from planner.conversation.views.schema import SCHEMA_STATEMENTS
    from planner.conversation.views.writer import backfill_projection

    affected = {conversation for conversation, _ in removed}
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' "
            "AND name LIKE 'conversation_view_%'"
        )
    ]
    expected_tables = {
        "conversation_view_state",
        "conversation_view_rows",
        "conversation_view_turns",
        "conversation_view_groups",
        "conversation_view_lookups",
    }
    if set(tables) != expected_tables:
        raise ValueError("Unknown conversation projection schema")
    # Replay retained rows into a new scratch schema without any production triggers.
    # No trigger on the actual apply copy is disabled, removed, or bypassed.
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute(
            connection.execute(
                "SELECT sql FROM main.sqlite_master WHERE type='table' AND name='conversations'"
            ).fetchone()[0]
        )
        scratch.execute(
            "CREATE TABLE conversation_events(conversation_id TEXT,sequence INTEGER,kind TEXT,"
            "payload TEXT,created_at INTEGER,PRIMARY KEY(conversation_id,sequence))"
        )
        for statement in SCHEMA_STATEMENTS:
            scratch.execute(statement)
        for conversation in sorted(affected):
            source_conversation = connection.execute(
                "SELECT * FROM main.conversations WHERE conversation_id=?", (conversation,)
            ).fetchone()
            placeholders = ",".join("?" for _ in source_conversation)
            scratch.execute(
                f"INSERT INTO conversations VALUES({placeholders})", source_conversation
            )
            scratch.executemany(
                "INSERT INTO conversation_events VALUES(?,?,?,?,?)",
                connection.execute(
                    "SELECT * FROM after.conversation_events "
                    "WHERE conversation_id=? ORDER BY sequence",
                    (conversation,),
                ),
            )
        backfill_projection(scratch)
        for table in tables:
            name = _quote(table)
            for conversation in sorted(affected):
                expected = sorted(
                    scratch.execute(
                        f"SELECT * FROM {name} WHERE conversation_id=?", (conversation,)
                    ).fetchall(),
                    key=repr,
                )
                actual = sorted(
                    connection.execute(
                        f"SELECT * FROM after.{name} WHERE conversation_id=?", (conversation,)
                    ).fetchall(),
                    key=repr,
                )
                if expected != actual:
                    raise ValueError(f"Retained-event projection replay differs: {table}")
            before_other = connection.execute(
                f"SELECT * FROM main.{name} WHERE conversation_id NOT IN "
                "(SELECT conversation_id FROM permitted)"
            ).fetchall()
            after_other = connection.execute(
                f"SELECT * FROM after.{name} WHERE conversation_id NOT IN "
                "(SELECT conversation_id FROM permitted)"
            ).fetchall()
            if sorted(before_other, key=repr) != sorted(after_other, key=repr):
                raise ValueError(f"Unaffected projection changed: {table}")


def _derived_controls(connection: sqlite3.Connection) -> dict[str, Any]:
    from dataclasses import asdict

    from planner.conversation.publication.reader import read_durable_facts
    from planner.runtime.worker_memory import conversation_holds_an_unanswered_memory_loss

    result: dict[str, Any] = {}
    for conversation_id, boundary in connection.execute(
        "SELECT conversation_id,automatically_compacted_through_sequence FROM conversations"
    ):
        first_prompt, answering = connection.execute(
            "SELECT MIN(CASE WHEN kind='prompt' THEN sequence END),"
            "MAX(CASE WHEN kind='prompt' AND "
            "substr(json_extract(payload,'$.sender_message_id'),1,12)='worker_step_' "
            "THEN sequence END) FROM conversation_events WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        attention = read_durable_facts(connection, conversation_id)
        result[conversation_id] = {
            "durable_attention": None if attention is None else asdict(attention),
            "memory_notice_required": conversation_holds_an_unanswered_memory_loss(
                connection, conversation_id
            ),
            "compaction_boundary": boundary,
            "first_prompt_sequence": first_prompt,
            "latest_worker_step_prompt_sequence": answering,
        }
    return result


def _unfinished_worker_prompt_evidence(database: Path, cutoff: int) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        controls = _derived_controls(connection)
        rows = connection.execute(
            "SELECT t.id,t.title,e.conversation_id,e.sequence,e.created_at FROM tickets t "
            "JOIN (SELECT id ticket_id,conversation_id FROM tickets "
            "WHERE conversation_id IS NOT NULL "
            "UNION SELECT ticket_id,conversation_id FROM ticket_conversations) l "
            "ON l.ticket_id=t.id "
            "JOIN conversation_events e ON e.conversation_id=l.conversation_id "
            "WHERE t.stage!='done' AND e.kind='prompt' AND e.created_at<? "
            "AND substr(json_extract(e.payload,'$.sender_message_id'),1,12)='worker_step_' "
            "ORDER BY t.title,e.conversation_id,e.sequence",
            (cutoff,),
        ).fetchall()
        return [
            {
                "ticket_id": ticket,
                "title": title,
                "conversation_id": conversation,
                "prompt_sequence": sequence,
                "created_at": created,
                "derived_controls": controls[conversation],
            }
            for ticket, title, conversation, sequence, created in rows
        ]


def _verify_metadata_installation(untouched: Path, prepared: Path) -> None:
    with closing(sqlite3.connect(untouched.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("ATTACH DATABASE ? AS after", (prepared.as_uri() + "?mode=ro",))
        before = set(connection.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_master"))
        after = set(connection.execute("SELECT type,name,tbl_name,sql FROM after.sqlite_master"))
        if before - after or any(row[2] != "conversation_expired_ranges" for row in after - before):
            raise ValueError("Metadata preparation changed existing schema")
        for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'"):
            if not _same_table(connection, row[0]):
                raise ValueError(f"Metadata preparation changed existing rows: {row[0]}")
        if connection.execute("SELECT 1 FROM after.conversation_expired_ranges LIMIT 1").fetchone():
            raise ValueError("Metadata preparation unexpectedly added omission ranges")


def measure_fresh_copy(
    database: Path, output_dir: Path, *, as_of_unix: int, command: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Measure private copies with production guards and an independent removal proof."""
    import dataclasses
    import hashlib
    import os
    import shlex
    import subprocess
    import sys
    import time

    source_root = Path(__file__).resolve().parents[1]
    source_files = sorted((source_root / "conversation" / "retention").rglob("*.py"))
    source_files.extend(
        [Path(__file__).resolve(), Path(__file__).with_name("diagnostic_history.py")]
    )

    def code_hashes() -> dict[str, str]:
        return {
            str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_files
        }

    initial_code_hashes = code_hashes()
    initial_diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "HEAD"],
        cwd=source_root,
        capture_output=True,
        check=True,
    ).stdout

    from planner.conversation.retention.data import read_report
    from planner.maintenance.diagnostic_history import (
        apply_private_report,
        prepare_private_copy,
        reclaim_private_database,
    )

    database = database.resolve(strict=True)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError("Measurement directory must not exist. Keep each experiment separate.")
    output_dir.mkdir(parents=True, mode=0o700)
    marker = output_dir / ".diagnostic-history-measurement"
    marker.write_text(json.dumps({"source_database": str(database)}))
    marker.chmod(0o600)
    started = time.time()
    untouched = output_dir / "source-backup.db"
    fresh_backup(database, untouched)
    snapshot_completed = time.time()
    # Schema preparation affects a private copy only. Both comparison arms inherit it.
    prepared = output_dir / "snapshot.db"
    fresh_backup(untouched, prepared)
    prepare_private_copy(prepared)
    _verify_metadata_installation(untouched, prepared)
    control = output_dir / "control.db"
    expired = output_dir / "expired.db"
    fresh_backup(untouched, control)
    fresh_backup(prepared, expired)
    reproduction = command or (
        "python",
        "-m",
        "planner.maintenance.diagnostic_history_measure",
        "--database",
        str(database),
        "--output-dir",
        str(output_dir) + "-fresh",
        "--as-of-unix",
        str(as_of_unix),
    )
    rerun_prefix = shlex.join(
        (
            sys.executable,
            "-m",
            "planner.maintenance.diagnostic_history_measure",
            "--database",
            str(database),
        )
    )
    command_text = (
        rerun_prefix
        + ' --output-dir "$(mktemp -d)/experiment"'
        + " --as-of-unix "
        + str(as_of_unix)
    )
    refresh_command = (
        rerun_prefix + ' --output-dir "$(mktemp -d)/experiment"' + ' --as-of-unix "$(date -u +%s)"'
    )
    report = read_report(expired, as_of_unix=as_of_unix, command=reproduction)
    with closing(sqlite3.connect(prepared.as_uri() + "?mode=ro", uri=True)) as reader:
        permitted = independent_removal_oracle(reader, report.cutoff_unix)
    actual = {
        (candidate.conversation_id, candidate.sequence)
        for decision in report.decisions
        for candidate in decision.candidates
    }
    if actual != permitted:
        (output_dir / "oracle-disagreement.json").write_text(
            json.dumps(
                {
                    "classifier_only": sorted(actual - permitted),
                    "oracle_only": sorted(permitted - actual),
                }
            )
        )
        raise ValueError(
            f"Classifier differs from raw-row oracle: "
            f"extra={len(actual - permitted)}, missing={len(permitted - actual)}"
        )
    with (output_dir / "reviewed-report.json").open("x") as handle:
        os.chmod(handle.name, 0o600)
        json.dump(dataclasses.asdict(report), handle, ensure_ascii=False)
    applied = apply_private_report(expired, report)
    proof = verify_removal(
        prepared,
        expired,
        permitted,
        cutoff_unix=report.cutoff_unix,
        expired_at_unix=applied.expired_at_unix,
    )
    control_result = reclaim_private_database(control)
    expired_result = reclaim_private_database(expired)
    # VACUUM must preserve logical contents, in addition to the writer proof above.
    for path in (control, expired):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
            if reader.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Compacted database integrity failed")
            if reader.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Compacted database foreign keys failed")
    unfinished_evidence = _unfinished_worker_prompt_evidence(prepared, report.cutoff_unix)
    if unfinished_evidence != _unfinished_worker_prompt_evidence(expired, report.cutoff_unix):
        raise ValueError("Real unfinished Worker prompt cases changed")
    proof_after_compaction = verify_removal(
        prepared,
        expired,
        permitted,
        cutoff_unix=report.cutoff_unix,
        expired_at_unix=applied.expired_at_unix,
    )
    with closing(sqlite3.connect(untouched.as_uri() + "?mode=ro", uri=True)) as reader:
        reader.execute("ATTACH DATABASE ? AS after", (control.as_uri() + "?mode=ro",))
        for row in reader.execute("SELECT name FROM main.sqlite_master WHERE type='table'"):
            if not _same_table(reader, row[0]):
                raise ValueError(f"Control compaction changed logical rows: {row[0]}")

    def allocated(path: Path) -> int:
        return path.stat().st_blocks * 512

    disk = os.statvfs(database.parent)
    metrics = {
        "source_filesystem_total_bytes": disk.f_blocks * disk.f_frsize,
        "source_filesystem_available_bytes_at_measurement_end": disk.f_bavail * disk.f_frsize,
        "cutoff_selection_unix": as_of_unix,
        "cutoff_unix": report.cutoff_unix,
        "snapshot_completion_unix": snapshot_completed,
        "source_snapshot_database_bytes": untouched.stat().st_size,
        "prepared_snapshot_database_bytes": prepared.stat().st_size,
        "compacted_control_database_bytes": control.stat().st_size,
        "compacted_expired_database_bytes": expired.stat().st_size,
        "compaction_only_physical_recovery_bytes": untouched.stat().st_size
        - control.stat().st_size,
        "removed_logical_payload_bytes": applied.removed_payload_bytes,
        "extra_physical_recovery_bytes": control.stat().st_size - expired.stat().st_size,
        "extra_allocated_block_recovery_bytes": allocated(control) - allocated(expired),
        "removed_event_count": applied.removed_event_count,
        "affected_conversation_count": applied.affected_conversation_count,
        "control_wal_bytes": control_result.wal_bytes_after,
        "expired_wal_bytes": expired_result.wal_bytes_after,
        "elapsed_seconds": time.time() - started,
        "temporary_database_bytes_at_measurement_end": sum(
            path.stat().st_size for path in output_dir.glob("*.db")
        ),
    }
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if code_hashes() != initial_code_hashes:
        raise ValueError(
            "Implementation source changed during measurement. Refresh the experiment."
        )
    result = {
        "format_version": 1,
        "revision": revision,
        "implementation_source_sha256": initial_code_hashes,
        "tracked_working_diff_sha256": hashlib.sha256(initial_diff).hexdigest(),
        "source_database": str(database),
        "source_fingerprint": report.source_fingerprint,
        "command": command_text,
        "refresh_command": refresh_command,
        "metrics": {
            name: {"value": value, "command": command_text} for name, value in metrics.items()
        },
        "metadata_installation_preserves_all_existing_rows_and_schema": True,
        "unfinished_worker_prompt_evidence": [
            {**evidence, "command": command_text} for evidence in unfinished_evidence
        ],
        "proof": {**proof, "command": command_text},
        "proof_after_compaction": {**proof_after_compaction, "command": command_text},
        "actual_metadata_and_publication_overhead_included": True,
        "recommendation": (
            "Consider compaction without expiry first. "
            "Expiry is not urgent at the reported disk headroom."
        ),
        "limits": [
            "No source rows were deleted. All writes target private copies.",
            "This measures database files and allocated blocks. "
            "It does not predict whole-filesystem free-space changes.",
            "Extra physical recovery includes row overhead, index space, "
            "and freed pages beyond logical JSON payload bytes.",
            "The source fingerprint identifies the prepared private snapshot. "
            "Refresh the report before any owner decision.",
            "Native provider sessions and external artifact files are outside this measurement.",
        ],
    }
    with (output_dir / "measurement.json").open("x") as handle:
        os.chmod(handle.name, 0o600)
        json.dump(result, handle, indent=2, ensure_ascii=False)
    return result


def main() -> None:
    """Run a new private experiment. The source opens through mode=ro only."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of-unix", type=int, required=True)
    options = parser.parse_args()
    print(
        json.dumps(
            measure_fresh_copy(options.database, options.output_dir, as_of_unix=options.as_of_unix),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
