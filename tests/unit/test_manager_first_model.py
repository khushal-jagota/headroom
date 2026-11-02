"""Manager defaults must survive the real first-message path to the Codex wire.

The existing Manager lifecycle tests use an in-memory conversation substitute. This
case reuses their app host with the production conversation system and Codex adapter.
The scripted provider records the exact model at the child-process boundary.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.support.conversation_codex_scripted_app_server import scripted_app_server_launch
from tests.unit.test_sprint_item_supervisors import _app, _create_item

from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChildFactory,
    CodexChildLaunch,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.core.db import connect


def _codex_app(tmp_path: Path) -> tuple[Any, Path, Path]:
    transcript = tmp_path / "wire.jsonl"
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"transcript_path": str(transcript), "turns": [{}]}))
    argv, environment = scripted_app_server_launch(script)
    factory = CodexAppServerBackendChildFactory(CodexChildLaunch(argv, tuple(environment.items())))
    app, db_path = _app(
        tmp_path, backend_child_factories={key: factory for key in ConversationBackendKey}
    )
    return app, db_path, transcript


def _turns(transcript: Path) -> list[dict[str, Any]]:
    if not transcript.exists():
        return []
    return [
        entry["received"]
        for line in transcript.read_text().splitlines()
        if (entry := json.loads(line)).get("received", {}).get("method") == "turn/start"
    ]


@pytest.mark.parametrize("with_goal", [False, True])
def test_manager_first_message_sends_concrete_default_to_codex(
    tmp_path: Path, with_goal: bool
) -> None:
    app, db_path, transcript = _codex_app(tmp_path)
    with TestClient(app) as client:
        if with_goal:
            response = client.post(
                "/api/managers",
                json={
                    "title": "Starts with its goal",
                    "project_id": "project_vylo",
                    "goal": "Reply with OK.",
                },
            )
            assert response.status_code == 200, response.text
            manager = response.json()
        else:
            manager = _create_item(client)
            response = client.post(
                f"/api/managers/{manager['id']}/conversation/send",
                json={
                    "content": [{"piece": "text", "text": "Reply with OK."}],
                    "sender_label": "owner",
                },
            )
            assert response.status_code == 200, response.text
        deadline = time.monotonic() + 5
        while not _turns(transcript) and time.monotonic() < deadline:
            time.sleep(0.01)
        turns = _turns(transcript)
        assert len(turns) == 1
        assert turns[0]["params"]["model"] == "gpt-6.1-sol"
        with connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT manager_model FROM managers WHERE id=?", (manager["id"],)
            ).fetchone()
            assert row[0] == "gpt-6.1-sol"


def test_legacy_manager_sol_fails_before_codex_with_repair_action(tmp_path: Path) -> None:
    app, db_path, transcript = _codex_app(tmp_path)
    with TestClient(app) as client:
        manager = _create_item(client)
        with connect(str(db_path)) as conn:
            conn.execute("UPDATE managers SET manager_model='sol' WHERE id=?", (manager["id"],))
            conn.commit()
        response = client.post(
            f"/api/managers/{manager['id']}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Reply with OK."}],
                "sender_label": "owner",
            },
        )
        assert response.status_code == 400, response.text
        assert "model" in response.text.lower()
        assert "choose" in response.text.lower()
        assert _turns(transcript) == []
        with connect(str(db_path)) as conn:
            assert (
                conn.execute(
                    "SELECT conversation_id FROM agents WHERE agent_key=?",
                    (f"manager_{manager['id']}",),
                ).fetchone()[0]
                is None
            )
        saved = client.put(
            f"/api/managers/{manager['id']}/launch-defaults",
            json={
                "employee_backend": "codex",
                "employee_launch_model": "gpt-6.1-sol",
                "employee_launch_reasoning_effort": "medium",
            },
        )
        assert saved.status_code == 200, saved.text
        repaired = client.post(
            f"/api/managers/{manager['id']}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Reply with OK."}],
                "sender_label": "owner",
            },
        )
        assert repaired.status_code == 200, repaired.text
        deadline = time.monotonic() + 5
        while not _turns(transcript) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _turns(transcript)[0]["params"]["model"] == "gpt-6.1-sol"


@pytest.mark.real_provider_homes
@pytest.mark.skipif(
    os.environ.get("PANELS_REAL_CODEX_TESTS") != "1",
    reason="set PANELS_REAL_CODEX_TESTS=1 for the isolated real-account Manager smoke",
)
def test_manager_first_message_real_codex_account(tmp_path: Path) -> None:
    """Account acceptance through the Manager path, in an isolated app and database."""
    import shutil

    from planner.conversation.backends.codex_app_server.adapter import codex_app_server_child_launch

    executable = shutil.which("codex")
    assert executable is not None
    factory = CodexAppServerBackendChildFactory(
        codex_app_server_child_launch(
            codex_executable=Path(executable),
            panels_server_url="http://127.0.0.1:1",
        )
    )
    app, db_path = _app(
        tmp_path, backend_child_factories={key: factory for key in ConversationBackendKey}
    )
    with TestClient(app) as client:
        manager = _create_item(client, "Isolated first-message model proof")
        response = client.post(
            f"/api/managers/{manager['id']}/conversation/send",
            json={
                "content": [
                    {
                        "piece": "text",
                        "text": (
                            "This is an isolated model acceptance test. "
                            "Reply with exactly MODEL_OK. "
                            "Do not use tools or change anything."
                        ),
                    }
                ],
                "sender_label": "owner",
            },
        )
        assert response.status_code == 200, response.text
        conversation_id = response.json()["conversation_id"]
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            with connect(str(db_path)) as conn:
                endings = conn.execute(
                    "SELECT payload FROM conversation_events WHERE conversation_id=? "
                    "AND kind='turn_ended' ORDER BY sequence",
                    (conversation_id,),
                ).fetchall()
            if endings:
                ending = json.loads(endings[-1][0])
                assert ending["ending"] == "completed", ending
                print(
                    f"Real-account Manager first-message completed: {conversation_id}, "
                    f"gpt-6.1-sol, {db_path}"
                )
                break
            time.sleep(0.1)
        else:
            pytest.fail("Real-account Manager did not finish its first message within 150 seconds")
