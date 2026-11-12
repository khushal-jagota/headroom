"""Fresh Python byte oracle for normalized manual writes, without an HTTP host.

HTTP comparison replaces random measure IDs, which cannot prove literal digests.
This fixed-ID proof covers numeric normalization and the exact fingerprint bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from planner.measures.logic.manual import fingerprint, parse_write

ROOT = Path(__file__).resolve().parents[3]


def test_unit12_manual_fingerprint(request: pytest.FixtureRequest) -> None:
    raw_values = ["3.0", "3", "900719925474099312345", "-2.75"]
    raw_requests = [
        '{"request_id":"same-id-☃", "date":"2025-12-31",'
        '"expected_definition_revision":1,"basis":"date",'
        '"operation":"replace","expected_write_revision":0,"value":' + value + "}"
        for value in raw_values
    ]
    raw_requests.append(
        raw_requests[0]
        .replace(
            '"expected_definition_revision":1',
            '"expected_definition_revision":9007199254740993',
        )
        .replace('"expected_write_revision":0', '"expected_write_revision":9007199254740995')
    )
    expected = []
    for raw in raw_requests:
        body = parse_write(json.loads(raw))
        payload = json.dumps(
            {"measure_id": "measure_fixed", **body},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        expected.append(
            {
                "bytes": payload.hex(),
                "text": payload.decode(),
                "fingerprint": fingerprint("measure_fixed", body),
            }
        )
        assert hashlib.sha256(payload).hexdigest() == expected[-1]["fingerprint"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_")
        and key
        not in (
            "CODEX_HOME",
            "HERMES_HOME",
            "CLAUDE_CONFIG_DIR",
        )
    }
    completed = subprocess.run(
        ["node", str(ROOT / "server/measures/testing/fingerprint.ts")],
        input=json.dumps(raw_requests),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        check=True,
    )
    actual = json.loads(completed.stdout)
    report = {"raw_values": raw_values, "python": expected, "typescript": actual}
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-manual-fingerprint.json").write_text(json.dumps(report, indent=2))
    assert actual == expected
