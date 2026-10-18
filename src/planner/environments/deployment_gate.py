"""Observe a release base and prove a candidate through its installed launcher."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import cast
from urllib.parse import urljoin

import click
import httpx

from planner.environments.app import validate_app_manifest, validate_app_sha
from planner.environments.deployment_gate_contracts import (
    GateEvidence,
    HealthObservation,
    ServedObservation,
)


def observe_served(current_root: Path, health_url: str) -> ServedObservation:
    """Keep concrete unavailable observations too; never infer a healthy base."""
    path = current_root / "app" / "manifest.json"
    manifest: str | None = None
    state = "missing"
    sha = None
    try:
        manifest = path.read_text(encoding="utf-8")
        state = "present"
        sha = validate_app_manifest(path).app_sha
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        state = "unreadable" if manifest is None else "invalid"
    health: HealthObservation
    try:
        response = httpx.get(health_url, params={"expected_sha": sha or "unknown"}, timeout=2)
        health = {"status": response.status_code, "body": response.text, "error": None}
        try:
            body = response.json()
        except ValueError:
            body = None
        if (
            response.status_code != 200
            or not isinstance(body, dict)
            or body.get("app_sha") != sha
            or body.get("ready") is not True
        ):
            sha = None
    except httpx.HTTPError as exc:
        health = {"status": None, "body": None, "error": type(exc).__name__}
        sha = None
    return {
        "current_root": str(current_root.resolve()),
        "health_url": health_url,
        "manifest_state": state,
        "manifest": manifest,
        "health": health,
        "base_sha": sha,
    }


def read_gate_evidence(path: Path) -> GateEvidence:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {
        "requested_sha",
        "artifact_digest",
        "observation",
    }:
        raise ValueError("invalid deployment gate evidence")
    if not isinstance(value["requested_sha"], str):
        raise ValueError("invalid requested SHA")
    validate_app_sha(value["requested_sha"])
    if (
        not isinstance(value["artifact_digest"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["artifact_digest"]) is None
    ):
        raise ValueError("invalid deployment artifact digest")
    observation = value["observation"]
    if not isinstance(observation, dict) or set(observation) != {
        "current_root",
        "health_url",
        "manifest_state",
        "manifest",
        "health",
        "base_sha",
    }:
        raise ValueError("invalid served observation")
    if not isinstance(observation["current_root"], str) or not isinstance(
        observation["health_url"], str
    ):
        raise ValueError("invalid served observation location")
    if not isinstance(observation["manifest_state"], str) or observation["manifest_state"] not in {
        "present",
        "missing",
        "unreadable",
        "invalid",
    }:
        raise ValueError("invalid manifest state")
    if observation["manifest"] is not None and not isinstance(observation["manifest"], str):
        raise ValueError("invalid manifest observation")
    if observation["base_sha"] is not None:
        if not isinstance(observation["base_sha"], str):
            raise ValueError("invalid base SHA")
        validate_app_sha(observation["base_sha"])
    health = observation["health"]
    if not isinstance(health, dict) or set(health) != {"status", "body", "error"}:
        raise ValueError("invalid health observation")
    if health["status"] is not None and type(health["status"]) is not int:
        raise ValueError("invalid health status")
    if any(
        health[key] is not None and not isinstance(health[key], str) for key in ("body", "error")
    ):
        raise ValueError("invalid health response")
    return cast(GateEvidence, value)


def verify_gate_evidence(evidence: GateEvidence, current_root: Path, candidate_app: Path) -> None:
    manifest = validate_app_manifest(
        candidate_app / "manifest.json",
        expected_sha=evidence["requested_sha"],
        require_runtime=True,
    )
    if manifest.artifact_digest != evidence["artifact_digest"]:
        raise ValueError("candidate artifact differs from checked artifact")
    previous = evidence["observation"]
    if previous["current_root"] != str(current_root.resolve()):
        raise ValueError("gate evidence names another deployment root")
    if observe_served(current_root, previous["health_url"]) != previous:
        raise ValueError("served base observation changed since checks")


def smoke_environment(root: Path, port: int) -> dict[str, str]:
    """No ambient values or credentials cross the candidate boundary."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(root / "home"),
        "TMPDIR": str(root / "tmp"),
        "XDG_RUNTIME_DIR": str(root / "run"),
        "PLAN_CONFIG_PATH": str(root / "config.yaml"),
        "PLAN_DB_PATH": str(root / "state" / "planning.db"),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_BACKUP_DIR": str(root / "backups"),
        "PLAN_HERMES_HOME": str(root / "home" / ".hermes"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "run" / "dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(root / "run" / "control.sock"),
        "PLAN_PORT": str(port),
        "PLAN_DISPATCH_ENABLED": "0",
    }


def smoke_candidate(candidate_app: Path, requested_sha: str, *, timeout: float = 45) -> str:
    candidate_app = candidate_app.resolve()
    before = validate_app_manifest(
        candidate_app / "manifest.json", expected_sha=requested_sha, require_runtime=True
    )
    with tempfile.TemporaryDirectory(prefix="panels-candidate-") as directory:
        root = Path(directory)
        for name in ("home", "tmp", "run", "state", "logs", "backups"):
            (root / name).mkdir()
        (root / "config.yaml").write_text("{}\n", encoding="utf-8")
        for _attempt in range(3):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            with (root / "bootstrap.log").open("w+") as log:
                process = subprocess.Popen(
                    [str(candidate_app / "bin" / "panels-launcher"), "serve"],
                    cwd=root,
                    env=smoke_environment(root, port),
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    base = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + timeout
                    with httpx.Client(timeout=2) as client:
                        while time.monotonic() < deadline and process.poll() is None:
                            try:
                                response = client.get(
                                    base + "/api/health", params={"expected_sha": requested_sha}
                                )
                                if (
                                    response.status_code == 200
                                    and response.json().get("app_sha") == requested_sha
                                    and response.json().get("ready") is True
                                ):
                                    break
                            except (httpx.HTTPError, ValueError):
                                pass
                            time.sleep(0.1)
                        else:
                            log.seek(0)
                            detail = log.read()
                            if "address already in use" in detail.lower():
                                continue
                            raise ValueError(f"candidate bootstrap failed: {detail[-4000:]}")
                        page = client.get(base + "/")
                        page.raise_for_status()
                        assets = set(re.findall(r"""(?:src|href)=["']([^"']+)["']""", page.text))
                        assets = {asset for asset in assets if asset.startswith("/_app/")}
                        if not assets:
                            raise ValueError("candidate root has no referenced built assets")
                        for asset in assets:
                            client.get(urljoin(base, asset)).raise_for_status()
                        initialized = client.get(base + "/api/projects")
                        initialized.raise_for_status()
                        if not isinstance(initialized.json().get("projects"), list):
                            raise ValueError("candidate initialized API returned no project list")
                    break
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    # A failed supervisor can leave an owned child alive.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        else:
            raise ValueError("candidate could not acquire an available port")
    after = validate_app_manifest(
        candidate_app / "manifest.json", expected_sha=requested_sha, require_runtime=True
    )
    if before.artifact_digest != after.artifact_digest:
        raise ValueError("candidate artifact changed during smoke")
    return after.artifact_digest


@click.group()
def main() -> None:
    """Release proof using the control runtime, never the requested source runtime."""


@main.command("observe")
@click.option("--current-root", type=click.Path(path_type=Path), required=True)
@click.option("--health-url", required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def observe_command(current_root: Path, health_url: str, output: Path) -> None:
    output.write_text(json.dumps(observe_served(current_root, health_url), sort_keys=True) + "\n")


@main.command("smoke")
@click.option("--candidate-app", type=click.Path(path_type=Path), required=True)
@click.option("--requested-sha", required=True)
@click.option("--observation", type=click.Path(path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def smoke_command(candidate_app: Path, requested_sha: str, observation: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    try:
        digest = smoke_candidate(candidate_app, requested_sha)
        value = {
            "requested_sha": requested_sha,
            "artifact_digest": digest,
            "observation": json.loads(observation.read_text()),
        }
        output.write_text(json.dumps(value, sort_keys=True) + "\n")
        read_gate_evidence(output)
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        output.unlink(missing_ok=True)
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
