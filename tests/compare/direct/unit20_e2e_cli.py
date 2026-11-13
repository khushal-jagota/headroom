"""Route unchanged browser tests through the TS HTTP CLI on its matrix leg only."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

HTTP_ROOTS = (
    "send-message",
    "worker-type",
    "skill",
    "project",
    "ticket",
    "manager",
    "worker",
    "credential",
    "measure",
    "attention",
    "measure-source",
    "preview",
    "dataset",
    "goal",
    "page",
)
CLI_TESTS = (
    "test_agent_questions_survive_reload_submit_as_one_map_and_replay_answers",
    "test_canonical_queue_writes_refresh_the_mounted_pane",
    "test_owner_send_lands_at_the_end_of_the_conversation_not_the_retained_window",
    "test_private_credential_save_survives_reload_and_reaches_the_actual_wire",
    "test_video_upload_early_send_and_native_range_playback",
    "test_history_refused_row_keeps_unread_credit_until_latest_delivery",
    "test_composing_survives_a_change_to_the_same_ticket",
    "test_html_artifact_interacts_loads_sibling_assets_and_refreshes_in_place",
    "test_a_video_preview_shows_a_poster_then_plays_and_seeks",
    "test_formula_expectation_projection_and_correction_reach_mounted_chart",
    "test_real_vite_applications_share_only_their_generation",
)


@pytest.fixture(autouse=True)
def unit20_typescript_http_cli(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    # Repository bootstrap must install runtime isolation before these imports.
    from test_runtime import validate_environment
    from tests.compare.direct.unit20_cli import CliHost, _admit
    from tests.support import server_fixtures
    from tests.support.server import PLAN_BIN, REPO_ROOT, ServerHandle

    original_start, original_run = server_fixtures._start_server, subprocess.run
    registered: dict[str, ServerHandle] = {}
    calls: list[dict[str, Any]] = []
    executables = {str(PLAN_BIN), str(REPO_ROOT / ".venv/bin/panels")}
    node_entry = str(REPO_ROOT / "server/cli/main.ts")

    def register(handle: ServerHandle) -> None:
        registered[handle.base] = handle

    def start(*args: Any, **kwargs: Any) -> ServerHandle:
        handle = original_start(*args, **kwargs)
        register(handle)
        return handle

    def admit_environment(environment: object) -> Mapping[str, str]:
        if not isinstance(environment, Mapping):
            raise ValueError("TS e2e CLI requires an explicit environment mapping")
        validate_environment(environment)
        if environment.get("PLAN_EXECUTOR_ROOT"):
            raise ValueError("TS HTTP CLI qualification cannot select executor IPC")
        temp = Path(tempfile.gettempdir()).resolve()
        for name in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"):
            value = environment.get(name)
            if not value:
                raise ValueError(f"TS e2e CLI requires a synthetic {name}")
            path = Path(value)
            if (
                not path.is_absolute()
                or path.resolve() == temp
                or not path.resolve().is_relative_to(temp)
            ):
                raise ValueError(f"TS e2e CLI refuses a non-temporary {name}")
        return environment

    def run(args: Any, *popenargs: Any, **kwargs: Any) -> Any:
        if not (
            isinstance(args, (list, tuple))
            and len(args) >= 2
            and os.fspath(args[0]) in executables
            and args[1] in HTTP_ROOTS
        ):
            return original_run(args, *popenargs, **kwargs)
        if kwargs.get("shell") or kwargs.get("executable") is not None:
            raise ValueError("TS e2e CLI requires argv without an executable override")
        environment = admit_environment(kwargs.get("env"))
        # A higher-scope fixture can return an already-started typed handle.
        for value in request.node.funcargs.values():
            if isinstance(value, ServerHandle) and value.proc.poll() is None:
                register(value)
            elif isinstance(value, server_fixtures._ReusableServer):
                if value.handle is not None and value.handle.proc.poll() is None:
                    register(value.handle)
        base = environment.get("PLAN_SERVER_URL", "")
        handle = registered.get(base)
        if handle is None:
            raise ValueError("TS e2e CLI target is not a registered fixture server")
        _admit(
            CliHost(handle, handle.db_path.parent), handle.db_path.parent / "unit20-cli-admission"
        )
        replacement = ["node", node_entry, *args[1:]]
        record: dict[str, Any] = {"argv": list(args[1:]), "entry": node_entry, "server": base}
        calls.append(record)
        completed = original_run(replacement, *popenargs, **kwargs)
        record["returncode"] = completed.returncode
        return completed

    monkeypatch.setattr(server_fixtures, "_start_server", start)
    monkeypatch.setattr(subprocess, "run", run)
    try:
        yield
    finally:
        request.node.user_properties.append(("typescript_http_cli_call_records", json.dumps(calls)))
        if getattr(request.node, "originalname", None) in CLI_TESTS and not calls:
            pytest.fail("The selected existing CLI test did not execute the TypeScript CLI")
