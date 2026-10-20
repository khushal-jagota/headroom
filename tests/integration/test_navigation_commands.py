"""HTTP bodies and receipts survive complete, failed and overflowing capture."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Never, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.navigation_current import current_navigation_database

from planner.core.db import commit_without_change_signal, connect, create_schema
from planner.navigation import proof
from planner.navigation.command_contracts import FENCE_HEADER
from planner.navigation.middleware import NavigationCommandFenceMiddleware


def command_app(tmp_path: Path) -> FastAPI:
    path = tmp_path / "planner.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES('c','codex','/synthetic','full',0)"
    )
    conn.commit()
    conn.close()
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Physical preparation is not public authority. Reuse the actual producer
        # for this middleware-only app rather than rewriting metadata to "current".
        with current_navigation_database(path):
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        NavigationCommandFenceMiddleware, db_path=str(path), conn_factory=lambda: connect(str(path))
    )
    return app


def write(tmp_path: Path, value: int) -> None:
    conn = connect(str(tmp_path / "planner.db"))
    try:
        conn.execute(
            "UPDATE conversations SET latest_sequence=? WHERE conversation_id='c'", (value,)
        )
        commit_without_change_signal(conn)
    finally:
        conn.close()


def fence(response: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(response.headers[FENCE_HEADER.decode()]))


def test_observation_failure_and_overflow_never_change_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = command_app(tmp_path)

    @app.post("/api/tickets")
    async def tickets() -> dict[str, bool]:
        for value in range(1, 67):
            write(tmp_path, value)
        return {"accepted": True}

    with TestClient(app) as client:
        response = client.post("/api/tickets")
        assert response.status_code == 200
        assert response.json() == {"accepted": True}
        assert fence(response)["coverage"] == "unknown"
        assert len(fence(response)["witnesses"]) <= 64

        def fail(*_args: object) -> object:
            raise RuntimeError("synthetic fence observer failure")

        monkeypatch.setattr(proof, "response_fence", fail)
        response = client.post("/api/tickets")
        assert response.status_code == 200
        assert response.json() == {"accepted": True}
        assert response.headers["panels-navigation-coverage"] == "unknown"


def test_real_application_command_families_and_store_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.integration.navigation_runtime_support import make_app

    from planner.conversation.backends.contracts import BackendSpawnFailed
    from planner.conversation.contracts import ConversationBackendKey

    def refuse_child(**_kwargs: Any) -> Never:
        raise BackendSpawnFailed("synthetic backend unavailable")

    monkeypatch.setattr(
        "planner.core.server.production_backend_child_factories",
        lambda **_kwargs: {key: refuse_child for key in ConversationBackendKey},
    )
    app = make_app(tmp_path)
    with TestClient(app) as client:

        def accepted(response: Any, *, commits: bool = True) -> dict[str, Any]:
            assert response.status_code in (200, 201, 204), response.text
            issued = fence(response)
            assert issued["coverage"] == "complete"
            if commits:
                assert issued["witnesses"]
            return response.json() if response.content else {}

        project = accepted(
            client.post(
                "/api/projects",
                json={"name": "Navigation proof", "summary": "Synthetic"},
            )
        )
        accepted(client.patch(f"/api/projects/{project['id']}", json={"name": "Renamed"}))
        item = accepted(
            client.post("/api/items", json={"title": "Orchestrator", "project_id": project["id"]})
        )
        accepted(client.patch(f"/api/items/{item['id']}/settled", json={"settled": True}))
        ticket = accepted(
            client.post(
                "/api/tickets",
                json={
                    "title": "Capture this command",
                    "worker_type": "coding",
                    "brief": "Synthetic",
                    "orchestrator": {"kind": "chief", "id": "chief"},
                },
            )
        )
        accepted(client.patch(f"/api/tickets/{ticket['id']}", json={"title": "Changed"}))
        accepted(
            client.patch(
                f"/api/tickets/{ticket['id']}",
                json={
                    "orchestrator": {"kind": "sprint_item", "id": item["id"]},
                },
            )
        )
        proposal = accepted(
            client.post(
                f"/api/tickets/{ticket['id']}/propose",
                json={"body": "Observe complete navigation evidence"},
                headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
            )
        )
        other = accepted(client.post(
            "/api/items", json={"title": "Other orchestrator", "project_id": project["id"]},
        ))

        def park_for_original_holder() -> dict[str, Any]:
            return accepted(client.patch(f"/api/tickets/{ticket['id']}", json={
                "orchestrator": {"kind": "sprint_item", "id": item["id"]},
                "ceiling_holder": {"kind": "sprint_item", "id": item["id"]},
            }))

        def owner_release(response: Any, previous: dict[str, Any], parent: str | None) -> None:
            accepted(response)
            detail = client.get("/api/tickets", params={"detail": "full", "id": ticket["id"]})
            assert detail.status_code == 200, detail.text
            moved = detail.json()
            assert moved["ceiling_holder"] == {"kind": "owner", "id": "owner"}
            assert moved["pending_proposal"] == previous["pending_proposal"]
            projected = client.get("/api/navigation/snapshot", params={
                "fence": response.headers[FENCE_HEADER.decode()],
            })
            assert projected.status_code == 200, projected.text
            body = projected.json()
            assert body["proof"]["status"] == "included"
            value = next(record["value"] for record in body["snapshot"]["records"]
                         if record["kind"] == "ticket" and record["id"] == ticket["id"])
            assert value["ceiling_holder"] == moved["ceiling_holder"]
            assert value["sprint_item_id"] == parent
            assert value["ticket_status"] == "awaiting_approval"
            assert value["proposal_revision"] == previous["pending_proposal"]["revision"]

        # Existing routes now prove the domain move and its owner-release publication
        # together, rather than publishing only a move whose holder already is owner.
        parked = park_for_original_holder()
        owner_release(client.patch(f"/api/tickets/{ticket['id']}", json={
            "orchestrator": {"kind": "sprint_item", "id": other["id"]},
        }), parked, other["id"])
        parked = park_for_original_holder()
        owner_release(client.delete(
            f"/api/collections/outcome_tickets/{item['id']}/{ticket['id']}",
        ), parked, None)
        parked = park_for_original_holder()
        owner_release(client.put(
            f"/api/collections/outcome_tickets/{other['id']}/{ticket['id']}",
        ), parked, other["id"])
        proposal = client.get(
            "/api/tickets", params={"detail": "full", "id": ticket["id"]},
        ).json()
        accepted(
            client.post(
                f"/api/tickets/{ticket['id']}/accept/{proposal['pending_proposal']['field']}",
                json={
                    "expected_proposal_revision": proposal["pending_proposal"]["revision"],
                    "next_holder": {"kind": "owner", "id": "owner"},
                    "next_ceiling": "needs_implementation",
                },
            )
        )
        accepted(
            client.put(
                "/api/workers/coding/launch-defaults",
                json={
                    "employee_backend": "codex",
                    "employee_launch_model": "gpt-5.6-sol",
                    "employee_launch_reasoning_effort": "high",
                },
            ),
            commits=False,
        )
        accepted(
            client.post(f"/api/tickets/{ticket['id']}/move", json={"locale": "vps"}), commits=False
        )
        accepted(client.delete(f"/api/tickets/{ticket['id']}"))
        # Real conversation routes use the runtime's actual store, not the injected
        # worker facade. Creation and owner-read use shielded thread writes.
        accepted(
            client.post(
                "/api/conversation/conversations",
                json={
                    "conversation_id": "synthetic",
                    "model": "gpt-5.6-sol",
                    "backend_key": "codex",
                    "workspace_folder": str(tmp_path),
                },
            )
        )
        sent = client.post(
            "/api/conversation/conversations/synthetic/send",
            json={
                "content": [{"piece": "text", "text": "Synthetic capture proof"}],
                "sender_label": "Owner",
            },
        )
        accepted(sent)
        assert len(fence(sent)["witnesses"]) >= 2
        proof_response = client.get(
            "/api/navigation/snapshot", params={"fence": sent.headers[FENCE_HEADER.decode()]}
        )
        assert proof_response.status_code == 200
        assert proof_response.json()["proof"]["status"] == "included"
        owner_read = client.post(
            "/api/conversation/conversations/synthetic/owner-read", json={"through_sequence": 1}
        )
        accepted(owner_read)
        accepted(client.post("/api/conversation/conversations/synthetic/kill"), commits=False)
        refused = client.post(
            "/api/projects",
            json={"name": "Denied"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]},
        )
        assert refused.status_code >= 400
        assert FENCE_HEADER.decode() not in refused.headers
