"""Fresh Python and the owned file worker prove runtime records on populated data."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.mac_executor_fixture import ROOT, report_direct, run_node
from tests.support.synthetic_homes import create_synthetic_homes

from planner.executor.runtime_bootstrap import atomic_record, read_record


def test_unit22_runtime_records(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Gap: generic file-copy proofs do not cover failure-atomic runtime JSON records.

    Reuse FilesService. One native-injection worker proves staged-write and close retry boundaries.
    """
    began = time.perf_counter()
    record: dict[str, Any] = {
        "request_id": "fixture",
        "detail": "résumé 🦉\r\n",
        "integer": 9_007_199_254_740_993,
        "floating": 1.0,
        "nullable": None,
    }
    py = tmp_path / "py"
    path = py / "updates" / "selection.json"
    expected: dict[str, Any] = {"missing": read_record(path)}
    atomic_record(path, record)
    expected.update(
        bytes=path.read_text(), value=read_record(path), mode=path.stat().st_mode & 0o777
    )
    path.write_text("null")
    with pytest.raises(ValueError) as failure:
        read_record(path)
    expected["nonobject"] = {"error": type(failure.value).__name__, "message": str(failure.value)}
    path.unlink()
    expected["removed"] = read_record(path)
    target = tmp_path / "ts"
    target.mkdir()
    (target / "input.json").write_text(json.dumps({"record": record}))
    actual = run_node(
        str(ROOT / "server/executor/mac/runtime/testing/records_parity.ts"),
        target,
        create_synthetic_homes(tmp_path),
        timeout=20,
    )
    assert {key: actual[key] for key in expected} == expected
    assert actual["native"] == dict.fromkeys(
        (
            "failed",
            "preserved",
            "cleanup_preserved",
            "swapped",
            "contained",
            "close_failed",
            "retained",
            "cleared",
        ),
        True,
    )
    report_direct(
        request,
        "unit22-runtime-records.json",
        actual,
        began,
        "atomic runtime JSON via retained file worker",
        added_worker="native write/ancestor swap/close injections",
        real_mac="not checked",
    )
