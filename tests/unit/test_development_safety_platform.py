"""Platform applicability must not turn missing Linux evidence into an admission."""

import sys
from pathlib import Path

import pytest
from development_safety import scanner
from development_safety.contracts import SafetyRefusal


def test_macos_reports_inapplicable_without_reading_linux_mounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def no_linux_probe(*args: object, **kwargs: object) -> str:
        raise AssertionError("macOS must not read Linux mount evidence")

    monkeypatch.setattr(Path, "read_text", no_linux_probe)
    with pytest.warns(RuntimeWarning, match="does not apply on macOS"):
        scanner.check_paths([tmp_path], environment={})


def test_linux_missing_mount_evidence_still_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def missing_mounts(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError("missing Linux mount evidence")

    monkeypatch.setattr(Path, "read_text", missing_mounts)
    with pytest.raises(SafetyRefusal, match="missing Linux mount evidence"):
        scanner.check_paths([tmp_path], environment={})
