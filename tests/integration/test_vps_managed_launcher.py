"""Exercise the generated launcher and final exec environment without a server."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from planner.environments.app import _write_app_entrypoints, digest_app_artifact, digest_app_source

SHA = "a" * 40


def test_actual_managed_launcher_applies_only_explicit_owner_configuration(tmp_path: Path) -> None:
    checkout = Path(__file__).resolve().parents[2]
    app = tmp_path / "current/app"
    app.mkdir(parents=True)
    # This launcher needs only its environment modules, independent of Mac tooling.
    source_files = {
        "src/planner/__init__.py",
        "src/planner/environments/__init__.py",
        "src/planner/environments/app.py",
        "src/planner/environments/app_launcher.py",
        "src/planner/environments/runtime_contracts.py",
        "src/planner/core/__init__.py",
        "src/planner/core/config.py",
        "src/planner/core/contracts.py",
        "src/planner/core/errors.py",
    }
    for name in source_files:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((checkout / name).read_bytes())
    _write_app_entrypoints(app, app_sha=SHA)
    python = app / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    bootstrap = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from planner.environments.app_launcher import main;sys.argv=sys.argv[2:];main()"
    )
    record = (
        "import json,os,sys;"
        "from planner.core.config import load_config;"
        "config=load_config(os.environ['PLAN_CONFIG_PATH']);"
        "keys=('PLAN_EXECUTOR_MODE','PLAN_EXECUTOR_VPS_SOCKET','PLAN_EXECUTOR_ID','PATH');"
        "print(json.dumps({'env':{key:os.environ[key] for key in keys if key in os.environ},"
        "'executor_id':config.executor_id,"
        "'untrusted_pythonpath_present':'PYTHONPATH' in os.environ,'argv':sys.argv[1:]}))"
    )
    python.write_text(
        "#!/bin/sh\nset -eu\n"
        'if [ "$1" = "-c" ]; then\n'
        + "  printf '%s\\n' "
        + shlex.quote(str(app / "src/planner/__init__.py"))
        + "\n  exit 0\nfi\n"
        + 'if [ "$1" = "-m" ] && [ "$2" = "planner.environments.app_launcher" ]; then\n'
        + "  exec "
        + shlex.join([sys.executable, "-I", "-B", "-c", bootstrap, str(app / "src")])
        + ' "$@"\nfi\n'
        + "exec "
        + shlex.join(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import sys;sys.path.insert(0,sys.argv.pop(1));" + record,
                str(app / "src"),
            ]
        )
        + ' "$@"\n'
    )
    python.chmod(0o755)
    (app / "web/dist").mkdir(parents=True)
    (app / "web/dist/index.html").write_text("instrument")
    (app / "agent_backends/node_modules").mkdir(parents=True)
    (app / "agent_backends/node_modules/.package-lock.json").write_text("{}")
    (app / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-app-v1",
                "app_sha": SHA,
                "source_digest": digest_app_source(app),
                "artifact_digest": digest_app_artifact(app),
            }
        )
    )
    configuration = app.parent / "data/config.yaml"
    configuration.parent.mkdir()
    configuration.write_text("{}\n")
    environment = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONPATH": "/poison/source",
        "PLAN_CONFIG_PATH": str(configuration),
        "PLAN_EXECUTOR_MODE": "remote",
        "PLAN_EXECUTOR_VPS_SOCKET": "/poison/socket",
        "PLAN_EXECUTOR_ID": "poison-executor",
    }

    def launch() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(app / "bin/panels-launcher"), "serve"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    local = launch()
    assert local.returncode == 0, local.stderr
    payload = json.loads(local.stdout)
    assert payload["argv"] == ["-m", "planner", "serve"]
    assert payload["executor_id"] is None
    assert "PLAN_EXECUTOR_MODE" not in payload["env"]
    assert "PLAN_EXECUTOR_ID" not in payload["env"]
    assert "PLAN_EXECUTOR_VPS_SOCKET" not in payload["env"]
    assert payload["untrusted_pythonpath_present"] is False
    executor_id = "owner-mac"
    configuration.write_text(f"executor_id: {executor_id}\n")
    remote = launch()
    assert remote.returncode == 0, remote.stderr
    remote_payload = json.loads(remote.stdout)
    assert remote_payload["executor_id"] == executor_id
    remote_env = remote_payload["env"]
    assert "PLAN_EXECUTOR_MODE" not in remote_env
    assert "PLAN_EXECUTOR_ID" not in remote_env
    assert "PLAN_EXECUTOR_VPS_SOCKET" not in remote_env
    # A malformed configured ID must refuse execution, not fall back to local.
    configuration.write_text("executor_id: invalid/executor\n")
    refused = launch()
    assert refused.returncode != 0 and not refused.stdout
    assert "executor_id must name a logical executor" in refused.stderr
