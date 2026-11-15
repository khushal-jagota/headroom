"""Native document metadata must protect the exact document inside its writer."""

import json
from copy import deepcopy
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.api_app import _app
from tests.unit.test_goal_pages import create

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
    fixture_path = root / "ios/PanelsTests/Fixtures/document-revision-requests.json"
    fixture = json.loads(fixture_path.read_text())
    request = next(item for item in fixture["cases"] if item["kind"] == kind)
    captured_revision = request["expected_revision"]
    assert type(captured_revision) is int
    if kind == "goal":
        goal = create(tmp_db)
        path, revision_key = request["path"].replace("{goal_id}", goal.id), "revision"
        tmp_db.execute("UPDATE goals SET revision=? WHERE id=?", (captured_revision, goal.id))
    elif kind == "chief":
        path, revision_key = request["path"], "page_revision"
        tmp_db.execute(
            "UPDATE chief_page SET page_revision=? WHERE singleton=1", (captured_revision,)
        )
    else:
        manager = create_manager(
            tmp_db, title="Guide", project_id="project_vylo", clock=fake_clock
        )
        path = request["path"].replace("{manager_id}", manager.id)
        revision_key = "page_revision"
        tmp_db.execute(
            "UPDATE managers SET page_revision=? WHERE id=?", (captured_revision, manager.id)
        )
    tmp_db.commit()
    with TestClient(_app(tmp_db), headers={"X-Panels-iOS-Build": "5"}) as client:
        captured = client.get(path).json()[revision_key]
        assert captured == captured_revision
        # Swift checks its actual PageSession PATCH bytes against this same fixture.
        document = fixture["page"]
        body = {"page": document, "expected_revision": captured_revision}
        first = client.request("PATCH", path, json=body)
        assert first.status_code == 200, first.text
        accepted = first.json()
        assert accepted[revision_key] == captured + 1
        newer = deepcopy(accepted["page"])
        newer["title"] = "Newer document"
        updated = client.patch(
            path, json={"page": newer, "expected_revision": accepted[revision_key]}
        )
        assert updated.status_code == 200, updated.text
        stale = client.request("PATCH", path, json=body)
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "revision_conflict"
        # Native retries retain the exact captured request instead of silently rebasing.
        assert client.request("PATCH", path, json=body).status_code == 409
        assert client.get(path).json()["page"] == newer
        assert client.get(path).json()[revision_key] == updated.json()[revision_key]
        for invalid in (True, 1.0, "1", None, -1):
            refused = client.patch(path, json={"page": newer, "expected_revision": invalid})
            assert refused.status_code == 400, refused.text
        unknown = client.patch(
            path,
            json={
                "page": newer,
                "expected_revision": updated.json()[revision_key],
                "typo": "must not be ignored",
            },
        )
        assert unknown.status_code == 400, unknown.text
        # Legacy omission remains compatible for installed clients and internal writers.
        assert client.patch(path, json={"page": newer}).status_code == 200
