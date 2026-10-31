"""Exercise the generated launcher and final exec environment without a server."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from planner.environments.app import _write_app_entrypoints, digest_app_artifact, digest_app_source
from planner.environments.executor_launch_contracts import VpsExecutorLaunchConfiguration
from planner.environments.vps_launch import write_vps_launch

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
        "src/planner/environments/executor_launch_contracts.py",
        "src/planner/environments/vps_launch.py",
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
        "keys=('PLAN_EXECUTOR_MODE','PLAN_EXECUTOR_VPS_SOCKET','PLAN_EXECUTOR_ID','PATH');"
        "print(json.dumps({'env':{key:os.environ[key] for key in keys if key in os.environ},"
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
        + shlex.join([sys.executable, "-I", "-B", "-c", record])
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
    environment = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONPATH": "/poison/source",
        "PLAN_EXECUTOR_MODE": "remote",
        "PLAN_EXECUTOR_VPS_SOCKET": "/poison/socket",
        "PLAN_EXECUTOR_ID": "poison-executor",
    }
    configuration = app.parent / "data/executor-launch.json"

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
    assert payload["env"]["PLAN_EXECUTOR_MODE"] == "local"
    assert "PLAN_EXECUTOR_VPS_SOCKET" not in payload["env"]
    assert payload["untrusted_pythonpath_present"] is False
    executor_id = "owner-mac"
    write_vps_launch(configuration, VpsExecutorLaunchConfiguration("remote", executor_id))
    remote = launch()
    assert remote.returncode == 0, remote.stderr
    remote_env = json.loads(remote.stdout)["env"]
    assert remote_env["PLAN_EXECUTOR_MODE"] == "remote"
    assert remote_env["PLAN_EXECUTOR_ID"] == executor_id
    assert "PLAN_EXECUTOR_VPS_SOCKET" not in remote_env
    # An untrusted configuration must refuse execution, not fall back to local.
    configuration.chmod(0o644)
    refused = launch()
    assert refused.returncode != 0 and not refused.stdout
    assert "owner-only" in refused.stderr
