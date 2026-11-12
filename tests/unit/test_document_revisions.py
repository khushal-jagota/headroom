"""Native document metadata must protect the exact document inside its writer."""

from copy import deepcopy
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.api_app import _app
from tests.unit.test_goal_pages import create, page

from planner.core import server
from planner.core.clock import TestClock as Clock
from planner.managers.data import create_manager


@pytest.mark.parametrize("kind", ["goal", "chief", "manager"])
def test_native_document_revision_success_and_stale_preservation(
    tmp_db: Connection, fake_clock: Clock, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(server, "_ASSETS_DIR", root / "assets")
    monkeypatch.setattr(server, "_STATIC_DIR", root / "static")
    if kind == "goal":
        goal = create(tmp_db)
        path, revision_key = f"/api/goals/{goal.id}", "revision"
    elif kind == "chief":
        path, revision_key = "/api/chief/page", "page_revision"
    else:
        manager = create_manager(
            tmp_db, title="Guide", project_id="project_vylo", clock=fake_clock
        )
        path, revision_key = f"/api/managers/{manager.id}/page", "page_revision"
    with TestClient(_app(tmp_db), headers={"X-Panels-iOS-Build": "5"}) as client:
        captured = client.get(path).json()[revision_key]
        document = page()
        first = client.patch(path, json={"page": document, "expected_revision": captured})
        assert first.status_code == 200, first.text
        accepted = first.json()
        assert accepted[revision_key] == captured + 1
        newer = deepcopy(accepted["page"])
        newer["title"] = "Newer document"
        updated = client.patch(
            path, json={"page": newer, "expected_revision": accepted[revision_key]}
        )
        assert updated.status_code == 200, updated.text
        stale = client.patch(path, json={"page": document, "expected_revision": captured})
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "revision_conflict"
        assert client.get(path).json()["page"] == newer
        assert client.get(path).json()[revision_key] == updated.json()[revision_key]
        for invalid in (True, 1.0, "1", None, -1):
            refused = client.patch(path, json={"page": newer, "expected_revision": invalid})
            assert refused.status_code == 400, refused.text
        unknown = client.patch(
            path, json={"page": newer, "expected_revision": updated.json()[revision_key],
                        "typo": "must not be ignored"}
        )
        assert unknown.status_code == 400, unknown.text
        # Legacy omission remains compatible for installed clients and internal writers.
        assert client.patch(path, json={"page": newer}).status_code == 200
