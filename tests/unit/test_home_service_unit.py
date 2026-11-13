"""Check the generated user unit with systemd's real directive parser."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from planner.installation.contracts import HomeInstall
from planner.installation.home import linux_unit


def test_generated_home_unit_passes_systemd_verify(
    tmp_path: Path,
) -> None:
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is unavailable on this platform")
    root = tmp_path / "home with spaces and %n"
    executable = root / "app/bin/panels-launcher"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    options = HomeInstall(root, "panels-ticket-unit-proof", 18767, Path("/usr/bin/python3"), True)
    unit = tmp_path / "panels-ticket-unit-proof.service"
    unit.write_text(linux_unit(options))
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    started = time.perf_counter()
    result = subprocess.run(
        [analyzer, "--user", "verify", str(unit)],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)},
    )
    print(f"systemd-analyze verify seconds={time.perf_counter() - started:.6f}")
    assert result.returncode == 0, result.stderr
