"""Actual ordinary child execution, errors, and lifecycle cleanup."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from tests.support.measure_runtime import MemoryCredentials, admission

from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import FailureCode, RunResult, RuntimeBudget
from planner.measure_sources.runtime import Runtime


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)


def test_normal_python_files_processes_and_prints(tmp_path: Path) -> None:
    output = tmp_path / "source.txt"
    script = f"""
import os, subprocess, sys
import httpx
from pathlib import Path
from zoneinfo import ZoneInfo
assert ZoneInfo('Europe/Berlin')
Path({str(output)!r}).write_text('ordinary script')
assert subprocess.check_output([sys.executable, '-c', 'print(42)']).strip() == b'42'
print('a normal diagnostic')
print('x' * 300000, file=sys.stderr)
record('m', '2026-10-27', 42)
"""
    resolver = MemoryCredentials()
    result = Runtime().execute(admission(script), resolver)
    assert result.failure is None
    assert result.observations[0].value == 42
    assert output.read_text() == "ordinary script"
    assert result.metrics.child_output_bytes > 256 * 1024
    assert resolver.calls == []


def test_fault_preserves_fixed_type_and_line_without_message() -> None:
    result = Runtime().execute(
        admission("secret = 'DO-NOT-RECORD'\nvalue = {}[secret]\n"), MemoryCredentials()
    )
    assert result.failure is not None
    assert result.failure.code is FailureCode.runtime_failure
    assert result.failure.exception_type == "KeyError"
    assert result.failure.source_line == 2
    assert "DO-NOT-RECORD" not in repr(result)


def test_missing_credential_is_an_actual_use_failure() -> None:
    resolver = MemoryCredentials()
    result = Runtime().execute(
        admission("request('http://localhost', 'project missing key')"),
        resolver,
    )
    assert result.failure is not None
    assert result.failure.code is FailureCode.credential_unavailable
    assert resolver.calls[0][1].name == "project missing key"
    unused = Runtime().execute(admission("record('m', '2026-10-27', 1)"), resolver)
    assert unused.failure is None
    assert len(resolver.calls) == 1


def test_invalid_observation_remains_a_data_error() -> None:
    for statement in (
        "record('other', '2026-10-27', 1)",
        "record('m', 'not-a-date', 1)",
        "record('m', '2026-10-27', True)",
    ):
        result = Runtime().execute(admission(statement), MemoryCredentials())
        assert result.failure is not None
        assert result.failure.code is FailureCode.invalid_output


def test_timeout_cleans_up_owned_process(tmp_path: Path) -> None:
    marker = tmp_path / "pid"
    script = f"""
import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
open({str(marker)!r}, 'w').write(json.dumps([os.getpid(), child.pid]))
time.sleep(30)
"""
    result = Runtime().execute(
        admission(script, limits=replace(DEFAULT_LIMITS, wall_seconds=0.8)),
        MemoryCredentials(),
    )
    assert result.failure is not None
    assert result.failure.budget is RuntimeBudget.wall_seconds
    pid, descendant = json.loads(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    # The source's child is terminated too. Its orphaned exit status belongs to init.
    status = Path(f"/proc/{descendant}/stat")
    try:
        process_state = status.read_text().split()[2]
    except FileNotFoundError:
        pass  # Reaping can remove the process between an existence check and a read.
    else:
        assert process_state == "Z"
    assert result.metrics.elapsed_milliseconds < 3000


def test_cancel_before_and_during_execution(tmp_path: Path) -> None:
    runtime = Runtime()
    runtime.cancel("early")
    early = runtime.execute(admission("raise AssertionError", run_id="early"), MemoryCredentials())
    assert early.failure is not None and early.failure.code is FailureCode.interrupted
    marker = tmp_path / "pid"
    script = f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(30)"
    results: list[RunResult] = []
    thread = threading.Thread(
        target=lambda: results.append(
            runtime.execute(admission(script, run_id="live"), MemoryCredentials())
        )
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    runtime.cancel("live")
    thread.join(3)
    assert not thread.is_alive()
    assert results[0].failure is not None and results[0].failure.code is FailureCode.interrupted
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_timeout_can_exceed_old_product_ceiling() -> None:
    result = Runtime().execute(
        admission("record('m', '2026-10-27', 1)", limits=replace(DEFAULT_LIMITS, wall_seconds=120)),
        MemoryCredentials(),
    )
    assert result.failure is None
