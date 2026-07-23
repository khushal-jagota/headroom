from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from planner.core.config import load_config
from planner.environments.release import (
    ReleaseValidationError,
    build_exported_release,
    digest_release_artifact,
    digest_release_source,
    validate_release_manifest,
)
from planner.environments.release_launcher import build_release_launch_env

SHA = "0123456789abcdef0123456789abcdef01234567"


def test_release_manifest_requires_a_full_sha_and_matching_digest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": SHA,
                "source_digest": "a" * 64,
                "artifact_digest": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseValidationError, match="artifact digest"):
        validate_release_manifest(manifest_path)


def test_export_rejects_checkout_at_a_different_sha(tmp_path: Path) -> None:
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
    with pytest.raises(ReleaseValidationError, match="requested SHA"):
        build_exported_release(source, requested_sha=SHA, release_root=tmp_path / "release")


def test_export_is_git_free_and_records_verified_source_input(tmp_path: Path) -> None:
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
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    release_root = tmp_path / "release"
    manifest = build_exported_release(source, requested_sha=sha, release_root=release_root)
    assert manifest.release_sha == sha
    assert len(manifest.source_digest) == 64
    assert not (release_root / ".git").exists()
    assert validate_release_manifest(release_root / "manifest.json").release_sha == sha


def test_export_accepts_an_already_validated_same_sha_release(tmp_path: Path) -> None:
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
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    release_root = tmp_path / sha
    first = build_exported_release(source, requested_sha=sha, release_root=release_root)
    second = build_exported_release(source, requested_sha=sha, release_root=release_root)
    assert second == first


def test_release_launcher_carries_manifest_sha_and_scrubs_unrelated_environment(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": SHA,
                "source_digest": digest_release_source(release),
                "artifact_digest": digest_release_artifact(release),
            }
        ),
        encoding="utf-8",
    )
    env = build_release_launch_env(release, ambient={"PATH": "/bin", "SECRET": "no"})
    assert env == {"PATH": "/bin", "PLAN_RELEASE_SHA": SHA, "PLAN_RELEASE_ROOT": str(release)}


def test_release_manifest_requires_exact_release_directory_and_artifact_digest(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    release = releases / SHA
    release.mkdir(parents=True)
    (release / "app.txt").write_text("release", encoding="utf-8")
    from planner.environments.release import digest_release_artifact

    (release / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": SHA,
                "source_digest": "b" * 64,
                "artifact_digest": digest_release_artifact(release),
            }
        ),
        encoding="utf-8",
    )
    assert (
        validate_release_manifest(
            release / "manifest.json", expected_sha=SHA, release_root=releases
        ).release_sha
        == SHA
    )

    with pytest.raises(ReleaseValidationError, match="release directory"):
        validate_release_manifest(release / "manifest.json", release_root=tmp_path)


def test_release_launcher_preserves_allowlisted_external_runtime_variables(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": SHA,
                "source_digest": "a" * 64,
                "artifact_digest": digest_release_artifact(release),
            }
        ),
        encoding="utf-8",
    )
    env = build_release_launch_env(
        release,
        ambient={"PLAN_DB_PATH": "/state/db", "PLAN_LOGS_DIR": "/logs", "SECRET": "no"},
    )
    assert env["PLAN_DB_PATH"] == "/state/db"
    assert env["PLAN_LOGS_DIR"] == "/logs"
    assert "SECRET" not in env


def test_config_keeps_development_identity_explicit() -> None:
    assert load_config(env={"PLAN_TEST_MODE": "1"}).release_sha is None
    assert load_config(env={"PLAN_TEST_MODE": "1", "PLAN_RELEASE_SHA": SHA}).release_sha == SHA
