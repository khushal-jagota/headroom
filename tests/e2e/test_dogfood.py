"""E2E item 35 — dogfood Level A (SPEC §18.3 item 35, §18.5): seed --demo, then run
scripts/dogfood_cli.py as a subprocess against the live test server."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"      # same pinning style as conftest.PLAN_BIN
SCRIPT = REPO_ROOT / "scripts" / "dogfood_cli.py"


def test_e35_dogfood_level_a(server, cli):
    cli(server, "seed", "--demo")                    # harness seeds; the script never does
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}
    env["PLAN_SERVER_URL"] = server.base
    env["PLAN_DB_PATH"] = str(server.db_path)
    proc = subprocess.run(
        [str(PYTHON), str(SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=300,
    )
    assert proc.returncode == 0, (
        f"dogfood_cli.py rc={proc.returncode}\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}"
    )
    assert "DOGFOOD LEVEL A: 13/13 PASS" in proc.stdout, proc.stdout
