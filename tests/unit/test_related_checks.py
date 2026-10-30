"""Admission: temporary histories prove release selection, previously untested.

Each case protects a distinct omission risk at the cheapest faithful layer (Git +
selector, no server/browser hosts). Existing deployment tests do not compute diffs.
"""
import json
import subprocess
from pathlib import Path

import pytest

from planner.environments.related_checks import select_release
from planner.environments.related_checks_contracts import RelatedSelection


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, str, Path]:
    def run(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tmp_path), *args]).decode().strip()

    run("init", "-q")
    run("config", "user.email", "tests@example.invalid")
    run("config", "user.name", "Selection test")
    for path in (
        "tests/unit/test_feedback.py", "tests/unit/test_authority_matrix.py",
        "tests/integration/test_adapter.py", "tests/e2e/test_feedback.py",
        "tests/containment/test_contained_pytest.py",
        "web/tests/feedback.test.ts", "web/tests/feedback.test.mjs",
        "src/planner/feedback/api.py", "docs/guide.md",
        "tests/support/conversation_codex_scripted_app_server.py",
    ):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("initial\n")
    run("add", ".")
    run("commit", "-qm", "initial")
    base = run("rev-parse", "HEAD")
    map_path = tmp_path.parent / "map.json"
    map_path.write_text(json.dumps({
        "documentation": ["docs/*"],
        "domains": [{"name": "feedback", "patterns": [
            "src/planner/feedback/*", "tests/unit/test_feedback.py",
        ], "tests": {"unit": ["tests/unit/test_feedback.py"],
                     "e2e": ["tests/e2e/test_feedback.py"]}}],
    }))
    return tmp_path, base, map_path


def release(history: tuple[Path, str, Path], edits: dict[str, str | None],
            baseline: str | None = "initial") -> RelatedSelection:
    root, base, mapping = history
    for path, content in edits.items():
        destination = root / path
        if content is None:
            destination.unlink()
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
    subprocess.check_call(["git", "-C", str(root), "add", "."])
    subprocess.check_call(["git", "-C", str(root), "commit", "--allow-empty", "-qm", "release"])
    requested = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"])
    return select_release(root, requested.decode().strip(), base,
                          base if baseline == "initial" else baseline, mapping)


def test_full_endpoint_union_includes_authority_and_rollback(
    history: tuple[Path, str, Path],
) -> None:
    root, base, mapping = history
    first = release(history, {"src/planner/feedback/api.py": "changed"})
    assert first["unit"] == ["tests/unit/test_authority_matrix.py", "tests/unit/test_feedback.py"]
    assert not first["broad"]
    second = release(history, {"docs/guide.md": "doc"})
    assert second["unit"] == first["unit"]  # not merely latest commit
    subprocess.check_call(["git", "-C", str(root), "checkout", "-q", base])
    reverse = select_release(root, base, base, second["requested_sha"], mapping)
    assert reverse["unit"] == first["unit"]




@pytest.mark.parametrize("path", [
    "src/planner/core/db.py", "unknown-input", "tests/unit/conftest.py",
    "src/planner/environments/related_checks.py",
])
def test_shared_or_unknown_inputs_cannot_underselect(
    history: tuple[Path, str, Path], path: str,
) -> None:
    selected = release(history, {path: "new"})
    assert selected["broad"] and selected["integration"] and selected["browser"]


def test_absent_baseline_and_missing_historical_mapping_broaden(
    history: tuple[Path, str, Path],
) -> None:
    selected = release(history, {}, baseline=None)
    assert selected["broad"]
    helper = "tests/support/conversation_codex_scripted_app_server.py"
    assert helper not in selected["unit"]
    assert helper in selected["ruff_paths"]
    # Opt-in probes must not become required publication tests or disappear from lint.
    probe = "tests/containment/test_contained_pytest.py"
    assert selected["integration"] == ["tests/integration/test_adapter.py"]
    assert probe not in selected["unit"] + selected["integration"] + selected["e2e"]
    assert probe in selected["ruff_paths"]
    assert selected["mypy"]
    selected = release(history, {"tests/unit/test_feedback.py": None,
                                 "src/planner/feedback/api.py": "changed"})
    assert selected["broad"]
    assert any("Missing mapped coverage" in reason for reason in selected["reasons"])






def test_requested_identity_and_missing_selected_file_refuse(
    history: tuple[Path, str, Path],
) -> None:
    root, base, mapping = history
    selected = release(history, {"src/planner/feedback/api.py": "new"})
    with pytest.raises(ValueError, match="HEAD"):
        select_release(root, base, base, base, mapping)
    (root / "tests/e2e/test_feedback.py").unlink()
    with pytest.raises(ValueError, match="absent"):
        select_release(root, selected["requested_sha"], base, base, mapping)
