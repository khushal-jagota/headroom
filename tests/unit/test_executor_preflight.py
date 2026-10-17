"""Resident compatibility inspection preserves every path and byte it observes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.executor.contracts import EnvelopeKind
from planner.executor.journal import FilesystemSpool
from planner.executor.preflight import check_spool_compatibility


def tree(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }


def test_absent_and_empty_root_stay_absent_and_empty(tmp_path: Path) -> None:
    root = tmp_path / "executor"
    check_spool_compatibility(root, 1)
    assert not root.exists()
    root.mkdir()
    before = tree(root)
    check_spool_compatibility(root, 1)
    assert tree(root) == before
    assert not (root / "spool").exists()
    (root / "spool").mkdir()
    before = tree(root)
    check_spool_compatibility(root, 1)
    assert tree(root) == before


def test_compatible_real_journal_inspection_takes_no_lock_or_write(tmp_path: Path) -> None:
    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"note": "unaccepted"}
    )
    before = tree(root)
    # The writer still owns its exclusive lock. Inspection cannot need that lock.
    check_spool_compatibility(root, 1)
    assert tree(root) == before
    spool.close()
    check_spool_compatibility(root, 1)
    assert tree(root) == before


@pytest.mark.parametrize(
    "condition",
    [
        "incompatible",
        "corrupt",
        "unknown",
        "missing_version",
        "missing_metadata",
        "unknown_metadata",
        "unknown_entry",
    ],
)
def test_refused_existing_state_is_unchanged_by_preflight_and_constructor(
    tmp_path: Path,
    condition: str,
) -> None:
    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    envelope = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    spool.close()
    metadata = root / "spool" / "metadata.json"
    data = json.loads(metadata.read_bytes())
    if condition == "incompatible":
        data["format"] = 2
        metadata.write_text(json.dumps(data))
    elif condition == "corrupt":
        metadata.write_text("{broken")
    elif condition == "unknown":
        (root / "spool" / "foreign-state").write_bytes(b"unknown bytes")
    elif condition == "missing_version":
        del data["format"]
        metadata.write_text(json.dumps(data))
    elif condition == "missing_metadata":
        metadata.unlink()
    elif condition == "unknown_metadata":
        data["other_format_field"] = "unsupported"
        metadata.write_text(json.dumps(data))
    else:
        (root / "spool" / "entries" / envelope.submission_id / "unknown").write_text("unread")
    before = tree(root)
    with pytest.raises(ValueError):
        check_spool_compatibility(root, 1)
    assert tree(root) == before
    with pytest.raises(ValueError):
        FilesystemSpool(root / "spool")
    assert tree(root) == before


@pytest.mark.parametrize("expected", [True, False, 0, -1, 2, "1", None])
def test_bad_expected_format_never_creates_state(tmp_path: Path, expected: object) -> None:
    root = tmp_path / "absent"
    with pytest.raises(ValueError):
        check_spool_compatibility(root, expected)  # type: ignore[arg-type]
    assert not root.exists()
