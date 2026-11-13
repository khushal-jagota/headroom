"""Small direct proofs for merged-backend faults, against fresh Python answers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tests.support.synthetic_homes import create_synthetic_homes

ROOT = Path(__file__).resolve().parents[3]
DRIVER = ROOT / "server/testing/cross_area_faults.ts"


def _node(tmp_path: Path, mode: str, argument: str = "", payload: Any = None) -> Any:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_")
        and key
        not in {
            "HOME",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "HERMES_HOME",
            "VITEST",
            "PYTEST_CURRENT_TEST",
        }
    }
    env.update(create_synthetic_homes(tmp_path))
    env.update(
        {
            "PLAN_DB_PATH": str(tmp_path / "database.sqlite"),
            "TMPDIR": str(tmp_path),
            "PLAN_CREDENTIAL_KEY_PATH": str(tmp_path / "key"),
            "PLAN_SERVER_URL": "http://127.0.0.1:1",
            "PLAN_CONFIG_PATH": str(tmp_path / "missing.yaml"),
            "PLAN_DISPATCH_ENABLED": "false",
        }
    )
    completed = subprocess.run(
        ["node", str(DRIVER), mode, argument],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_cross_area_actual_serializer_payloads_match_python(tmp_path: Path) -> None:
    # Real definitions and inventory projections prove the reachable payload shape.
    examples = _node(tmp_path, "serializers", str(tmp_path))

    def no_floats(value: Any) -> bool:
        if isinstance(value, float):
            return False
        if isinstance(value, dict):
            return all(no_floats(item) for item in value.values())
        if isinstance(value, list):
            return all(map(no_floats, value))
        return True

    for example in examples:
        assert no_floats(example["value"])
        assert example["encoded"] == json.dumps(
            example["value"], ensure_ascii=False, sort_keys=example["sorted"]
        )


def test_cross_area_all_truthy_spellings_close_io_doors(tmp_path: Path) -> None:
    # Existing safety proofs only set '1'; this covers the accepted alias boundary.
    from planner.core.config import _parse_bool

    for token in ("true", "yes", "on", "1"):
        assert _parse_bool(token, "test_mode") is True
        assert _node(tmp_path, "guards", token) is True
        # Only the startup function is replaced. The actual host guard runs.
        assert _node(tmp_path, "host", token) is True
