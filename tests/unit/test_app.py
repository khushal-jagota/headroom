from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from planner.core.config import load_config
from planner.core.server import resolve_application_root
from planner.environments.app import (
    AppValidationError,
    build_exported_app,
    digest_app_artifact,
    digest_app_source,
    validate_app_manifest,
)
from planner.environments.app_launcher import build_app_launch_env

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_app_runtime_resolves_assets_from_app_root(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    installed_module = app_root / ".venv/lib/python3.13/site-packages/planner/core/server.py"
    assert resolve_application_root(
        environment={"PLAN_APP_ROOT": str(app_root)}, module_file=installed_module
    ) == app_root.resolve()


def test_checkout_runtime_resolves_assets_from_source_tree(tmp_path: Path) -> None:
    module_file = tmp_path / "repo/src/planner/core/server.py"
    assert resolve_application_root(environment={}, module_file=module_file) == (
        tmp_path / "repo"
    ).resolve()


def test_app_manifest_requires_full_sha_and_matching_digest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "panels-app-v1",
                "app_sha": SHA,
                "source_digest": "a" * 64,
                "artifact_digest": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AppValidationError, match="artifact digest"):
        validate_app_manifest(manifest_path)


def test_nested_manifest_is_covered_by_app_digest(tmp_path: Path) -> None:
    app = tmp_path / "app"
    nested = app / "package" / "manifest.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("first", encoding="utf-8")
    digest = digest_app_artifact(app)
    nested.write_text("tampered", encoding="utf-8")
    assert digest_app_artifact(app) != digest


def test_nested_git_metadata_is_rejected_even_when_excluded_from_digest(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    nested_git = app / "vendor" / ".git" / "config"
    nested_git.parent.mkdir(parents=True)
    nested_git.write_text("tampered", encoding="utf-8")
    with pytest.raises(AppValidationError, match="Git metadata"):
        validate_app_manifest(app / "manifest.json")


def test_export_is_git_free_and_records_exact_checkout(tmp_path: Path) -> None:
    source, sha = _git_source(tmp_path)
    candidate = tmp_path / "candidate"
    manifest = build_exported_app(source, requested_sha=sha, candidate_app=candidate)
    assert manifest.app_sha == sha
    assert len(manifest.source_digest) == 64
    assert not (candidate / ".git").exists()
    assert validate_app_manifest(candidate / "manifest.json").app_sha == sha


def test_export_rejects_checkout_at_different_sha(tmp_path: Path) -> None:
    source, _ = _git_source(tmp_path)
    with pytest.raises(AppValidationError, match="requested SHA"):
        build_exported_app(source, requested_sha=SHA, candidate_app=tmp_path / "candidate")


def test_export_accepts_already_validated_same_app(tmp_path: Path) -> None:
    source, sha = _git_source(tmp_path)
    candidate = tmp_path / "candidate"
    first = build_exported_app(source, requested_sha=sha, candidate_app=candidate)
    assert build_exported_app(source, requested_sha=sha, candidate_app=candidate) == first


def test_app_launcher_carries_identity_and_scrubs_unrelated_environment(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    env = build_app_launch_env(
        app,
        ambient={
            "HOME": "/Users/operator",
            "PATH": "/bin",
            "PLAN_DB_PATH": "/state/db",
            "SECRET": "no",
        },
    )
    assert env == {
        "HOME": "/Users/operator",
        "PATH": "/bin",
        "PLAN_DB_PATH": "/state/db",
        "PLAN_APP_SHA": SHA,
        "PLAN_APP_ROOT": str(app),
    }


def test_config_keeps_development_identity_explicit() -> None:
    assert load_config(env={"PLAN_TEST_MODE": "1"}).app_sha is None
    assert load_config(env={"PLAN_TEST_MODE": "1", "PLAN_APP_SHA": SHA}).app_sha == SHA


def test_runtime_app_validation_requires_runnable_tree(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    (app / "bin" / "panels-launcher").chmod(0o644)
    _rewrite_digest(app)
    with pytest.raises(AppValidationError, match="executable"):
        validate_app_manifest(app / "manifest.json", require_runtime=True)


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
