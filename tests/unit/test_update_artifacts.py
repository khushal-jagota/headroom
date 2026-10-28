"""The retained archive must survive upload/download without losing executability."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from scripts.update_publication import full_selection
from tests.unit.test_deployment import _app, _rewrite_artifact_digest

from planner.environments import deployment_gate as gate
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


def test_portable_publication_gate_never_reuses_old_live_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(tmp_path / "app", "a" * 40, "candidate")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "requested_sha": "a" * 40, "artifact_digest": digest_app_artifact(app),
        "publication_run_id": 123, "archive_sha256": "b" * 64,
    }))
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("portable publication must not observe the old publication-time base")
    monkeypatch.setattr(gate, "observe_served", unexpected)
    gate.verify_gate_evidence(gate.read_gate_evidence(evidence), tmp_path / "current", app)
    (app / "candidate").write_text("changed after publication")
    with pytest.raises(ValueError, match="digest"):
        gate.verify_gate_evidence(gate.read_gate_evidence(evidence), tmp_path / "current", app)


def test_publication_selects_complete_suite_and_browser_catalog(tmp_path: Path) -> None:
    catalog = {"schema": 1, "entries": [{"path": "web/tests/a-browser.test.mjs"},
                                         {"path": "web/tests/b-browser.test.mjs"}]}
    path = tmp_path / "web/tests/browser/catalog.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog))
    for layer in ("unit", "integration", "e2e"):
        for name in ("test_top.py", "nested/test_nested.py"):
            path = tmp_path / "tests" / layer / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
    selection = full_selection(tmp_path)
    assert selection["ruff_paths"] == ["."]
    for layer in ("unit", "integration", "e2e"):
        assert selection[layer] == [f"tests/{layer}/nested/test_nested.py",
                                     f"tests/{layer}/test_top.py"]
    assert selection["browser"] == [entry["path"] for entry in catalog["entries"]]
