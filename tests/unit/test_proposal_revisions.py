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
    arguments = [
        "ticket",
        "proposal",
        ticket.id,
        "accept",
        "--json",
        "--ceiling",
        "needs_implementation",
        "--holder",
        "owner",
    ]
    result = runner.invoke(main, arguments)
    assert result.exit_code == 0, result.output
    assert calls[-1][2]["json_body"]["expected_proposal_revision"] == revision
    calls.clear()
    missing = runner.invoke(
        main, ["ticket", "proposal", ticket.id, "revise", "--json"], input="Exact guidance"
    )
    assert missing.exit_code != 0
    assert "requires --revision" in missing.output
    assert not calls
    earlier = runner.invoke(
        main,
        [
            "ticket",
            "proposal",
            ticket.id,
            "revise",
            "--revision",
            str(revision + 1),
            "--json",
        ],
        input="Exact earlier guidance",
    )
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


def test_native_and_web_stale_decisions_preserve_replacement_and_decision_messages(
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
    messages_before = list(tmp_db.execute(
        "SELECT * FROM ticket_decision_messages WHERE ticket_id=?", (original.id,)
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
                    "SELECT * FROM ticket_decision_messages WHERE ticket_id=?", (original.id,)
                )) == messages_before
        accepted = client.post(f"/api/tickets/{original.id}/reject", json={
            **captured["send_back"],
            "expected_proposal_revision": replacement.pending_proposal_revision,
        }, headers={"X-Panels-iOS-Build": "5"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["pending_proposal"] is None
        saved = tmp_db.execute(
            "SELECT message,proposal_revision FROM ticket_decision_messages WHERE ticket_id=?",
            (original.id,),
        ).fetchall()
        assert len(saved) == len(messages_before) + 1
        assert saved[-1]["message"] == captured["send_back"]["message"]
        assert saved[-1]["proposal_revision"] == replacement.pending_proposal_revision


def test_ticket_show_proposal_exposes_revision_in_text_and_json(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _park(tmp_db)
    detail = views.ticket_json(ticket, 2)

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/api/worker-types":
            return {"worker_types": [{"worker_type": "coding", "fields": [{"id": "brief"}]}]}
        return detail

    monkeypatch.setattr(http, "send", send)
    args = ["ticket", "show", ticket.id, "proposal"]
    plain = CliRunner().invoke(main, args)
    structured = CliRunner().invoke(main, [*args, "--json"])
    assert plain.exit_code == structured.exit_code == 0
    assert f'"revision": {ticket.pending_proposal_revision}' in plain.output
    assert (json.loads(structured.output)["parts"]["proposal"]["value"]["revision"]
            == ticket.pending_proposal_revision)


def test_cli_lost_response_retries_real_recorded_revision(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    ticket = _park(tmp_db)
    _conversation_on_a_ticket(tmp_db, ticket.id)
    monkeypatch.delenv("PLAN_EXECUTOR_ROOT", raising=False)
    calls = 0
    with TestClient(_app(tmp_db)) as client:
        def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            nonlocal calls
            calls += 1
            response = client.request(method, httpx.URL(url).raw_path.decode(), json=kwargs["json"])
            req = httpx.Request(method, url)
            if calls == 1:
                assert response.status_code == 200, response.text
                raise httpx.ReadTimeout("response lost", request=req)
            return httpx.Response(response.status_code, json=response.json(), request=req)

        monkeypatch.setattr(httpx, "request", request)
        result = CliRunner().invoke(
            main, ["ticket", "proposal", ticket.id, "revise", "--revision",
                   str(ticket.pending_proposal_revision), "--json"], input="Exact guidance",
        )
    assert calls == 2
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["detail"]["result"] == "already_resolved"
    messages = list(tmp_db.execute(
        "SELECT message FROM ticket_decision_messages WHERE ticket_id=?", (ticket.id,)
    ))
    assert [row["message"] for row in messages] == ["Exact guidance"]
