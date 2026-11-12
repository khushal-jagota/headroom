"""Both decision transports must retain the revision that the caller inspected."""

import json
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from tests.support.api_app import _app
from tests.unit.test_notifications import _conversation_on_a_ticket
from tests.unit.test_pending_ceiling import _park

from planner.cli import http
from planner.cli.main import main
from planner.core import server
from planner.core.contracts import Principal, PrincipalKind
from planner.tickets import data, views


def test_cli_pins_command_read_and_explicit_revision_before_ancillary_writes(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = _park(tmp_db)
    detail = views.ticket_json(ticket, 2)
    revision = ticket.pending_proposal_revision
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return detail

    monkeypatch.setattr(http, "send", send)
    monkeypatch.delenv("PLAN_CALLER_PRINCIPAL", raising=False)
    runner = CliRunner()
    for action in ("accept", "revise"):
        calls.clear()
        arguments = ["ticket", "proposal", ticket.id, action, "--json"]
        if action == "accept":
            arguments += ["--ceiling", "needs_implementation", "--holder", "owner"]
        result = runner.invoke(main, arguments, input="Exact guidance")
        assert result.exit_code == 0, result.output
        assert calls[-1][2]["json_body"]["expected_proposal_revision"] == revision
        explicit = runner.invoke(
            main, [*arguments, "--expected-proposal-revision", str(revision)],
            input="Exact guidance",
        )
        assert explicit.exit_code == 0, explicit.output
        assert calls[-1][2]["json_body"]["expected_proposal_revision"] == revision
    calls.clear()
    earlier = runner.invoke(main, [
        "ticket", "proposal", ticket.id, "revise", "--expected-proposal-revision",
        str(revision + 1), "--json",
    ], input="Exact earlier guidance")
    assert earlier.exit_code == 0, earlier.output
    assert [method for method, _path, _body in calls] == ["POST"]
    assert calls[-1][2]["json_body"]["expected_proposal_revision"] == revision + 1
    calls.clear()
    page_edit = runner.invoke(main, ["page", "edit", "--chief", "--json"], input=json.dumps({
        "page": None, "expected_revision": 7,
    }))
    assert page_edit.exit_code == 0, page_edit.output
    assert calls[-1][2]["json_body"] == {"page": None, "expected_revision": 7}
    calls.clear()
    stale = runner.invoke(main, [
        "ticket", "proposal", ticket.id, "accept", "--ceiling", "needs_implementation",
        "--holder", "owner", "--kickoff-title", "Must not change",
        "--expected-proposal-revision", str(revision + 1), "--json",
    ])
    assert stale.exit_code != 0
    assert "visible proposal changed" in stale.output
    assert [method for method, _path, _body in calls] == ["GET"]


def test_native_and_web_stale_decisions_preserve_replacement_and_feedback(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(server, "_ASSETS_DIR", root / "assets")
    monkeypatch.setattr(server, "_STATIC_DIR", root / "static")
    original = _park(tmp_db)
    _conversation_on_a_ticket(tmp_db, original.id)
    fixture = root / "ios/PanelsTests/Fixtures/review-decision-requests.json"
    captured = json.loads(fixture.read_text())
    revision = captured["approve"]["expected_proposal_revision"]
    tmp_db.execute(
        "UPDATE tickets SET pending_proposal_revision=? WHERE id=?", (revision, original.id)
    )
    tmp_db.commit()
    replacement = data.file_current_proposal(
        tmp_db, original.id, body="Replacement proposal B",
        principal=Principal(PrincipalKind.ticket, original.id), now=2,
    )
    assert replacement.pending_proposal_revision == revision + 1
    feedback_before = list(tmp_db.execute(
        "SELECT * FROM ticket_revision_feedback WHERE ticket_id=?", (original.id,)
    ))
    with TestClient(_app(tmp_db)) as client:
        for native in (True, False):
            headers = {"X-Panels-iOS-Build": "5"} if native else {}
            for decision, suffix in (("approve", "accept/what_changes"), ("send_back", "reject")):
                body = captured[decision] if native else {
                    **captured[decision], "expected_proposal_revision": revision,
                }
                refused = client.post(
                    f"/api/tickets/{original.id}/{suffix}", json=body, headers=headers
                )
                assert refused.status_code == 400, refused.text
                assert "visible proposal changed" in refused.json()["error"]["message"]
                assert data.read_ticket(tmp_db, original.id) == replacement
                assert list(tmp_db.execute(
                    "SELECT * FROM ticket_revision_feedback WHERE ticket_id=?", (original.id,)
                )) == feedback_before
        accepted = client.post(f"/api/tickets/{original.id}/reject", json={
            **captured["send_back"],
            "expected_proposal_revision": replacement.pending_proposal_revision,
        }, headers={"X-Panels-iOS-Build": "5"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["pending_proposal"] is None
        saved = tmp_db.execute(
            "SELECT feedback_json FROM ticket_revision_feedback WHERE ticket_id=?", (original.id,)
        ).fetchone()
        assert saved is not None and captured["send_back"]["message"] in saved[0]
