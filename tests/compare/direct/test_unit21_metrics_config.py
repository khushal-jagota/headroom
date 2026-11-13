"""A lexical database alias must not select a different metrics sidecar.

Existing store proofs receive typed configuration. They do not prove Python's
environment number grammar or canonical sidecar placement. One Node invocation
checks both intake boundaries, with existing synthetic homes and no host probes.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.environments.vps_metrics.config import load_config, store_path

ROOT = Path(__file__).resolve().parents[3]


def test_unit21_metrics_configuration_and_sidecar_paths(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    target = tmp_path / "canonical"
    (target / "child").mkdir(parents=True)
    database = target / "database.sqlite"
    database.touch()
    alias = tmp_path / "database-alias.sqlite"
    alias.symlink_to(database)
    directory_alias = tmp_path / "directory-alias"
    directory_alias.symlink_to(target / "child", target_is_directory=True)
    paths = [
        str(database),
        str(alias),
        str(directory_alias) + "/../database.sqlite",
        str(target / "missing" / "database.sqlite"),
        "~/database.sqlite",
    ]
    environments = [
        {},
        {"PLAN_VPS_METRICS_IDLE_SECONDS": "1_0.5", "PLAN_VPS_METRICS_PROCESS_PAGE_ROWS": "2_50"},
        {
            "PLAN_VPS_METRICS_IDLE_SECONDS": "\u0085١٥.٥\u0085",
            "PLAN_VPS_METRICS_PROCESS_PAGE_ROWS": "２_５０",
        },
        {"PLAN_VPS_METRICS_IDLE_SECONDS": "+1_5e+0"},
        {"PLAN_VPS_METRICS_IDLE_SECONDS": "0x10"},
        {"PLAN_VPS_METRICS_PROCESS_PAGE_ROWS": "0x10"},
        {"PLAN_VPS_METRICS_PROCESS_PAGE_ROWS": "2.0"},
        {"PLAN_VPS_METRICS_IDLE_SECONDS": "1__5"},
        {"PLAN_VPS_METRICS_IDLE_SECONDS": "nan"},
        {"PLAN_VPS_METRICS_IDLE_SECONDS": "INF"},
    ]
    configurations: list[dict[str, Any]] = []
    for environment in environments:
        try:
            configurations.append({"value": asdict(load_config(environment))})
        except ValueError as error:
            configurations.append({"error": {"name": type(error).__name__, "message": str(error)}})
    with synthetic_home_environment(homes):
        expected: dict[str, Any] = {
            "configurations": configurations,
            "paths": [str(store_path(path)) for path in paths],
        }
    # Both aliases must select the actual database's sidecar, not an alias-named file.
    assert expected["paths"][0] == expected["paths"][1] == expected["paths"][2]
    specification = tmp_path / "input.json"
    unsafe = {"PLAN_VPS_METRICS_HISTORY_POINTS": "9007199254740993"}
    python_unsafe = load_config(unsafe).history_points
    assert python_unsafe == 9007199254740993
    specification.write_text(json.dumps({"environments": [*environments, unsafe], "paths": paths}))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(homes)
    configuration = tmp_path / "config.yaml"
    configuration.write_text("{}\n")
    environment.update(
        {
            "PLAN_DB_PATH": str(database),
            "PLAN_CONFIG_PATH": str(configuration),
            "PLAN_APP_ROOT": str(tmp_path),
            "PLAN_WORKSPACE_ROOT": str(tmp_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_BACKUP_DIR": str(tmp_path / "backups"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
            "PLAN_CREDENTIAL_KEY_PATH": str(tmp_path / "key"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
        }
    )
    output = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "const {metrics_config_parity}=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            "process.stdout.write(JSON.stringify(await metrics_config_parity(input)));",
            (ROOT / "server/environments/vps_metrics/testing/config_parity.ts").as_uri(),
            str(specification),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert output.returncode == 0, output.stderr
    actual = json.loads(output.stdout)
    proposed_refusal = actual["configurations"].pop()
    assert actual["paths"] == expected["paths"]
    for candidate, source in zip(actual["configurations"], expected["configurations"], strict=True):
        assert set(candidate) == set(source)
        if "error" in source:
            assert set(candidate["error"]) == set(source["error"]) == {"name", "message"}
            assert isinstance(candidate["error"]["name"], str)
            assert isinstance(candidate["error"]["message"], str) and candidate["error"]["message"]
        else:
            assert candidate == source
    # The Manager accepted this TS admission boundary. Python's different answer
    # remains informational; this refusal assertion is a gating result.
    assert proposed_refusal == {
        "error": {
            "name": "MetricsValueError",
            "message": "history_points exceeds the supported exact integer range",
        }
    }
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-21-metrics-config.json").write_text(
            json.dumps(
                {
                    "kind": "direct",
                    "equal": True,
                    "configurations": len(environments),
                    "canonical_paths": len(paths),
                    "proposed_difference": {
                        "accepted": True,
                        "python_accepts_integer": str(python_unsafe),
                        "typescript": proposed_refusal,
                    },
                    "seconds": time.perf_counter() - started,
                },
                indent=2,
            )
            + "\n"
        )
