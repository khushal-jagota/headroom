"""The read must trust stored principals and preserve scope through deleted work."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import asdict
from datetime import date
from pathlib import Path

from tests.unit.test_attention_summary import at

from planner.attention.actions import read_summary
from planner.attention.contracts import Destination, GroupBy, Scope, ScopeKind, SummaryRequest
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.projects.data import create_project
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.data import create_ticket


def test_authenticated_global_replay_deleted_snapshot_goal_context_and_old_database(
    tmp_path: Path,
) -> None:
    with closing(connect(str(tmp_path / "attention.sqlite3"))) as conn:
        create_schema(conn)
        start = at("2026-11-02")
        conn.execute("UPDATE attention_history_metadata SET started_at=?", (at("2026-10-01"),))
        project = create_project(conn, name="Attention test", now=start - 1000000)
        other_project = create_project(conn, name="Moved work", now=start - 1000000)
        ticket = create_ticket(
            conn,
            title="Deleted work",
            principal=OWNER_PRINCIPAL,
            now=start - 1000,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            project_id=project.id,
        )
        snapshot = Destination(
            "ticket", ticket.id, ticket.title, f"/tickets/{ticket.id}", project.id
        )
        conn.execute(
            "INSERT INTO attention_approval_events(ticket_id,field,actor_kind,actor_id,method,"
            "occurred_at,destination_json) VALUES (?,?,?,?,?,?,?)",
            (
                ticket.id,
                "brief",
                "owner",
                "owner",
                "explicit_accept",
                start + 300,
                json.dumps(asdict(snapshot)),
            ),
        )
        conn.execute("UPDATE tickets SET project_id=? WHERE id=?", (other_project.id, ticket.id))
        for conversation in ("old", "new"):
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,"
                "access,created_at) "
                "VALUES (?,?,?,?,?)",
                (conversation, "codex", str(tmp_path), "{}", at("2026-10-01")),
            )
        records: tuple[tuple[str, int, int, dict[str, object]], ...] = (
            (
                "old",
                1,
                start - 100,
                {
                    "sender": {"kind": "owner", "id": "owner"},
                    "sender_message_id": "replay",
                    "recipient": {"kind": "ticket", "id": ticket.id},
                },
            ),
            (
                "new",
                1,
                start + 100,
                {
                    "sender": {"kind": "owner", "id": "owner"},
                    "sender_message_id": "replay",
                    "recipient": {"kind": "chief", "id": "chief"},
                },
            ),
            (
                "new",
                2,
                start + 200,
                {
                    "sender": {"kind": "ticket", "id": ticket.id},
                    "sender_label": "Khushal",
                    "recipient": {"kind": "chief", "id": "chief"},
                },
            ),
            (
                "old",
                2,
                start + 250,
                {
                    "sender": {"kind": "owner", "id": "owner"},
                    "recipient": {"kind": "ticket", "id": ticket.id},
                    "sent_at_unix_milliseconds": 1,
                    "sender_message_id": "same-second",
                },
            ),
        )
        # A replay in the same server second must retain the first persisted recipient.
        records += (
            (
                "new",
                3,
                start + 250,
                {
                    "sender": {"kind": "owner", "id": "owner"},
                    "sender_message_id": "same-second",
                    "recipient": {"kind": "chief", "id": "chief"},
                },
            ),
        )
        for conversation, sequence, occurred, payload in records:
            conn.execute(
                "INSERT INTO conversation_events VALUES (?,?,?,?,?)",
                (conversation, sequence, "prompt", json.dumps(payload), occurred),
            )
        conn.execute("DELETE FROM tickets WHERE id=?", (ticket.id,))
        conn.execute(
            "INSERT INTO goals(id,project_id,title,why,kind,active,completed,page,revision,"
            "created_at,updated_at,employee_backend,employee_launch_model) "
            "VALUES (?,?,?,'why','binary',1,0,'{}',1,?,?,'codex','gpt-6.1-sol')",
            ("goal_test", project.id, "Goal", start, start),
        )
        conn.commit()
        req = SummaryRequest(date(2026, 11, 2), date(2026, 11, 2), GroupBy.ticket)
        result = read_summary(conn, req, now=at("2026-11-04"))
        assert result["totals"]["current"]["owner_messages"]["value"] == 1
        assert result["totals"]["previous"]["owner_messages"]["value"] == 1
        assert result["totals"]["current"]["owner_approvals"]["value"] == 1
        deleted = next(row for row in result["rows"] if row["id"] == ticket.id)
        assert deleted["current"]["owner_messages"]["value"] == 1
        chief = next(row for row in result["rows"] if row["kind"] == "chief")
        assert chief["current"]["owner_messages"]["value"] == 0
        assert deleted["title"] == "Deleted work"
        assert deleted["project_id"] is None
        assert "identity_only" in deleted["attribution_basis"]
        grouped = read_summary(
            conn, SummaryRequest(date(2026, 11, 2), date(2026, 11, 2)), now=at("2026-11-04")
        )
        old_project = next(row for row in grouped["rows"] if row["id"] == project.id)
        assert old_project["current"]["owner_messages"]["value"] == 0
        assert old_project["current"]["owner_approvals"]["value"] == 1
        unattributed = next(row for row in grouped["rows"] if row["kind"] == "unattributed")
        assert unattributed["current"]["owner_messages"]["value"] == 1
        goal = read_summary(
            conn,
            SummaryRequest(
                date(2026, 11, 2), date(2026, 11, 2), scope=Scope(ScopeKind.goal, "goal_test")
            ),
            now=at("2026-11-04"),
        )
        assert goal["totals"]["current"]["owner_messages"]["value"] == 0
        assert goal["shared_project_context"]["totals"]["current"]["owner_messages"]["value"] == 0
        assert goal["shared_project_context"]["totals"]["current"]["owner_approvals"]["value"] == 1
        # Legacy missing provenance stays unknown instead of becoming an automatic message.
        conn.execute(
            "INSERT INTO conversation_events VALUES ('new',4,'prompt',?,?)",
            (json.dumps({"sender_label": "Panels loop"}), start + 400),
        )
        # A read-only old deployment can be inspected without running its new migration.
        conn.execute("DROP TABLE attention_approval_events")
        conn.execute("DROP TABLE attention_gate_intervals")
        conn.execute("DROP TABLE attention_history_metadata")
        conn.commit()
        old = read_summary(conn, req, now=at("2026-11-04"))
        assert old["totals"]["current"]["owner_messages"]["value"] == 1
        assert old["totals"]["current"]["owner_approvals"]["value"] is None
        assert old["totals"]["current"]["owner_messages"]["coverage_status"] == "partial"
        assert old["totals"]["current"]["without_owner_involvement_share"]["value"] is None


def test_system_provenance_codec_rejects_untyped_markers_and_keeps_legacy_absence() -> None:
    import pytest

    from planner.conversation.contracts import PromptDeliveryMode
    from planner.conversation.events import (
        ConversationEventKind,
        PromptEventPayload,
        conversation_event_payload_from_canonical_json,
        conversation_event_payload_to_canonical_json,
    )
    from planner.conversation.message_content import text_message_content

    payload = PromptEventPayload(
        text_message_content("system"), "Panels", PromptDeliveryMode.queue, system_generated=True
    )
    encoded = conversation_event_payload_to_canonical_json(payload)
    assert json.loads(encoded)["system_generated"] is True
    decoded = conversation_event_payload_from_canonical_json(ConversationEventKind.prompt, encoded)
    assert isinstance(decoded, PromptEventPayload)
    assert decoded.system_generated is True
    malformed = json.loads(encoded)
    malformed["system_generated"] = 1
    with pytest.raises(ValueError, match="boolean"):
        conversation_event_payload_from_canonical_json(
            ConversationEventKind.prompt, json.dumps(malformed)
        )
    del malformed["system_generated"]
    legacy = conversation_event_payload_from_canonical_json(
        ConversationEventKind.prompt, json.dumps(malformed)
    )
    assert isinstance(legacy, PromptEventPayload)
    assert legacy.system_generated is False
    assert "system_generated" not in json.loads(
        conversation_event_payload_to_canonical_json(legacy)
    )
