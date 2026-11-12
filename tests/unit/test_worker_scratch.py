"""Real temporary APIs must follow the Worker environment on both platforms."""

import json
import os
import subprocess
import sys
from pathlib import Path

from planner.conversation.worker_scratch import WorkerScratch


def test_child_tempfile_defaults_stay_private_and_expire_with_process_owner(tmp_path: Path) -> None:
    scratch = WorkerScratch({**os.environ, "PLAN_TICKET_FILES_DIR": str(tmp_path)})
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import tempfile,json; "
             "print(json.dumps([tempfile.gettempdir(), tempfile.mkstemp()[1]]))"],
            env=scratch.environment, text=True, capture_output=True, check=True,
        )
        default, file = json.loads(result.stdout)
        assert Path(default) == scratch.directory
        assert Path(file).parent == scratch.directory
        assert scratch.directory.parent == tmp_path / "tmp"
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
