"""Real temporary APIs must follow the Worker environment on both platforms."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from planner.conversation.worker_scratch import WorkerScratch


def test_child_tempfile_defaults_stay_private_and_expire_with_process_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from tests.support.conversation_codex_app_server_bench import _resolved_start

    from planner.conversation.contracts import ConversationRoleMaterials
    from planner.conversation.worker_scratch import with_worker_files_root

    monkeypatch.chdir(tmp_path)
    start = replace(_resolved_start(tmp_path), role_materials=ConversationRoleMaterials(
        role_text="Worker", identity_environment_variables=(
            ("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "ticket"),
        ),
    ))
    bound = with_worker_files_root(start, "data/planner.db")
    assert bound.role_materials is not None
    environment = {**os.environ, **dict(bound.role_materials.identity_environment_variables)}
    scratch = WorkerScratch(environment)
    child_cwd = tmp_path / "different-cwd"
    child_cwd.mkdir()
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import tempfile,json; "
             "print(json.dumps([tempfile.gettempdir(), tempfile.mkstemp()[1]]))"],
            env=scratch.environment, cwd=child_cwd, text=True, capture_output=True, check=True,
        )
        default, file = json.loads(result.stdout)
        assert Path(default) == scratch.directory
        assert Path(file).parent == scratch.directory
        assert scratch.directory.parent == tmp_path / "data/files/tickets/ticket/tmp"
        assert scratch.directory.stat().st_mode & 0o777 == 0o700
        assert scratch.environment["TMP"] == scratch.environment["TEMP"] == default
    finally:
        scratch.close()
    assert not scratch.directory.exists()


def test_respawn_binds_worker_folder_to_current_database_before_executor_rebind(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from tests.support.conversation_codex_app_server_bench import _resolved_start

    from planner.conversation.contracts import ConversationRoleMaterials
    from planner.conversation.worker_scratch import with_worker_files_root

    # A persisted conversation predates the new environment entry.
    start = replace(_resolved_start(tmp_path), role_materials=ConversationRoleMaterials(
        role_text="Worker", identity_environment_variables=(
            ("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "ticket"),
        ),
    ))
    bound = with_worker_files_root(start, str(tmp_path / "data" / "planner.db"))
    assert bound.role_materials is not None
    identity = dict(bound.role_materials.identity_environment_variables)
    assert identity["PLAN_TICKET_FILES_DIR"] == str(
        tmp_path / "data" / "files" / "tickets" / "ticket"
    )
    chief = replace(start, role_materials=ConversationRoleMaterials(
        role_text="Chief", identity_environment_variables=(("PLAN_ACTOR", "chief"),),
    ))
    assert with_worker_files_root(chief, str(tmp_path / "planner.db")) is chief


def test_retirement_scans_repository_temporaries_but_excludes_owned_scratch(tmp_path: Path) -> None:
    from planner.ticket_workspaces.contracts import OWNERSHIP_FILE
    from planner.ticket_workspaces.filesystem import _checkouts

    root = tmp_path / "ticket"
    (root / "tmp" / "research" / ".git").mkdir(parents=True)
    important = root / "worktree" / "tmp" / "research"
    (important / ".git").mkdir(parents=True)
    assert set(_checkouts(root)) == {root / "tmp" / "research", important}
    (root / OWNERSHIP_FILE).write_text("{}")
    assert _checkouts(root) == [important]
