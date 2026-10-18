"""Disposable hosted-runner setup around the real deployment workflow."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def deployment_paths(environment: dict[str, str]) -> dict[str, str]:
    """Production keeps its fixed contract; rehearsal never shares its host or state."""
    run = environment["GITHUB_RUN_ID"] + "-" + environment["GITHUB_RUN_ATTEMPT"]
    if not re.fullmatch(r"\d+-\d+", run):
        raise ValueError("invalid workflow run identity")
    rehearsal = environment.get("PANELS_REHEARSAL") == "true"
    if rehearsal:
        if environment.get("RUNNER_ENVIRONMENT") != "github-hosted":
            raise ValueError("rehearsal requires a disposable GitHub-hosted runner")
        baseline = environment.get("PANELS_BASELINE_SHA", "")
        if not re.fullmatch(r"[0-9a-f]{40}", baseline):
            raise ValueError("rehearsal requires an explicit full known-good baseline SHA")
        if baseline == environment["PANELS_REQUESTED_SHA"]:
            raise ValueError("rehearsal baseline must differ from requested SHA")
        temporary = Path("/home/vps/Deployments/Panels") / f"rehearsal-{run}"
        app_home = temporary / "home"
        current = temporary / "current"
    else:
        temporary = Path(environment["RUNNER_TEMP"]) / f"panels-deploy-{run}"
        app_home = Path(environment["HOME"])
        current = app_home / "Deployments/Panels/current"
    return {
        "PANELS_TEMP_ROOT": str(temporary),
        "PANELS_CANDIDATE_SOURCE": str(temporary / "source"),
        "PANELS_CANDIDATE_APP": str(temporary / "candidate"),
        "PANELS_DEPLOY_VENV": str(temporary / "venv"),
        "PANELS_CODEX_TOOLS": str(temporary / "codex-tools"),
        "PANELS_GATE_DIR": str(temporary / "gate"),
        "PANELS_REPORT_DIR": str(temporary / "gate/reports"),
        "PANELS_DEPLOYMENT_ID": run,
        "PANELS_CURRENT_ROOT": str(current),
        "PANELS_APP_HOME": str(app_home),
        "PANELS_LIFECYCLE_PATH": str(current / "data/deployment-lifecycle.json"),
        "PANELS_SERVICE_NAME": f"panels-rehearsal-{run}.service" if rehearsal
        else "panels-live.service",
        "PANELS_HEALTH_URL": "http://127.0.0.1:8767/api/health",
    }


def append_environment(values: dict[str, str]) -> None:
    with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("workflow paths must be single-line")
            stream.write(f"{key}={value}\n")


def configure() -> None:
    paths = deployment_paths(dict(os.environ))
    Path(paths["PANELS_TEMP_ROOT"]).mkdir(mode=0o700)
    Path(paths["PANELS_GATE_DIR"]).mkdir()
    Path(paths["PANELS_CODEX_TOOLS"]).mkdir()
    append_environment(paths)


def require_rehearsal() -> None:
    if (os.environ.get("PANELS_REHEARSAL") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("this operation requires a disposable hosted rehearsal")


def unit_path() -> Path:
    return Path(os.environ["XDG_RUNTIME_DIR"]) / "systemd/user" / os.environ["PANELS_SERVICE_NAME"]


def start() -> None:
    require_rehearsal()
    root = Path(os.environ["PANELS_TEMP_ROOT"])
    current = Path(os.environ["PANELS_CURRENT_ROOT"])
    app_home = Path(os.environ["PANELS_APP_HOME"])
    baseline = os.environ["PANELS_BASELINE_SHA"]
    source = root / "baseline-source"
    subprocess.run(["git", "worktree", "add", "--detach", str(source), baseline], check=True)
    with (root / "gate/baseline-build.log").open("w") as log:
        subprocess.run([
            sys.executable, "-m", "planner", "environment", "app-build",
            "--source-root", str(source), "--requested-sha", baseline,
            "--candidate-app", str(current / "app"),
        ], check=True, stdout=log, stderr=subprocess.STDOUT)
    for directory in (app_home, current / "data", current / "logs", current / "run"):
        directory.mkdir(parents=True, exist_ok=True)
    config = current / "data/config.yaml"
    config.write_text("{}\n")
    # No inherited provider, service, database, or agent-home inputs enter this service.
    service_environment = {
        "HOME": str(app_home), "PATH": os.environ["PATH"], "LANG": "C.UTF-8",
        "PLAN_CONFIG_PATH": str(config), "PLAN_DISPATCH_ENABLED": "0",
        "PLAN_DB_PATH": str(current / "data/planner.db"),
        "PLAN_LOGS_DIR": str(current / "logs"),
        "PLAN_BACKUP_DIR": str(current / "data/backups"),
        "PLAN_HERMES_HOME": str(app_home / ".hermes"),
        "PLAN_DISPATCHER_LOCK_PATH": str(current / "run/dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(current / "run/control.sock"),
    }
    service = os.environ["PANELS_SERVICE_NAME"]
    unit = unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        service_environment["PLAN_PORT"] = str(port)
        unit.write_text(
            "[Unit]\nDescription=Disposable Panels deployment rehearsal\n[Service]\n"
            "Type=simple\n"
            f"WorkingDirectory={current}\n"
            f"ExecStart={current}/app/bin/panels-launcher serve\n"
            "KillMode=control-group\nTimeoutStopSec=30\n"
            + "".join(f"Environment={json.dumps(key + '=' + value)}\n"
                      for key, value in service_environment.items())
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "restart", service], check=True)
        url = f"http://127.0.0.1:{port}/api/health"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    url + "?expected_sha=" + baseline, timeout=2
                ) as response:
                    body = json.load(response)
                if body.get("app_sha") == baseline and body.get("ready") is True:
                    (root / "gate/baseline-health.json").write_text(json.dumps(body) + "\n")
                    append_environment({"PANELS_HEALTH_URL": url})
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        subprocess.run(["systemctl", "--user", "stop", service], check=True)
        journal = subprocess.run(
            ["journalctl", "--user", "-u", service, "--no-pager"],
            capture_output=True, text=True, check=False,
        ).stdout
        if "address already in use" not in journal.lower() or attempt == 2:
            raise RuntimeError("baseline did not become healthy:\n" + journal[-6000:])


def check_selection() -> None:
    require_rehearsal()
    selection = json.loads((Path(os.environ["PANELS_GATE_DIR"]) / "selection.json").read_text())
    if "tests/unit/test_conversation_codex_bindings.py" not in selection["unit"]:
        raise ValueError("rehearsal must exercise Codex bindings")
    if not selection["e2e"]:
        raise ValueError("rehearsal must exercise the E2E report boundary")


def verify_proof(gate: Path, baseline: str, requested: str) -> None:
    """A green rehearsal must prove the real replacement and backup path."""
    def read(name: str) -> dict[str, object]:
        value: dict[str, object] = json.loads((gate / name).read_text())
        return value

    baseline_health = read("baseline-health.json")
    health = read("final-health.json")
    if baseline_health.get("app_sha") != baseline or baseline == requested:
        raise ValueError("rehearsal did not serve the distinct known-good baseline")
    if health.get("app_sha") != requested or health.get("ready") is not True:
        raise ValueError("rehearsal did not finish healthy at the requested SHA")
    if read("deployed-manifest.json").get("app_sha") != requested:
        raise ValueError("rehearsal installed manifest differs from requested SHA")
    if read("lifecycle.json").get("phase") != "succeeded":
        raise ValueError("rehearsal lifecycle did not succeed")
    if not any(
        (metadata := read(path.name)).get("verified") is True
        and metadata.get("deployed_revision") == baseline
        for path in gate.glob("backup-*.json")
    ):
        raise ValueError("rehearsal did not retain a verified prior-release backup")
    (gate / "replacement-proof.json").write_text(json.dumps({
        "baseline_sha": baseline, "requested_sha": requested,
        "real_backup_and_replacement": True,
    }) + "\n")


def capture() -> None:
    require_rehearsal()
    if "PANELS_GATE_DIR" not in os.environ:
        return
    gate = Path(os.environ["PANELS_GATE_DIR"])
    current = Path(os.environ["PANELS_CURRENT_ROOT"])
    gate.mkdir(parents=True, exist_ok=True)
    for name, path in {
        "lifecycle.json": Path(os.environ["PANELS_LIFECYCLE_PATH"]),
        "deployed-manifest.json": current / "app/manifest.json",
        "service-unit.txt": unit_path(),
    }.items():
        if path.is_file():
            shutil.copy2(path, gate / name)
    for path in (current / "data/backups").glob("*/metadata.json"):
        shutil.copy2(path, gate / ("backup-" + path.parent.name + ".json"))
    if (current / "logs").exists():
        shutil.copytree(current / "logs", gate / "service-logs", dirs_exist_ok=True)
    with (gate / "service-journal.log").open("w") as log:
        subprocess.run(["journalctl", "--user", "-u", os.environ["PANELS_SERVICE_NAME"],
                        "--no-pager"], stdout=log, stderr=subprocess.STDOUT, check=False)
    try:
        with urllib.request.urlopen(os.environ["PANELS_HEALTH_URL"], timeout=5) as response:
            (gate / "final-health.json").write_bytes(response.read())
    except (OSError, ValueError) as exc:
        (gate / "final-health-error.txt").write_text(str(exc))
    if os.environ.get("PANELS_REHEARSAL_JOB_STATUS") == "success":
        verify_proof(gate, os.environ["PANELS_BASELINE_SHA"], os.environ["PANELS_REQUESTED_SHA"])


def cleanup() -> None:
    if "PANELS_TEMP_ROOT" not in os.environ:
        return
    root = Path(os.environ["PANELS_TEMP_ROOT"])
    if os.environ.get("PANELS_REHEARSAL") == "true":
        require_rehearsal()
        subprocess.run(["systemctl", "--user", "stop", os.environ["PANELS_SERVICE_NAME"]],
                       check=False)
        unit_path().unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["git", "worktree", "remove", "--force", str(root / "baseline-source")],
                       check=False)
    subprocess.run(["git", "worktree", "remove", "--force",
                    os.environ["PANELS_CANDIDATE_SOURCE"]], check=False)
    shutil.rmtree(root, ignore_errors=False)


if __name__ == "__main__":
    {"configure": configure, "start": start, "check-selection": check_selection,
     "capture": capture, "cleanup": cleanup}[sys.argv[1]]()
