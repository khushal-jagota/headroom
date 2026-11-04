"""Saved workflows cross the canonical HTTP gate without a registered custom type.

These cases reuse the existing create API host. They protect snapshot independence
and custom field approval, which registered-type tests cannot establish.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from tests.unit.test_ticket_create_api import create_api as create_api


def _custom_workflow() -> dict[str, Any]:
    return {
        "label": "Evidence review",
        "guidance": "Use the supplied evidence only.",
        "stages": [
            {
                "id": "needs_brief",
                "label": "Brief",
                "gating_field": "brief",
                "is_terminal": False,
                "ownership_mode": "worker",
                "instructions": "Define the question.",
            },
            {
                "id": "needs_evidence",
                "label": "Evidence",
                "gating_field": "evidence",
                "is_terminal": False,
                "ownership_mode": "worker",
                "instructions": "Compare the two records.",
            },
            {
                "id": "needs_consequences",
                "label": "Consequences",
                "gating_field": "consequences",
                "is_terminal": False,
                "ownership_mode": "worker",
                "instructions": "Record the result.",
            },
            {
                "id": "done",
                "label": "Done",
                "gating_field": None,
                "is_terminal": True,
                "ownership_mode": None,
                "instructions": "",
            },
        ],
        "fields": [
            {"id": key, "label": label}
            for key, label in [
                ("brief", "Brief"),
                ("evidence", "Evidence"),
                ("consequences", "Consequences"),
            ]
        ],
    }


def test_custom_workflow_reaches_its_own_gate_and_accepts_its_own_result(
    create_api: tuple[TestClient, Path],
) -> None:
    client, _ = create_api
    preset = client.get("/api/worker-types/coding").json()
    profile = preset["profile"]
    response = client.post(
        "/api/tickets",
        json={
            "title": "Compare the evidence",
            "worker_type": None,
            "workflow": _custom_workflow(),
            "employee_backend": profile["default_backend"],
            "employee_launch_model": profile["default_model"],
            "locale": "vps",
            "brief": "Compare the two supplied records.",
            "ceiling": "needs_evidence",
        },
    )
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket["worker_type"] is None
    assert ticket["stage"] == "needs_evidence"
    assert ticket["workflow"]["worker_profile_id"] is None
    assert ticket["workflow"]["stages"][1]["instructions"] == "Compare the two records."
    ticket_id = ticket["id"]
    proposal = client.post(
        f"/api/tickets/{ticket_id}/propose",
        json={"body": "The records agree."},
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
    )
    assert proposal.status_code == 200, proposal.text
    assert proposal.json()["pending_proposal"]["field"] == "evidence"
    accepted = client.post(
        f"/api/tickets/{ticket_id}/accept/evidence",
        json={
            "next_ceiling": "needs_consequences",
            "next_holder": {"kind": "owner", "id": "owner"},
            "expected_proposal_revision": proposal.json()["pending_proposal"]["revision"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["stage"] == "needs_consequences"
    assert accepted.json()["field_values"]["evidence"] == "The records agree."


def test_preset_rewrite_does_not_reinterpret_existing_ticket_stages(
    create_api: tuple[TestClient, Path],
) -> None:
    client, _ = create_api
    created = []
    for title in ("First independent copy", "Second independent copy"):
        response = client.post(
            "/api/tickets", json={"title": title, "worker_type": "coding", "locale": "vps"}
        )
        assert response.status_code == 200, response.text
        created.append(response.json())
    preset = client.get("/api/worker-types/coding").json()
    replacement = deepcopy(preset)
    replacement["stages"] = [
        stage for stage in preset["stages"] if stage["id"] != "needs_implementation"
    ]
    replacement["fields"] = [field for field in preset["fields"] if field["id"] != "implementation"]
    response = client.post("/api/worker-types", json=replacement)
    assert response.status_code == 200, response.text
    assert response.json()["migrations"] == []
    for before in created:
        read = client.get("/api/tickets", params={"id": before["id"], "detail": "full"})
        assert read.status_code == 200, read.text
        body = read.json()
        after = body["tickets"][0] if "tickets" in body else body
        assert after["workflow"] == before["workflow"]
        assert after["stage"] == before["stage"]
        assert "needs_implementation" in after["workflow"]["ceiling_range"]
    future = client.post(
        "/api/tickets", json={"title": "Future copy", "worker_type": "coding", "locale": "vps"}
    )
    assert future.status_code == 200, future.text
    assert "needs_implementation" not in future.json()["workflow"]["ceiling_range"]
