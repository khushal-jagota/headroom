"""One pure native codec proof, without a listener, provider, or Python value model.

Both executor peers use this codec. Existing connected proofs own transport and
error codes. This exchange protects root coverage, schema admission and framing.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from tests.support.synthetic_homes import create_synthetic_homes

ROOT = Path(__file__).resolve().parents[3]


def test_unit22_shared_protocol_exchange(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    result = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/protocol.ts")],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            **create_synthetic_homes(tmp_path),
            "PLAN_TEST_MODE": "1",
            "PLAN_SERVER_URL": "http://127.0.0.1:1",
        },
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    schema = json.loads((ROOT / "server/executor/wire.schema.json").read_text())
    assert set(evidence["roots"]) == set(schema["roots"])
    assert evidence["framed"] is evidence["events"] is True
    assert {
        "malformed-json",
        "missing-required-field",
        "missing-nullable-field",
        "unknown-enum",
        "no-scalar-coercion",
        "nonfinite-json-token",
        "nonfinite-json-exponent",
        "nonfinite-structured-extra",
        "command-kind-conflict",
        "command-needs-turn",
        "mailbox-payload-conflict",
        "mailbox-command-id-conflict",
        "body-budget",
        "output-budget",
    } <= set(evidence["refused"])
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination) / "unit-22-protocol.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({**evidence, "seconds": time.perf_counter() - started}, indent=2) + "\n"
        )
