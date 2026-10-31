"""Configured workspace roots stay local to one application and preserve precedence."""

from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
from sqlite3 import Connection

import pytest
import yaml

from planner.core import server
from planner.core.clock import TestClock as Clock
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL, ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.goals.actions import create_goal
from planner.goals.conversation import resolve_goal
from planner.managers.data import create_manager
from planner.projects.data import create_project
from planner.runtime.conversation_start import agent_resolve, manager_resolve, worker_resolve
from planner.runtime.workspaces import register_workspace_root, workspace_root_for_connection
from planner.tickets.data import create_ticket, read_ticket


def test_workspace_configuration_precedence_and_canonical_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured"
    configured.mkdir()
    override = tmp_path / "override"
    override.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(override, target_is_directory=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"workspace_root": str(configured)}))
    assert load_config(str(config_path), env={}).workspace_root == configured
    assert (
        load_config(str(config_path), env={"PLAN_WORKSPACE_ROOT": str(alias)}).workspace_root
        == override
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        load_config(str(config_path), env={"PLAN_WORKSPACE_ROOT": "~/override"}).workspace_root
        == override
    )
    config_path.write_text("workspace_root: null\n")
    assert load_config(str(config_path), env={}).workspace_root is None
    config_path.write_text("{}\n")
    assert load_config(str(config_path), env={}).workspace_root is None


@pytest.mark.parametrize("invalid", ["", "relative", "missing", "file", 12, False, []])
def test_invalid_yaml_workspace_never_falls_back(tmp_path: Path, invalid: object) -> None:
    if invalid in ("missing", "file"):
        path = tmp_path / str(invalid)
        if invalid == "file":
            path.write_text("not a directory")
        invalid = str(path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"workspace_root": invalid}))
    with pytest.raises(PlannerError, match="workspace_root") as caught:
        load_config(str(config_path), env={})
    assert caught.value.code is ErrorCode.validation


def test_invalid_environment_overrides_valid_yaml_and_resolution_failures_are_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"workspace_root": str(tmp_path)}))
    with pytest.raises(PlannerError, match="workspace_root"):
        load_config(str(config_path), env={"PLAN_WORKSPACE_ROOT": ""})
    original_resolve = Path.resolve

    def fail_workspace(self: Path, strict: bool = False) -> Path:
        if self == tmp_path:
            raise OSError("synthetic path resolution failure")
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_workspace)
    with pytest.raises(PlannerError, match="workspace_root") as caught:
        load_config(str(config_path), env={})
    assert caught.value.code is ErrorCode.validation


def test_all_agent_resolvers_keep_explicit_and_project_precedence(
    tmp_db: Connection, tmp_path: Path, fake_clock: Clock
) -> None:
    configured = tmp_path / "configured"
    project_folder = tmp_path / "project"
    explicit = tmp_path / "explicit"
    for workspace_root in (configured, project_folder, explicit):
        workspace_root.mkdir()
    project = create_project(tmp_db, name="Workspace", folder_path=project_folder, now=1)
    manager = create_manager(tmp_db, title="Manager", project_id=project.id, clock=fake_clock)
    goal = create_goal(
        tmp_db,
        {"project_id": project.id, "title": "Goal", "kind": "binary",
         "primary_checklist_block_id": None},
        principal=OWNER_PRINCIPAL, now=1
    )
    ticket = create_ticket(
        tmp_db,
        title="Worker",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    with register_workspace_root(path, configured):
        assert agent_resolve(tmp_db).workspace_folder == configured
        assert manager_resolve(tmp_db, manager).workspace_folder == configured
        assert resolve_goal(tmp_db, goal.id).workspace_folder == configured
        assert worker_resolve(tmp_db, ticket).workspace_folder == configured
        assert agent_resolve(tmp_db, workspace_folder=explicit).workspace_folder == explicit
        assert (
            manager_resolve(tmp_db, manager, workspace_folder=explicit).workspace_folder == explicit
        )
        assert resolve_goal(tmp_db, goal.id, workspace_folder=explicit).workspace_folder == explicit
        tmp_db.execute("UPDATE tickets SET project_id=? WHERE id=?", (project.id, ticket.id))
        ticket = read_ticket(tmp_db, ticket.id)
        assert worker_resolve(tmp_db, ticket).workspace_folder == project_folder
        assert (
            worker_resolve(tmp_db, ticket, workspace_folder=explicit).workspace_folder == explicit
        )
        project_folder.rmdir()
        assert worker_resolve(tmp_db, ticket).workspace_folder == configured


def test_unconfigured_workspace_preserves_both_existing_fallbacks(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preferred = tmp_path / "projects"
    application = tmp_path / "app"
    application.mkdir()
    monkeypatch.setattr(server, "_PREFERRED_WORKER_WORKSPACE_ROOT", preferred)
    monkeypatch.setattr(server, "_REPO_ROOT", application)
    assert agent_resolve(tmp_db).workspace_folder == application
    preferred.mkdir()
    assert agent_resolve(tmp_db).workspace_folder == preferred


def test_lifespans_isolate_databases_and_reject_duplicate_database_aliases(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "workspace"
    configured.mkdir()
    apps = []
    paths = []
    for index in range(2):
        state = tmp_path / str(index)
        state.mkdir()
        path = state / "planner.db"
        with closing(connect(str(path))) as conn:
            create_schema(conn)
        env = {
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(path),
            "PLAN_LOGS_DIR": str(state / "logs"),
        }
        if index == 0:
            env["PLAN_WORKSPACE_ROOT"] = str(configured)
        config = load_config(str(state / "absent.yaml"), env=env)
        def conn_factory(path: Path = path) -> Connection:
            return connect(str(path))

        apps.append(server.create_app(config, build_clock(config), conn_factory))
        paths.append(path)

    async def exercise() -> None:
        first, second = apps
        async with first.router.lifespan_context(first), second.router.lifespan_context(second):
            with closing(connect(str(paths[0]))) as conn:
                assert workspace_root_for_connection(conn) == configured
            with closing(connect(str(paths[1]))) as conn:
                assert workspace_root_for_connection(conn) is None
                assert (
                    agent_resolve(conn).workspace_folder == server.resolve_worker_workspace_root()
                )
            alias = tmp_path / "database-alias"
            alias.symlink_to(paths[0])
            with pytest.raises(RuntimeError, match="already"):
                register_workspace_root(str(alias), None)
        for path in paths:
            with register_workspace_root(str(path), None):
                pass
        assert (configured / ".agents/skills").is_symlink()

    asyncio.run(asyncio.wait_for(exercise(), 15))


def test_skill_conflict_unwinds_real_application_startup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    conflict = workspace / ".agents/skills"
    conflict.mkdir(parents=True)
    retained = conflict / "custom.txt"
    retained.write_text("keep this")
    path = tmp_path / "state/planner.db"
    path.parent.mkdir()
    with closing(connect(str(path))) as conn:
        create_schema(conn)
    config = load_config(
        str(tmp_path / "absent.yaml"),
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(path),
            "PLAN_WORKSPACE_ROOT": str(workspace),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = server.create_app(config, build_clock(config), lambda: connect(str(path)))

    async def exercise() -> None:
        with pytest.raises(FileExistsError, match=str(conflict)):
            async with app.router.lifespan_context(app):
                raise AssertionError("conflict must fail before lifespan yields")
        assert retained.read_text() == "keep this"
        conflict.rename(conflict.with_name("saved-skills"))
        # A second ordinary start needs both the workspace and Signal registrations released.
        restarted = server.create_app(config, build_clock(config), lambda: connect(str(path)))
        async with restarted.router.lifespan_context(restarted):
            assert conflict.is_symlink()

    asyncio.run(asyncio.wait_for(exercise(), 15))
