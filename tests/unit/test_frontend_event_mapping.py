from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from planner.core.contracts import EventKind

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_event_mapping_covers_backend_event_kinds() -> None:
    env = os.environ.copy()
    env["PLANNER_EVENT_KINDS"] = json.dumps([kind.value for kind in EventKind])
    result = subprocess.run(
        ["node", "web/tests/resource-catalogue.test.mjs"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
