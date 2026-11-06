"""Disposable hosted-runner setup around the real deployment workflow."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import sysconfig
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
    # The workflow restores this build from its cache, keyed by the baseline SHA.
    # On a miss, build it once here and the workflow saves it for later runs.
    cached = Path(os.environ["PANELS_BASELINE_APP_CACHE"])
    if not cached.exists():
        source = root / "baseline-source"
        subprocess.run(["git", "worktree", "add", "--detach", str(source), baseline], check=True)
        with (root / "gate/baseline-build.log").open("w") as log:
            subprocess.run([
                sys.executable, "-m", "planner", "environment", "app-build",
                "--source-root", str(source), "--requested-sha", baseline,
                "--candidate-app", str(cached),
            ], check=True, stdout=log, stderr=subprocess.STDOUT)
    shutil.copytree(cached, current / "app", symlinks=True)
    from planner.environments.app import validate_app_manifest

    # The copy must still be the exact baseline build: SHA and artifact digest.
    validate_app_manifest(current / "app/manifest.json", expected_sha=baseline,
                          require_runtime=True)
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
                    seed_synthetic_state(current, url)
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


def seed_synthetic_state(current: Path, health_url: str) -> None:
    """Create only disposable records and excluded files on the hosted runner."""
    require_rehearsal()
    url = health_url.removesuffix("/api/health") + "/api/projects"
    request = urllib.request.Request(url, data=json.dumps({
        "name": "Publication rehearsal", "summary": "Synthetic upgrade sentinel",
    }).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        record = json.load(response)
    gate = Path(os.environ["PANELS_GATE_DIR"])
    (gate / "synthetic-record-before.json").write_text(json.dumps(record) + "\n")
    capture_schema(current, gate / "schema-before.json")
    hashes = {}
    for relative in ("video-uploads/rehearsal-sentinel.txt",
                     "tickets/t_rehearsal/artifacts/sentinel.txt"):
        path = current / "data/files" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ("Disposable publication proof: " + relative).encode()
        path.write_bytes(content)
        hashes[relative] = hashlib.sha256(content).hexdigest()
    (gate / "excluded-files-before.json").write_text(json.dumps(hashes) + "\n")


def capture_schema(current: Path, output: Path) -> None:
    with sqlite3.connect(f"file:{current / 'data/planner.db'}?mode=ro", uri=True) as conn:
        revisions = [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]
    output.write_text(json.dumps({"revisions": sorted(revisions)}) + "\n")


def capture_synthetic_state(current: Path, health_url: str, gate: Path) -> None:
    capture_schema(current, gate / "schema-after.json")
    record = json.loads((gate / "synthetic-record-before.json").read_text())
    url = (health_url.removesuffix("/api/health") + "/api/projects?detail=full&id="
           + record["id"])
    with urllib.request.urlopen(url, timeout=5) as response:
        after = json.load(response)
    (gate / "synthetic-record-after.json").write_text(json.dumps(after) + "\n")
    if any(after.get(key) != record.get(key) for key in ("id", "name", "summary")):
        raise ValueError("synthetic record changed across the upgrade")
    before = json.loads((gate / "excluded-files-before.json").read_text())
    hashes = {relative: hashlib.sha256((current / "data/files" / relative).read_bytes()).hexdigest()
              for relative in before}
    (gate / "excluded-files-after.json").write_text(json.dumps(hashes) + "\n")
    if hashes != before:
        raise ValueError("excluded upload or ticket artifact changed across the upgrade")


def install() -> None:
    """Install the staged candidate onto the known-good release the way the server does."""
    require_rehearsal()
    from datetime import UTC, datetime, timedelta

    from planner.environments.update_attempts import (
        FileUpdateAttemptStore,
        attempt_path,
        parse_release,
        utc_now,
    )
    from planner.environments.update_configuration import (
        STAGED_APP_PREFIX,
        UPDATE_HEALTH_TIMEOUT_SECONDS,
        UPDATE_INSTALL_DEADLINE_SECONDS,
    )
    from planner.environments.update_contracts import (
        SUPPORTED_UPDATE_SCHEMA_FLOOR,
        UpdateAttempt,
    )
    from planner.environments.update_install import (
        installer_command,
        schema_change_pending,
        write_installer,
    )

    current = Path(os.environ["PANELS_CURRENT_ROOT"])
    gate = Path(os.environ["PANELS_GATE_DIR"])
    baseline, requested = os.environ["PANELS_BASELINE_SHA"], os.environ["PANELS_REQUESTED_SHA"]
    data, staged = current / "data", current / f"{STAGED_APP_PREFIX}{requested}"
    backup = None
    if schema_change_pending(data / "planner.db", staged):
        # The running known-good release backs itself up, while it still serves.
        backup = subprocess.run(
            [str(current / "app/bin/panels"), "environment", "backup-current",
             "--source-db", str(data / "planner.db"), "--backup-dir", str(data / "backups"),
             "--current-app", str(current / "app")],
            check=True, capture_output=True, text=True,
            env=os.environ | {"HOME": os.environ["PANELS_APP_HOME"]},
        ).stdout.strip()
    attempt_id = "rehearsal-" + os.environ["PANELS_DEPLOYMENT_ID"]
    now = utc_now()
    store = FileUpdateAttemptStore(attempt_path(data))
    store.create(UpdateAttempt(
        attempt_id=attempt_id, from_sha=baseline, to_sha=requested, mode="now",
        confirm_activity=False, state="installing", created_at=now, updated_at=now,
        # The artifact is uploaded after this proof, so it has no artifact ids yet.
        release=parse_release({
            "publication_run_id": os.environ["GITHUB_RUN_ID"], "artifact_id": 0,
            "proof_artifact_id": 0, "manifest": json.loads((staged / "manifest.json").read_text()),
            "archive_sha256": (gate / "archive-sha256.txt").read_text().strip(),
            "schema_floor": SUPPORTED_UPDATE_SCHEMA_FLOOR, "expires_at": now,
        }),
        deadline_at=(
            datetime.now(UTC) + timedelta(seconds=UPDATE_INSTALL_DEADLINE_SECONDS)
        ).isoformat(),
        backup=backup,
    ))
    command = installer_command(
        write_installer(data), current_root=current, attempt_id=attempt_id,
        service=os.environ["PANELS_SERVICE_NAME"], health_url=os.environ["PANELS_HEALTH_URL"],
        health_timeout_seconds=UPDATE_HEALTH_TIMEOUT_SECONDS,
    )
    # Its own transient unit, as the server starts it, so stopping the service spares it.
    subprocess.run(["systemd-run", "--user", "--wait", "--collect", "--pipe", "--quiet",
                    f"--unit=panels-update-{attempt_id}", *command], check=False)
    attempt = store.read()
    if attempt is None or attempt.attempt_id != attempt_id or attempt.state != "done":
        raise RuntimeError(
            "the installer did not finish the update: "
            + ("no attempt" if attempt is None else f"{attempt.state}: {attempt.detail}")
        )
    url = os.environ["PANELS_HEALTH_URL"] + "?expected_sha=" + requested
    with urllib.request.urlopen(url, timeout=5) as response:
        health = json.load(response)
    if health.get("app_sha") != requested or health.get("ready") is not True:
        raise RuntimeError("the installed candidate is not healthy at its exact SHA")


def check_selection() -> None:
    require_rehearsal()
    selection = json.loads((Path(os.environ["PANELS_GATE_DIR"]) / "selection.json").read_text())
    if "tests/integration/test_conversation_codex_bindings.py" not in selection["integration"]:
        raise ValueError("rehearsal must exercise Codex bindings")
    if not selection["e2e"]:
        raise ValueError("rehearsal must exercise the E2E report boundary")


def verify_proof(gate: Path, baseline: str, requested: str) -> None:
    """A green rehearsal must prove the installer's replacement and any backup it needed."""
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
    attempt = read("update-attempt.json")
    if (attempt.get("state") != "done" or attempt.get("from_sha") != baseline
            or attempt.get("to_sha") != requested):
        raise ValueError("rehearsal update attempt did not finish at the requested SHA")
    backup = attempt.get("backup")
    if backup is not None:
        metadata = read("backup-" + Path(str(backup)).name + ".json")
        if metadata.get("verified") is not True or metadata.get("deployed_revision") != baseline:
            raise ValueError("rehearsal did not retain a verified prior-release backup")
    (gate / "replacement-proof.json").write_text(json.dumps({
        "baseline_sha": baseline, "requested_sha": requested, "backup": backup,
        "installer_steps": attempt.get("steps"),
    }) + "\n")


def capture() -> None:
    require_rehearsal()
    if "PANELS_GATE_DIR" not in os.environ:
        return
    gate = Path(os.environ["PANELS_GATE_DIR"])
    current = Path(os.environ["PANELS_CURRENT_ROOT"])
    gate.mkdir(parents=True, exist_ok=True)
    for name, path in {
        "update-attempt.json": current / "data/update-attempt.json",
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
        url = (os.environ["PANELS_HEALTH_URL"]
               + "?expected_sha=" + os.environ["PANELS_REQUESTED_SHA"])
        with urllib.request.urlopen(url, timeout=5) as response:
            (gate / "final-health.json").write_bytes(response.read())
    except (OSError, ValueError) as exc:
        (gate / "final-health-error.txt").write_text(str(exc))
    if os.environ.get("PANELS_REHEARSAL_JOB_STATUS") == "success":
        capture_synthetic_state(current, os.environ["PANELS_HEALTH_URL"], gate)
        verify_proof(gate, os.environ["PANELS_BASELINE_SHA"], os.environ["PANELS_REQUESTED_SHA"])


def validate_cleanup(environment: dict[str, str]) -> None:
    """Keep this checkout, its Python and unretained evidence out of every deletion."""
    root = Path(environment["PANELS_TEMP_ROOT"]).resolve()
    checkout = Path(__file__).resolve().parents[1]
    protected = (
        checkout, Path(sys.executable).resolve(),
        Path(sysconfig.get_path("stdlib")).resolve(),
        Path(sysconfig.get_path("platstdlib")).resolve(),
    )
    targets = [root, Path(environment["PANELS_CANDIDATE_SOURCE"]).resolve()]
    if environment.get("PANELS_REHEARSAL") == "true":
        targets.append((root / "baseline-source").resolve())
        if environment.get("PANELS_REHEARSAL_HERMES_ROOT"):
            targets.append(Path(environment["PANELS_REHEARSAL_HERMES_ROOT"]).resolve())
    for target in targets:
        for execution_path in protected:
            if target.is_relative_to(execution_path) or execution_path.is_relative_to(target):
                raise ValueError(
                    f"Cleanup refused: {target} overlaps execution path "
                    f"{execution_path}. No service action or deletion occurred."
                )
    if environment.get("PANELS_REHEARSAL") != "true":
        return
    if environment.get("PANELS_PROOF_RETENTION_OUTCOME") != "success":
        raise ValueError("Cleanup refused: publication diagnostics are not retained.")


def cleanup() -> None:
    if "PANELS_TEMP_ROOT" not in os.environ:
        return
    validate_cleanup(dict(os.environ))
    root = Path(os.environ["PANELS_TEMP_ROOT"])
    if os.environ.get("PANELS_REHEARSAL") == "true":
        require_rehearsal()
        subprocess.run(["systemctl", "--user", "stop", os.environ["PANELS_SERVICE_NAME"]],
                       check=False)
        unit_path().unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["git", "worktree", "remove", "--force", str(root / "baseline-source")],
                       check=False)
        hermes_root = os.environ.get("PANELS_REHEARSAL_HERMES_ROOT")
        if hermes_root and Path(hermes_root).exists():
            shutil.rmtree(hermes_root)
    subprocess.run(["git", "worktree", "remove", "--force",
                    os.environ["PANELS_CANDIDATE_SOURCE"]], check=False)
    shutil.rmtree(root, ignore_errors=False)


if __name__ == "__main__":
    {"configure": configure, "start": start, "install": install,
     "check-selection": check_selection, "capture": capture, "cleanup": cleanup}[sys.argv[1]]()
