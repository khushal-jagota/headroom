"""Declaring a Worker type over HTTP: the one door, and what it refuses.

Landing a Worker used to mean a Python file and a release. These tests are the proof that
it is now a write, and that the write is guarded where the import-time check used to be.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.support.model_catalogue import seed_test_model_catalogue
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.worker_settings import service as worker_settings_service
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    db_path = tmp_path / "worker-types.db"
    with closing(connect(str(db_path))) as boot:
        create_schema(boot)
        seed_test_model_catalogue(boot)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": "2026-07-10T12:00:00+01:00",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as test_client:
        yield test_client, db_path


def _arrival_record(coding: dict[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(json.dumps(coding))
    record["worker_type"] = "arrival"
    record["label"] = "Arrival"
    record["profile"]["specialist_skill"] = "panels-worker-arrival"
    record["skill"] = {
        "description": "A worker declared without a release.",
        "markdown_body": "# Arrival\n\nDo the work.\n",
    }
    return record


def test_a_rewrite_that_would_strand_a_live_ticket_is_refused(
    client: tuple[TestClient, Path],
) -> None:
    test_client, db_path = client
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Mid flight",
            brief="Kickoff",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        )
        conn.execute("UPDATE tickets SET stage = 'needs_plan' WHERE id = ?", (ticket.id,))
    finally:
        conn.close()

    coding = test_client.get("/api/worker-types/coding").json()
    without_plan = json.loads(json.dumps(coding))
    without_plan["stages"] = [
        stage for stage in without_plan["stages"] if stage["id"] != "needs_plan"
    ]
    without_plan["fields"] = [field for field in without_plan["fields"] if field["id"] != "plan"]

    response = test_client.post("/api/worker-types", json=without_plan)

    assert response.status_code == 400, response.text
    assert ticket.id in response.text
    assert test_client.get("/api/worker-types/coding").json() == coding


def test_launch_defaults_are_the_type_and_take_effect_at_once(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client

    response = test_client.put(
        "/api/workers/coding/launch-defaults",
        json={
            "employee_backend": "claude",
            "employee_launch_model": "claude-opus-5",
            "employee_launch_reasoning_effort": None,
        },
    )

    assert response.status_code == 200, response.text
    record = test_client.get("/api/worker-types/coding").json()
    assert record["profile"]["default_backend"] == "claude"
    assert record["profile"]["default_model"] == "claude-opus-5"
    assert (
        configured_worker_type_registry().require("coding").worker_profile.default_model == "claude-opus-5"
    )


def test_a_worker_may_not_declare_a_worker_type(client: tuple[TestClient, Path]) -> None:
    """Declaring a Worker is the owner's act. An agent proposes; it does not self-declare."""
    test_client, _db_path = client
    coding = test_client.get("/api/worker-types/coding").json()

    response = test_client.post(
        "/api/worker-types",
        json=_arrival_record(coding),
        headers={"X-Plan-Actor": "worker"},
    )

    assert response.status_code != 200, response.text
    assert "arrival" not in [
        manifest["worker_type"]
        for manifest in test_client.get("/api/worker-types").json()["worker_types"]
    ]


def test_chief_recap_is_writable_by_the_owner_and_the_chief(
    client: tuple[TestClient, Path],
) -> None:
    """The Chief writes its own recap; other agents and malformed payloads are refused."""
    test_client, db_path = client
    settings_path = "/api/workers/chief-of-staff/settings"
    write_path = "/api/workers/chief-of-staff/recap"
    before = test_client.get(settings_path).json()
    assert before["recap"] == ""
    recap = "Own the shared direction.\n\nKeep **decisions** visible."
    expected = dict(before, recap=recap)
    response = test_client.put(write_path, json={"recap": recap})
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert test_client.get(settings_path).json() == expected
    assert test_client.get("/api/workers").json()["chief_of_staff"]["recap"] == recap
    with connect(str(db_path)) as conn:
        assert conn.execute("SELECT recap FROM chief_settings").fetchone()[0] == recap

    chief_recap = "Chief wrote this."
    response = test_client.put(
        write_path, json={"recap": chief_recap}, headers={"X-Plan-Actor": "chief"}
    )
    assert response.status_code == 200, response.text
    assert test_client.get(settings_path).json()["recap"] == chief_recap
    response = test_client.put(write_path, json={"recap": recap})
    assert response.status_code == 200, response.text

    for headers in (
        {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_recap"},
        {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": "si_recap"},
    ):
        response = test_client.put(write_path, json={"recap": "unauthorized"}, headers=headers)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "agent_forbidden"
    for payload in ({}, {"recap": None}, {"recap": 1}, {"recap": "bad", "label": "bad"}):
        response = test_client.put(write_path, json=payload)
        assert response.status_code == 400, response.text
    assert test_client.get(settings_path).json() == expected
    response = test_client.put(write_path, json={"recap": ""})
    assert response.status_code == 200, response.text
    assert response.json() == before


def test_generic_skill_edit_and_legacy_adapters_use_the_same_partial_writer(
    client: tuple[TestClient, Path],
) -> None:
    test_client, db_path = client
    before = test_client.get("/api/skills/panels-worker-coding").json()
    response = test_client.patch(
        "/api/skills/panels-worker-coding",
        json={"description": "common writer", "markdown_body": "# Updated"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "common writer"
    assert response.json()["markdown_body"] == "\n# Updated\n"
    assert response.json()["name"] == before["name"]
    for method in ("put", "patch"):
        response = test_client.request(
            method, "/api/workers/coding/skill", json={"body": f"# {method}"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["specialist_skill"]["description"] == "common writer"
        assert response.json()["specialist_skill"]["markdown_body"] == f"\n# {method}\n"
    for method in ("put", "patch"):
        response = test_client.request(
            method, "/api/workers/chief-of-staff/skill", json={"body": f"# Chief {method}"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["skill"]["markdown_body"] == f"\n# Chief {method}\n"
    missing = test_client.patch("/api/skills/missing", json={"description": "no create"})
    assert missing.status_code == 404
    with connect(str(db_path)) as conn:
        assert (
            conn.execute("SELECT 1 FROM managed_skills WHERE skill_name = 'missing'").fetchone()
            is None
        )


def test_skill_write_authority_is_preserved_at_every_http_entrypoint(
    client: tuple[TestClient, Path],
) -> None:
    test_client, db_path = client
    coding = test_client.get("/api/worker-types/coding").json()
    writes = [
        ("patch", "/api/skills/panels-worker-coding", {"description": "authority checked"}),
        ("put", "/api/workers/coding/skill", {"description": "authority checked"}),
        ("patch", "/api/workers/coding/skill", {"description": "authority checked"}),
        ("put", "/api/workers/chief-of-staff/skill", {"description": "authority checked"}),
        ("patch", "/api/workers/chief-of-staff/skill", {"description": "authority checked"}),
        ("post", "/api/worker-types", dict(coding, skill={"description": "authority checked"})),
    ]
    for method, path, payload in writes:
        chief = test_client.request(method, path, json=payload, headers={"X-Plan-Actor": "chief"})
        assert chief.status_code == 200, chief.text
        before = test_client.get("/api/skills").json()
        for headers in (
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_denied"},
            {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": "si_denied"},
        ):
            refused = test_client.request(method, path, json=payload, headers=headers)
            assert refused.status_code == 400, refused.text
            assert refused.json()["error"]["code"] == "agent_forbidden"
        assert test_client.get("/api/skills").json() == before
    skill = test_client.get("/api/skills/panels-worker-coding").json()
    assert skill["description"] == "authority checked"
    assert (db_path.parent / "skills/panels-worker-coding/SKILL.md").is_file()


def test_embedded_skill_validates_the_original_payload_and_requires_initial_fields(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client
    coding = test_client.get("/api/worker-types/coding").json()
    for skill in (
        {"description": "initial"},
        {"body": "initial body"},
        {"name": "wrong", "description": "initial", "body": "body"},
        {"description": "initial", "body": "body", "unknown": "bad"},
        {"description": "initial", "body": "one", "markdown_body": "two"},
    ):
        record = _arrival_record(coding)
        record["skill"] = skill
        response = test_client.post("/api/worker-types", json=record)
        assert response.status_code == 400, response.text
        assert "field" in response.json()["error"]["detail"]
        assert test_client.get("/api/skills/panels-worker-arrival").status_code == 404
    record = _arrival_record(coding)
    record["skill"] = {
        "name": "panels-worker-arrival",
        "description": "initial",
        "body": "new body",
    }
    assert test_client.post("/api/worker-types", json=record).status_code == 200


def test_skill_file_failure_reaches_http_as_an_incomplete_save(
    client: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _db_path = client

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(worker_settings_service, "atomic_replace_text", fail_replace)
    response = test_client.patch("/api/skills/panels", json={"description": "saved"})
    assert response.status_code == 500, response.text
    error = response.json()["error"]
    assert error["code"] == "skill_save_incomplete"
    assert error["detail"] == {
        "skill_name": "panels",
        "saved": True,
        "published": False,
        "phase": "file",
    }
    assert "Read the canonical skill before retry" in error["message"]


def test_one_type_reads_back_exactly_what_the_whole_list_said(
    client: tuple[TestClient, Path],
) -> None:
    """The list is 20,165 bytes for fourteen types; a Ticket page draws one.

    The single-type read has to be the same entry in the same shape, because the
    lifecycle, the stage table and the Ticket section admission all read that shape.
    """
    test_client, _db_path = client

    whole = test_client.get("/api/worker-types").json()["worker_types"]
    one = test_client.get("/api/worker-types/coding/manifest")

    assert one.status_code == 200, one.text
    assert one.json()["worker_types"] == [
        entry for entry in whole if entry["worker_type"] == "coding"
    ]

    unknown = test_client.get("/api/worker-types/not-a-worker/manifest")
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "not_found"
    assert unknown.json()["error"]["detail"] == {"worker_type": "not-a-worker"}


def test_reviewed_skill_batch_accepts_owner_and_chief_but_refuses_other_agents(
    client: tuple[TestClient, Path],
) -> None:
    test_client, _db_path = client
    source = "---\nname: batch-role\ndescription: Reviewed role\n---\n\nExact owner instructions.\n"
    payload = {"skills": [{"name": "batch-role", "source_text": source, "expected_sha256": None}]}
    before = test_client.get("/api/skills").json()
    for headers in (
        {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_denied"},
        {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": "si_denied"},
        {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": "si_denied"},
    ):
        result = test_client.post("/api/skills/apply", json=payload, headers=headers)
        assert result.status_code == 400, result.text
        assert result.json()["error"]["code"] == "agent_forbidden"
        assert test_client.get("/api/skills").json() == before
    accepted = test_client.post("/api/skills/apply", json=payload)
    assert accepted.status_code == 200, accepted.text
    assert test_client.get("/api/skills/batch-role").json()["source_text"] == source
    chief_source = source.replace("Exact owner instructions.", "Exact Chief instructions.")
    chief_payload = {
        "skills": [{
            "name": "batch-role",
            "source_text": chief_source,
            "expected_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }]
    }
    accepted = test_client.post(
        "/api/skills/apply", json=chief_payload, headers={"X-Plan-Actor": "chief"}
    )
    assert accepted.status_code == 200, accepted.text
    assert test_client.get("/api/skills/batch-role").json()["source_text"] == chief_source
