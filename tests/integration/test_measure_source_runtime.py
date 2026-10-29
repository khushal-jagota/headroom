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
from planner.measure_sources.guidance import runtime_guide
from planner.measure_sources.runtime import Runtime


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in tuple(os.environ):
        if key.startswith(("PLAN_", "PANELS_REAL_")):
            monkeypatch.delenv(key)
    for key in ("CODEX_HOME", "HERMES_HOME", "CLAUDE_CONFIG_DIR", "PLAN_HERMES_HOME"):
        path = tmp_path / key.lower()
        path.mkdir()
        monkeypatch.setenv(key, str(path))


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
    assert resolver.calls[0].name == "project missing key"
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


def test_dataset_helper_exposes_captured_declarations_and_keeps_scalar_helpers() -> None:
    from planner.datasets.contracts import (
        DatasetDimension,
        DatasetField,
        DatasetFieldKind,
        OutputDataset,
    )
    from planner.measures.contracts import Observation, ScalarCoverageStatus

    output = OutputDataset(
        "d",
        3,
        "UTC",
        (DatasetDimension("region", "Region"),),
        (DatasetField("events", "Events", "events", DatasetFieldKind.count),),
        "2026-10-27",
    )
    signatures = {name: helper["signature"] for name, helper in runtime_guide()["helpers"].items()}
    script = (
        f"expected_signatures = {signatures!r}\n"
        + """
import ast, inspect
for name, documented in expected_signatures.items():
    actual = inspect.signature(globals()[name])
    actual = actual.replace(
        parameters=[
            p.replace(annotation=inspect.Parameter.empty) for p in actual.parameters.values()
        ],
        return_annotation=inspect.Signature.empty,
    )
    documented_definition = ast.parse('def ' + documented + ': pass')
    documented_definition.body[0].returns = None
    assert ast.dump(ast.parse('def ' + name + str(actual) + ': pass')) == ast.dump(
        documented_definition
    )
output = context['dataset_outputs'][0]
assert output['definition_revision'] == 3 and output['dimensions'][0]['name'] == 'region'
previous = latest('m')
assert previous['value'] == 0 and previous['query_start_at'] == 1
assert previous['query_end_at'] == 2 and previous['limitation'] == 'Clipped'
assert previous['coverage_status'] == 'partial'
record('m', '2026-10-27', 4)
record('m', '2026-10-28', 0, query_start_at=1, query_end_at=2, limitation='Clipped')
record('m', '2026-10-29', 5, query_start_at=2, query_end_at=3)
record('m', '2026-10-26', None, 'provider_has_no_value')
record_dataset(output['dataset_id'], '2026-10-20',
               [{'dimensions': {'region': 'de'}, 'values': {'events': 7}}],
               query_end_at=context['frozen_at'], query_start_at=context['frozen_at'] - 3600,
               labels={'region': {'de': 'Germany'}}, limitation='Provider sample')
"""
    )
    previous = Observation(
        "m",
        "2026-10-26",
        0,
        "previous-run",
        1,
        1,
        None,
        query_start_at=1,
        query_end_at=2,
        limitation="Clipped",
        coverage_status=ScalarCoverageStatus.partial,
        coverage_reasons=("Clipped",),
    )
    result = Runtime().execute(
        replace(admission(script, dataset_outputs=(output,)), previous=(previous,)),
        MemoryCredentials(),
    )
    assert result.failure is None and result.observations[0].value == 4
    assert result.observations[0].query_start_at is None
    assert (
        result.observations[1].query_start_at,
        result.observations[1].query_end_at,
        result.observations[1].limitation,
    ) == (1, 2, "Clipped")
    assert result.observations[2].value == 5 and result.observations[2].limitation is None
    assert result.observations[3].unavailable_reason == "provider_has_no_value"
    assert result.dataset_results[0].dataset_id == "d"
    assert result.dataset_results[0].date == "2026-10-20"
    assert result.dataset_results[0].rows[0].values == {"events": 7}
    assert result.dataset_results[0].labels == {"region": {"de": "Germany"}}
    assert result.dataset_results[0].limitation == "Provider sample"
    for statement in (
        "record_dataset('foreign', '2026-10-27', [], query_end_at=0)",
        "record_dataset('d', '2026-10-27', [], query_end_at=float('nan'))",
        "record('m', '2026-10-27', 0, query_start_at=True)",
        "record('m', '2026-10-27', 0, query_start_at=2, query_end_at=1)",
        "record('m', '2026-10-27', 0, limitation='')",
    ):
        failed = Runtime().execute(
            admission(statement, dataset_outputs=(output,)), MemoryCredentials()
        )
        assert failed.failure is not None and failed.failure.code is FailureCode.invalid_output
        assert failed.dataset_results == () and failed.observations == ()
