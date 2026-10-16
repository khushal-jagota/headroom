"""Offline-only reviewed cutover proof. Never starts a server or readiness loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from planner.core import db
from planner.core.contracts import OWNER_PRINCIPAL
from planner.runtime.worker_step_readiness import is_ready_for_worker_step
from planner.tickets import data
from planner.worker_types.configuration import load_worker_runtime_definitions

LIVE = Path("/home/vps/Deployments/Panels/current/data/planner.db").resolve()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute('SELECT * FROM "' + table + '"')]


def table_digest(conn: sqlite3.Connection, table: str) -> str:
    checksum = hashlib.sha256()
    # Stream large transcripts instead of retaining production-sized payloads.
    for row in conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
        checksum.update(digest(dict(row)).encode())
    return checksum.hexdigest()


def run(baseline: Path, destination: Path, manifest: Path, output: Path) -> None:
    baseline, destination = baseline.resolve(), destination.resolve()
    if baseline == LIVE or destination == LIVE or destination == baseline or destination.exists():
        raise RuntimeError("Use an existing isolated baseline and a new isolated destination")
    manifest_bytes = manifest.read_bytes()
    dispositions = json.loads(manifest_bytes)
    if len(dispositions["records"]) != 17:
        raise RuntimeError("Expected the reviewed 17-record manifest")
    source = sqlite3.connect(f"file:{baseline}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    before_tickets = {r["id"]: r for r in rows(source, "tickets")}
    before_items = {r["id"]: r for r in rows(source, "sprint_items")}
    preserved_tables = [
        "ideas",
        "conversations",
        "conversation_events",
        "ticket_conversations",
        "ticket_blocks",
        "ticket_paired_stage_openers",
    ]
    before_tables = {table: table_digest(source, table) for table in preserved_tables}
    copy = sqlite3.connect(destination)
    source.backup(copy)
    copy.close()
    source.close()
    # This is the migration engine only. It does not seed/write external skill homes,
    # load conversations, acquire runtime claims, send messages or start poll loops.
    db._upgrade_to_current_schema(str(destination), 5000)
    conn = db.connect(str(destination))
    registry = load_worker_runtime_definitions(conn).worker_type_registry
    receipts = []
    selected = {r["ticket_id"] for r in dispositions["records"]}
    now = 1792150000
    for entry in dispositions["records"]:
        tid = entry["ticket_id"]
        original = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
        assert original["stage"] == entry["current_stage"]
        assert {k: original[k] for k in entry["original_record"]} == entry["original_record"]
        fingerprint = data.ticket_record_sha256(conn, tid)
        if entry["disposition"] == "backlog":
            data.set_ticket_backlog(
                conn,
                tid,
                is_backlogged=True,
                principal=OWNER_PRINCIPAL,
                now=now,
                expected_record_sha256=fingerprint,
            )
        else:
            if entry["disposition"] == "idea":
                assert (
                    conn.execute("SELECT 1 FROM ideas WHERE id=?", (entry["idea_id"],)).fetchone()
                    is not None
                )
            data.retire_ticket(
                conn,
                tid,
                reason=entry["reason"],
                principal=OWNER_PRINCIPAL,
                now=now,
                expected_record_sha256=fingerprint,
            )
        after = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
        if entry["disposition"] == "backlog":
            assert after == {**original, "is_backlogged": 1, "updated_at": now}
            ticket = data.read_ticket(conn, tid)
            assert not is_ready_for_worker_step(
                conn, ticket, worker_type_definition=registry.require(ticket.worker_type)
            )
        else:
            assert after["stage"] == "cancelled" and after["pending_proposal"] is None
            provenance = json.loads(json.loads(after["field_values"])["retirement"])
            assert provenance["original_record"] == original
            for key in original.keys() - {
                "stage",
                "pending_proposal",
                "pending_proposal_revision",
                "field_values",
                "updated_at",
            }:
                assert after[key] == original[key]
        assert after["conversation_id"] == original["conversation_id"]
        assert after["ceiling_holder"] == original["ceiling_holder"]
        # A repeated request is a no-op even with a later clock. No extra transition
        # or provenance record is permitted.
        if entry["disposition"] == "backlog":
            data.set_ticket_backlog(
                conn, tid, is_backlogged=True, principal=OWNER_PRINCIPAL, now=now + 1
            )
        else:
            data.retire_ticket(
                conn, tid, reason=entry["reason"], principal=OWNER_PRINCIPAL, now=now + 1
            )
        repeated = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
        assert repeated == after
        receipts.append(
            {
                "ticket_id": tid,
                "disposition": entry["disposition"],
                "before": original,
                "after": after,
                "repeat_unchanged": True,
                "readback_verified": True,
            }
        )
    after_tickets = {r["id"]: r for r in rows(conn, "tickets")}
    assert set(before_tickets) == set(after_tickets)
    untouched = set(before_tickets) - selected - {"t_tnmp9e7r"}
    for tid in untouched:
        assert after_tickets[tid] == {
            **before_tickets[tid],
            "orchestrator_kind": "sprint_item"
            if before_tickets[tid]["sprint_item_id"]
            else "chief",
            "is_backlogged": 0,
        }
    completed_ids = [
        tid
        for tid, r in before_tickets.items()
        if r["worker_type"].startswith("planning-") and r["stage"] == "done"
    ]
    assert len(completed_ids) == 37 and set(completed_ids) <= untouched
    before_approvals = {
        tid: r["pending_proposal"]
        for tid, r in before_tickets.items()
        if r["pending_proposal"] is not None
    }
    after_approvals = {
        tid: r["pending_proposal"]
        for tid, r in after_tickets.items()
        if r["pending_proposal"] is not None
    }
    assert len(before_approvals) == 35
    assert after_approvals == {tid: p for tid, p in before_approvals.items() if tid != "t_tnmp9e7r"}
    after_items = {r["id"]: r for r in rows(conn, "sprint_items")}
    for iid, item in before_items.items():
        assert after_items[iid] == {
            **item,
            "settled": int(iid not in {"si_dmhx1u7h", "si_8nbv749p"}),
        }
    assert sum(not i["settled"] for i in after_items.values()) == 2
    for table in preserved_tables:
        assert table_digest(conn, table) == before_tables[table]
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert (
        hashlib.sha256(manifest.read_bytes()).hexdigest()
        == hashlib.sha256(manifest_bytes).hexdigest()
    )
    # Reopening the same migration head is a no-op.
    final_tickets = rows(conn, "tickets")
    final_items = rows(conn, "sprint_items")
    db._upgrade_to_current_schema(str(destination), 5000)
    assert final_tickets == rows(conn, "tickets") and final_items == rows(conn, "sprint_items")
    report = {
        "status": "PASS",
        "offline_only": True,
        "baseline": str(baseline),
        "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "destination": str(destination),
        "migration_head": conn.execute("SELECT version_num FROM alembic_version").fetchone()[0],
        "counts": {
            "originals": 17,
            "retired": 6,
            "backlogged": 11,
            "tickets_before": len(before_tickets),
            "tickets_after": len(after_tickets),
            "untouched_tickets": len(untouched),
            "completed_planning_preserved": 37,
            "approvals_before": 35,
            "approvals_after": 34,
            "active_items": 2,
            "settled_items": 88,
        },
        "preserved_table_hashes": before_tables,
        "untouched_ticket_ids": sorted(untouched),
        "completed_planning_ids": completed_ids,
        "preserved_approval_ids": sorted(after_approvals),
        "receipts": receipts,
        "planning_retirement": after_tickets["t_tnmp9e7r"],
        "repeat_operations_and_migrations_unchanged": True,
        "direct_contact": "Conversation IDs and conversation tables unchanged. No backend ran.",
        "runtime_proof": "Separate synthetic real Luna evidence is required.",
    }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    conn.close()
    print(json.dumps({"status": "PASS", "report": str(output), "counts": report["counts"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.baseline, args.destination, args.manifest, args.output)
