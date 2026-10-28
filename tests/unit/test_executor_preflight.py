"""Resident compatibility inspection preserves every path and byte it observes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.executor.configuration import SPOOL_FORMAT_VERSION
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
    check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert not root.exists()
    root.mkdir()
    before = tree(root)
    check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert tree(root) == before
    assert not (root / "spool").exists()
    (root / "spool").mkdir()
    before = tree(root)
    check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert tree(root) == before


def test_compatible_real_journal_inspection_takes_no_lock_or_write(tmp_path: Path) -> None:
    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"note": "unaccepted"}
    )
    before = tree(root)
    # The writer still owns its exclusive lock. Inspection cannot need that lock.
    check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert tree(root) == before
    spool.close()
    check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
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
        data["format"] = 1
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
        check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert tree(root) == before
    with pytest.raises(ValueError):
        FilesystemSpool(root / "spool")
    assert tree(root) == before


@pytest.mark.parametrize("expected", [True, False, 0, -1, 1, "2", None])
def test_bad_expected_format_never_creates_state(tmp_path: Path, expected: object) -> None:
    root = tmp_path / "absent"
    with pytest.raises(ValueError):
        check_spool_compatibility(root, expected)  # type: ignore[arg-type]
    assert not root.exists()


@pytest.mark.parametrize("operation", ["iterdir", "lstat"])
def test_inspection_oserror_propagates_and_preserves_existing_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    import os
    from collections.abc import Iterator

    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload={"note": "preserve these authored bytes"},
    )
    spool.close()
    before = tree(root)
    original_iterdir = Path.iterdir
    original_lstat = Path.lstat

    def denied_iterdir(path: Path) -> Iterator[Path]:
        if path == root / "spool":
            raise OSError(5, "directory inspection failed")
        return original_iterdir(path)

    def denied_lstat(path: Path) -> os.stat_result:
        if path == root / "spool":
            raise PermissionError(13, "state inspection denied")
        return original_lstat(path)

    with monkeypatch.context() as injected:
        if operation == "iterdir":
            injected.setattr(Path, "iterdir", denied_iterdir)
        else:
            injected.setattr(Path, "lstat", denied_lstat)
        # There is no successful None result when inspection cannot establish safety.
        with pytest.raises(OSError):
            check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert tree(root) == before


@pytest.mark.parametrize("target", ["root", "spool", "metadata", "entry"])
def test_permission_failure_is_never_absence_with_suppressing_path_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    import os

    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    row = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"bytes": "still here"}
    )
    spool.close()
    before = tree(root)
    denied = {
        "root": root,
        "spool": root / "spool",
        "metadata": root / "spool" / "metadata.json",
        "entry": root / "spool" / "entries" / row.submission_id,
    }[target]
    original_lstat = Path.lstat

    def inaccessible(path: Path) -> os.stat_result:
        if path == denied:
            raise PermissionError(13, "cannot inspect existing executor state")
        return original_lstat(path)

    with monkeypatch.context() as injected:
        # Model the newer pathlib behavior explicitly. Preflight must use direct
        # lstat and must not mistake these suppressed errors for an absent spool.
        for probe in ("exists", "is_dir", "is_file", "is_symlink"):
            injected.setattr(Path, probe, lambda self: False)
        injected.setattr(Path, "lstat", inaccessible)
        with pytest.raises(PermissionError):
            check_spool_compatibility(root, SPOOL_FORMAT_VERSION)
    assert tree(root) == before
