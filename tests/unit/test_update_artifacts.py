"""The retained archive must survive upload/download without losing executability."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from tests.support.update_apps import build_app as _app
from tests.support.update_apps import rewrite_artifact_digest as _rewrite_artifact_digest

from planner.environments.app import digest_app_artifact
from planner.environments.update_artifacts import (
    create_app_archive,
    extract_app_archive,
    hash_archive,
)


def test_archive_preserves_modes_links_and_independent_proof_trees(tmp_path: Path) -> None:
    app = _app(tmp_path / "built", "a" * 40, "sentinel")
    (app / "bin/alias").symlink_to("panels")
    (app / "sentinel").chmod(0o640)
    _rewrite_artifact_digest(app)
    archive = tmp_path / "panels-app.tar.gz"
    digest = create_app_archive(app, archive)
    for name in ("smoke", "rehearsal", "adoption"):
        output = tmp_path / name
        manifest = extract_app_archive(archive, output, digest, "a" * 40)
        assert manifest.artifact_digest == digest_app_artifact(app)
        assert (output / "sentinel").stat().st_mode & 0o777 == 0o640
        assert (output / "bin/alias").is_symlink()
        assert (output / "bin/panels").stat().st_mode & 0o111 == 0o111
        (output / "sentinel").write_text("proof tree changed")
    assert hash_archive(archive) == digest
    with pytest.raises(FileExistsError):
        create_app_archive(app, archive)
    with pytest.raises(ValueError, match="hash differs"):
        extract_app_archive(archive, tmp_path / "bad-hash", "0" * 64, "a" * 40)
    assert not (tmp_path / "bad-hash").exists()


def test_archive_refuses_escape_before_extracting_member(tmp_path: Path) -> None:
    archive = tmp_path / "escape.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("../outside")
        member.size = 4
        bundle.addfile(member, io.BytesIO(b"oops"))
    with pytest.raises(ValueError, match="unsafe"):
        extract_app_archive(archive, tmp_path / "output", hash_archive(archive), "a" * 40)
    assert not (tmp_path / "outside").exists()


