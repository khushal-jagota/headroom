from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from planner.environments.contracts import (
    EnvironmentCredentialPolicy,
    EnvironmentValidationError,
)
from planner.environments.logic.credentials import parse_environment_file
from planner.environments.logic.launch_env import (
    build_environment_run_env,
    build_test_environment_run_env,
)
from planner.environments.logic.registry import resolve_environment_instance


def test_environment_file_parses_comments_blank_lines_and_quoted_values(tmp_path: Path) -> None:
    env_file = tmp_path / "credentials.env"
    env_file.write_text(
        "\n".join(
            [
                "# provider credentials for this instance",
                "",
                "ANTHROPIC_API_KEY=anthropic-secret",
                'OPENAI_API_KEY="openai secret"',
                "GOOGLE_API_KEY='google secret'",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_environment_file(env_file, kind="staging") == {
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "OPENAI_API_KEY": "openai secret",
        "GOOGLE_API_KEY": "google secret",
    }


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("ANTHROPIC_API_KEY", "malformed"),
        ("=secret", "malformed"),
        ("bad-key=value", "malformed"),
        ("ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two", "duplicate"),
        ("STRIPE_SECRET_KEY=secret", "unknown"),
        ("PLAN_DB_PATH=/tmp/poison.db", "forbidden"),
        ("PLAN_TEST_MODE=1", "forbidden"),
        ("PLAN_GATEWAY_ADAPTER=fake", "forbidden"),
        ("PLAN_FAKE_NOW=2026-07-04T12:00:00", "forbidden"),
        ("HERMES_HOME=/tmp/home", "forbidden"),
        ("HERMES_SESSION_KEY=sess", "forbidden"),
        ("PYTHONPATH=/tmp/src", "forbidden"),
        ("HOME=/tmp/home", "forbidden"),
        ("PWD=/tmp/repo", "forbidden"),
        ("USER=panels-live", "forbidden"),
    ],
)
def test_environment_file_rejects_unsafe_lines(tmp_path: Path, body: str, message: str) -> None:
    env_file = tmp_path / "credentials.env"
    env_file.write_text(body, encoding="utf-8")

    with pytest.raises(EnvironmentValidationError, match=message):
        parse_environment_file(env_file, kind="staging")


def test_default_environment_policy_keeps_tailscale_setup_outside_runtime(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "credentials.env"
    env_file.write_text("TAILSCALE_AUTHKEY=tskey-secret", encoding="utf-8")

    with pytest.raises(EnvironmentValidationError, match="unknown"):
        parse_environment_file(env_file, kind="live")


def test_environment_file_supports_explicit_production_only_policy(tmp_path: Path) -> None:
    env_file = tmp_path / "credentials.env"
    env_file.write_text("EXAMPLE_LIVE_ONLY=secret", encoding="utf-8")
    policy = EnvironmentCredentialPolicy(
        allowed_keys=frozenset({"EXAMPLE_LIVE_ONLY"}),
        production_only_keys=frozenset({"EXAMPLE_LIVE_ONLY"}),
    )

    with pytest.raises(EnvironmentValidationError, match="production-only"):
        parse_environment_file(env_file, kind="staging", policy=policy)

    assert parse_environment_file(env_file, kind="live", policy=policy) == {
        "EXAMPLE_LIVE_ONLY": "secret"
    }


def test_launch_environment_scrubs_ambient_and_adds_contract_values(
    tmp_path: Path,
) -> None:
    instance = _staging_instance(tmp_path)
    ambient = {
        "LANG": "en_GB.UTF-8",
        "LC_ALL": "en_GB.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TERM": "xterm-256color",
        "TMPDIR": str(tmp_path / "tmp"),
        "HOME": str(tmp_path / "ambient-home"),
        "PYTHONPATH": "/tmp/poison",
        "PLAN_DB_PATH": "/tmp/poison.db",
        "PLAN_ACTOR": "chief",
        "PLAN_TICKET_ID": "t_poison",
        "PLAN_TEST_MODE": "1",
        "PLAN_GATEWAY_ADAPTER": "real",
        "PLAN_FAKE_NOW": "2099-01-01T00:00:00",
        "HERMES_HOME": "/tmp/hermes",
        "HERMES_SESSION_KEY": "sess_poison",
        "ANTHROPIC_API_KEY": "ambient-secret",
    }

    run_env = build_environment_run_env(
        instance,
        credentials={"ANTHROPIC_API_KEY": "file-secret"},
        ambient=ambient,
        hermes_python=Path("/operator-hermes/bin/python"),
        runtime_port=43123,
    )

    assert run_env["LANG"] == "en_GB.UTF-8"
    assert run_env["LC_ALL"] == "en_GB.UTF-8"
    assert run_env["PATH"] == "/usr/bin:/bin"
    assert run_env["TERM"] == "xterm-256color"
    assert run_env["TMPDIR"] == str(tmp_path / "tmp")
    assert run_env["HOME"] == str(tmp_path / "ambient-home")
    assert run_env["ANTHROPIC_API_KEY"] == "file-secret"
    assert run_env["PLAN_DB_PATH"] == str(instance.db_path)
    assert run_env["PLAN_PORT"] == "43123"
    assert run_env["PLAN_LOGS_DIR"] == str(instance.logs_dir)
    assert run_env["PLAN_DISPATCHER_LOCK_PATH"] == str(instance.dispatcher_lock_path)
    assert run_env["PLAN_SERVER_CONTROL_SOCKET"] == str(instance.server_control_socket_path)
    assert run_env["PLAN_HERMES_PYTHON"] == "/operator-hermes/bin/python"

    assert "PYTHONPATH" not in run_env
    assert "PLAN_ACTOR" not in run_env
    assert "PLAN_TICKET_ID" not in run_env
    assert "PLAN_TEST_MODE" not in run_env
    assert "PLAN_GATEWAY_ADAPTER" not in run_env
    assert "PLAN_FAKE_NOW" not in run_env
    assert "HERMES_HOME" not in run_env
    assert "HERMES_SESSION_KEY" not in run_env
    assert "PLAN_HERMES_HOME" not in run_env


def test_hidden_test_launch_seam_injects_fake_runtime_itself(tmp_path: Path) -> None:
    instance = _staging_instance(tmp_path)
    ambient = {
        "PATH": "/usr/bin:/bin",
        "PLAN_TEST_MODE": "0",
        "PLAN_GATEWAY_ADAPTER": "real",
        "PLAN_FAKE_NOW": "2099-01-01T00:00:00",
        "PLAN_WS_POLL_MS": "9999",
        "PLAN_WS_HEARTBEAT_MS": "9999",
        "PLAN_UI_DEBOUNCE_MS": "9999",
        "PLAN_TICK_SECONDS": "9999",
    }

    run_env = build_test_environment_run_env(
        instance,
        credentials={},
        ambient=ambient,
        hermes_python=Path("/operator-hermes/bin/python"),
        runtime_port=43124,
    )

    assert run_env["PLAN_TEST_MODE"] == "1"
    assert "PLAN_GATEWAY_ADAPTER" not in run_env
    assert run_env["PLAN_FAKE_NOW"] == "2026-07-04T12:00:00+00:00"
    assert run_env["PLAN_WS_POLL_MS"] == "50"
    assert run_env["PLAN_WS_HEARTBEAT_MS"] == "500"
    assert run_env["PLAN_UI_DEBOUNCE_MS"] == "50"
    assert run_env["PLAN_TICK_SECONDS"] == "1"


def _staging_instance(tmp_path: Path):
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    subprocess.run(["git", "init", "-q", str(repository_root)], check=True)
    return resolve_environment_instance(
        kind="staging",
        environment_root=_short_environment_root(tmp_path),
        allowed_repository_roots=(repository_root,),
        requested_repository_roots=(repository_root,),
        prepared=True,
    )


def _short_environment_root(tmp_path: Path) -> Path:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return Path("/tmp") / f"pe-credentials-{os.getpid()}-{digest}"
