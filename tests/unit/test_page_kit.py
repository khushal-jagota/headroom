"""A proposal page is stored only when it stays inside the shared kit.

The risk is a filed page the browser would show and the server would not, or a
prose proposal whose bytes change on the way in. The proposal door canonicalizes
a page, keeps its identities, and refuses one that leaves the kit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tests.support.principals import OWNER_PRINCIPAL, ticket_principal
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)

from planner.core.clock import TestClock
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.page_kit.logic.validate import prepare_stored_text
from planner.tickets import data
from planner.tickets.contracts import NO_FURTHER, TITLE_MAX_CHARS

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "src" / "planner" / "page_kit" / "examples" / "proposal-page.json"


def test_example_page_is_stored_once_and_kept() -> None:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    stored = prepare_stored_text(json.dumps(document), "proposal body")
    parsed = json.loads(stored)
    assert parsed["title"] == "Ship proposal pages"
    assert prepare_stored_text(stored, "proposal body") == stored
    glance = next(block for block in parsed["blocks"] if block.get("title") == "At a glance")
    assert glance["blocks"][0]["type"] == "properties"
    assert [item["label"] for item in glance["blocks"][0]["items"]] == [
        "Stage",
        "Owner",
        "Open choice",
        "Blockers",
    ]
    readiness = next(block for block in parsed["blocks"] if block.get("title") == "Readiness")
    assert [view["id"] for view in readiness["views"]] == ["risks", "evidence"]
    evidence = next(view for view in readiness["views"] if view["id"] == "evidence")
    assert [block["type"] for block in evidence["blocks"]] == ["table"]
    assert "blocks" not in readiness


def test_prose_is_stored_unchanged_and_a_broken_page_is_refused() -> None:
    prose = "  {not a page, just a note}\n"
    assert prepare_stored_text(prose, "proposal body") == prose
    other = json.dumps({"kind": "note", "body": "hello"})
    assert prepare_stored_text(other, "proposal body") == other
    broken = '{ "kind": "headroom.page", "title": '
    try:
        prepare_stored_text(broken, "proposal body")
    except PlannerError as exc:
        assert "not valid JSON" in exc.message
    else:
        raise AssertionError("a broken page was stored")
    try:
        prepare_stored_text("", "proposal body")
    except PlannerError as exc:
        assert "must be non-empty" in exc.message
    else:
        raise AssertionError("an empty body was stored")
    try:
        prepare_stored_text(
            json.dumps(
                {
                    "kind": "headroom.page",
                    "version": 1,
                    "title": "Nope",
                    "summary": "Outside.",
                    "blocks": [{"type": "hero", "id": "nope"}],
                }
            ),
            "proposal body",
        )
    except PlannerError as exc:
        assert "outside the page kit" in exc.message
        assert exc.detail["path"]
    else:
        raise AssertionError("a page outside the kit was stored")


def test_parked_proposal_stores_the_canonical_page(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "probe.db"))
    create_schema(conn)
    seed_probe_worker_type(conn)
    install_probe_registry()
    try:
        now = TestClock(datetime(2026, 10, 31, 12).astimezone()).now_unix()
        page = {
            "summary": "The parked proposal is a page.",
            "blocks": [{"text": "Ship the kit.", "type": "text"}],
            "title": "Beta page",
            "version": 1,
            "kind": "headroom.page",
        }
        raw = "\n" + json.dumps(page) + "\n"
        ticket = data.create_ticket(
            conn,
            worker_type="probe",
            title="Probe",
            principal=OWNER_PRINCIPAL,
            now=now,
            title_max_chars=TITLE_MAX_CHARS,
            brief=raw,
        )
        assert ticket.pending_proposal is not None
        stored_page = json.loads(ticket.pending_proposal.body)
        assert stored_page["title"] == "Beta page"
        assert stored_page["blocks"][0]["text"] == "Ship the kit."
        assert stored_page["blocks"][0]["id"]
        assert prepare_stored_text(ticket.pending_proposal.body, "proposal body") == (
            ticket.pending_proposal.body
        )
        kept = ticket.pending_proposal
        try:
            data.file_current_proposal(
                conn,
                ticket.id,
                body=json.dumps(
                    {
                        "kind": "headroom.page",
                        "version": 1,
                        "title": "Nope",
                        "summary": "Outside.",
                        "blocks": [{"type": "hero"}],
                    }
                ),
                principal=ticket_principal(ticket.id),
                now=now,
            )
        except PlannerError as exc:
            assert "outside the page kit" in exc.message
        else:
            raise AssertionError("a page outside the kit replaced the parked proposal")
        assert data.read_ticket(conn, ticket.id).pending_proposal == kept
        edited = {
            "kind": "headroom.page",
            "version": 1,
            "title": "Beta page",
            "summary": "The holder tightened the summary.",
            "blocks": [{"type": "text", "text": "Ship the kit.", "id": "kept-text"}],
        }
        accepted = data.accept_proposal(
            conn,
            ticket.id,
            field=kept.field,
            principal=OWNER_PRINCIPAL,
            now=now,
            edited_body=json.dumps(edited),
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
        assert accepted.pending_proposal is None
        stored = json.loads(accepted.field_values[kept.field])
        assert stored["summary"] == "The holder tightened the summary."
        assert stored["blocks"][0]["id"] == "kept-text"
    finally:
        uninstall_probe_registry()
        conn.close()
