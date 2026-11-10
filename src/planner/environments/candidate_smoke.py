"""Prove a built candidate through its installed launcher before it is published."""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx

from planner.environments.app import validate_app_manifest


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
                        initialized = client.get(base + "/api/projects", params={"detail": "full"})
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
