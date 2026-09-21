from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from planner.environments.app import (
    AppValidationError,
    build_exported_app,
    digest_app_artifact,
    digest_app_source,
    validate_app_manifest,
)
from planner.environments.app_launcher import build_app_launch_env

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_real_file_mutation_still_invalidates_manifest_with_pytest_cache(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    cache = app / ".pytest_cache" / "v" / "cache" / "lastfailed"
    cache.parent.mkdir(parents=True)
    cache.write_text("test state", encoding="utf-8")
    (app / "web" / "dist" / "index.html").write_text("tampered", encoding="utf-8")

    with pytest.raises(AppValidationError, match="artifact digest"):
        validate_app_manifest(app / "manifest.json")


def test_app_launcher_carries_identity_and_scrubs_unrelated_environment(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    env = build_app_launch_env(
        app,
        ambient={
            "HOME": "/Users/operator",
            "PATH": "/bin",
            "PLAN_DB_PATH": "/state/db",
            "PLAN_GROQ_API_KEY": "groq-secret",
            "PLAN_CLAUDE_EXECUTABLE": "/operator/claude",
            "SECRET": "no",
        },
    )
    assert env == {
        "HOME": "/Users/operator",
        "PATH": "/bin",
        "PLAN_DB_PATH": "/state/db",
        "PLAN_GROQ_API_KEY": "groq-secret",
        "PLAN_CLAUDE_EXECUTABLE": "/operator/claude",
        "PLAN_APP_SHA": SHA,
        "PLAN_APP_ROOT": str(app),
    }


def test_runtime_app_validation_requires_runnable_tree(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    (app / "bin" / "panels-launcher").chmod(0o644)
    _rewrite_digest(app)
    with pytest.raises(AppValidationError, match="executable"):
        validate_app_manifest(app / "manifest.json", require_runtime=True)


def test_exported_cli_is_relocatable_and_preserves_caller_context(
    tmp_path: Path,
) -> None:
    source, sha = _git_source(tmp_path)
    candidate = tmp_path / "candidate"
    build_exported_app(source, requested_sha=sha, candidate_app=candidate)
    assert not (candidate / ".git").exists()
    python = candidate / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$PLAN_APP_ROOT\" \"$PLAN_APP_SHA\" \"$PLAN_SERVER_URL\" "
        "\"$PLAN_TICKET_ID\" \"$PLAN_ACTOR\" \"$PYTHONPATH\" \"$@\"\n"
        "exit 23\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    deployed = tmp_path / "installed" / "app"
    deployed.parent.mkdir()
    shutil.move(candidate, deployed)
    outside = tmp_path / "outside"
    outside.mkdir()

    result = subprocess.run(
        [str(deployed / "bin" / "panels"), "worker", "my-ticket", "--json"],
        cwd=outside,
        env={
            "PATH": "/usr/bin:/bin",
            "PLAN_SERVER_URL": "http://panels.test",
            "PLAN_TICKET_ID": "t_worker",
            "PLAN_ACTOR": "worker",
            "PLAN_APP_ROOT": "/ambient/wrong-app",
            "PLAN_APP_SHA": SHA,
            "PYTHONPATH": "/ambient/package",
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 23
    assert result.stdout.splitlines() == [
        str(deployed.resolve()),
        sha,
        "http://panels.test",
        "t_worker",
        "worker",
        "/ambient/package",
        "-I",
        "-m",
        "planner",
        "worker",
        "my-ticket",
        "--json",
    ]


def _git_source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "README.md").write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return source, sha


def _legacy_cached_app(app: Path) -> tuple[Path, Path]:
    build_cache = (
        app
        / ".venv"
        / "lib"
        / "python3.13"
        / "site-packages"
        / "package"
        / "__pycache__"
        / "built.pyc"
    )
    build_cache.parent.mkdir(parents=True)
    build_cache.write_bytes(b"built during install")
    stable = app / "stable.txt"
    stable.write_text("stable", encoding="utf-8")
    legacy = hashlib.sha256()
    for path in sorted((build_cache, stable)):
        legacy.update(path.relative_to(app).as_posix().encode())
        legacy.update(b"\0")
        legacy.update(path.read_bytes())
        legacy.update(b"\0")
    manifest = app / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "panels-app-v1",
                "app_sha": SHA,
                "source_digest": "a" * 64,
                "artifact_digest": legacy.hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    os.utime(build_cache, ns=(1_000_000_000, 1_000_000_000))
    os.utime(stable, ns=(1_000_000_000, 1_000_000_000))
    os.utime(manifest, ns=(2_000_000_000, 2_000_000_000))
    runtime_cache = build_cache.with_name("runtime.pyc")
    runtime_cache.write_bytes(b"created after deployment")
    os.utime(runtime_cache, ns=(3_000_000_000, 3_000_000_000))
    return manifest, runtime_cache


def _runtime_app(app: Path) -> Path:
    app.mkdir()
    python = app / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    planner_file = app / "src" / "planner" / "__init__.py"
    planner_file.parent.mkdir(parents=True)
    planner_file.write_text("", encoding="utf-8")
    python.write_text(f"#!/bin/sh\nprintf '%s\\n' '{planner_file}'\n", encoding="utf-8")
    python.chmod(0o755)
    launcher = app / "bin" / "panels-launcher"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    cli = app / "bin" / "panels"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    cli.chmod(0o755)
    (app / "web" / "dist").mkdir(parents=True)
    (app / "web" / "dist" / "index.html").write_text("ok", encoding="utf-8")
    (app / "agent_backends" / "node_modules").mkdir(parents=True)
    (app / "agent_backends" / "node_modules" / ".package-lock.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (app / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-app-v1",
                "app_sha": SHA,
                "source_digest": digest_app_source(app),
                "artifact_digest": digest_app_artifact(app),
            }
        ),
        encoding="utf-8",
    )
    return app


def _rewrite_digest(app: Path) -> None:
    value = json.loads((app / "manifest.json").read_text(encoding="utf-8"))
    value["artifact_digest"] = digest_app_artifact(app)
    (app / "manifest.json").write_text(json.dumps(value), encoding="utf-8")
