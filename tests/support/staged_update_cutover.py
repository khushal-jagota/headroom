"""Disposable update cutover shared by integration and existing browser proof.

The app packaging fixture and native admission are synthetic. File replacement,
process restart, exact-SHA HTTP, request identity and lifecycle records are real.
Only the subprocess recorded by StagedProcess is stopped.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from unittest.mock import patch

import httpx
from tests.unit.test_deployment import SHA_A, SHA_B, _app

from planner.environments.app import validate_app_manifest
from planner.environments.deployment import HttpHealthClient, deploy_app
from planner.environments.deployment_lifecycle import (
    DeploymentLifecycleStore,
    _atomic_write,
    _exclusive_lock,
)
from planner.environments.update_requests import FileUpdateRequestStore

_SERVER = r'''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
sha = json.loads((Path(sys.argv[1]) / "app/manifest.json").read_text())["app_sha"]
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/health":
            ready = parse_qs(parsed.query).get("expected_sha") == [sha] and sha != sys.argv[3]
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ready": ready, "app_sha": sha}).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            document = "<!doctype html><title>Staged version</title><p>" + sha + "</p>"
            self.wfile.write(document.encode())
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[2])), Handler).serve_forever()
'''


class StagedProcess:
    def __init__(self, root: Path) -> None:
        self.root = root
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.process: subprocess.Popen[bytes] | None = None
        self.fail_sha = SHA_B
        self.pids: list[int] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.process.stderr is not None:
            self.process.stderr.close()
        self.process = None

    def restart(self) -> None:
        self.stop()
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("PLAN_")
        }
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", _SERVER,
             str(self.root), str(self.port), self.fail_sha],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.pids.append(self.process.pid)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                assert self.process.stderr is not None
                raise AssertionError(self.process.stderr.read().decode())
            try:
                if httpx.get(self.url, timeout=0.2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
        raise AssertionError("Disposable staged HTTP process did not start")


class DeadlineHealth:
    """Use real HTTP health, then advance the fixture's durable deadline."""

    def __init__(self, url: str, store: FileUpdateRequestStore, *, expire: bool) -> None:
        self.client = HttpHealthClient(url, poll_seconds=0.02)
        self.store, self.expire = store, expire

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        healthy = self.client.wait_for_sha(sha, deadline=deadline)
        if healthy and sha == SHA_B and self.expire:
            with _exclusive_lock(self.store.lock_path):
                receipt = self.store.read()
                assert receipt is not None and receipt.state == "applying"
                expired = replace(
                    receipt, deadline_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                )
                _atomic_write(self.store.path, asdict(expired))
        return healthy


class StagedCutover:
    """Use one disposable installed app across failed and successful browser attempts."""

    def __init__(self, directory: Path) -> None:
        self.root = directory / "current"
        _app(self.root / "app", SHA_A, "old")
        self.candidate = _app(directory / "candidate", SHA_B, "new")
        self.manifest = validate_app_manifest(
            self.candidate / "manifest.json", require_runtime=True,
        )
        self.process = StagedProcess(self.root)
        self.outcomes: list[dict[str, object]] = []
        try:
            self.process.restart()
        except BaseException:
            self.process.stop()
            raise

    def close(self) -> None:
        self.process.stop()

    def observed_sha(self) -> str:
        response = httpx.get(self.process.url + "/api/health", params={"expected_sha": SHA_B})
        # The initial or restored app can report its actual SHA with a 503 for B.
        return str(response.json()["app_sha"])

    def apply(
        self, store: FileUpdateRequestStore, lifecycle: DeploymentLifecycleStore,
        database: Path, *, fail: bool = False,
    ) -> str:
        started = time.monotonic()
        receipt = store.read()
        assert receipt is not None and receipt.state in {"dispatched", "applying"}
        assert receipt.release.manifest == self.manifest
        self.process.fail_sha = SHA_B if fail else ""
        current = lifecycle.read().lifecycle
        if current is None or current.deployment_id != receipt.request_id:
            lifecycle.start(
                receipt.request_id, SHA_B, prior_sha=self.observed_sha(),
                expected_deployment_id=current.deployment_id if current is not None else None,
            )

        def claim() -> None:
            current_receipt = store.read()
            assert current_receipt is not None
            if current_receipt.state == "dispatched":
                store.transition(
                    receipt.request_id, frozenset({"dispatched"}), "applying",
                    deployment_id=receipt.request_id,
                )

        def publish(phase: str, **values: object) -> None:
            lifecycle.transition(receipt.request_id, phase, **values)  # type: ignore[arg-type]

        snapshot = self.root.parent / "snapshot.db"

        def backup(_: str) -> Path:
            shutil.copy2(database, snapshot)
            return snapshot

        def restore(saved: Path) -> None:
            shutil.copy2(saved, database)

        # This helper uses the same explicit synthetic native admission as _app's
        # existing unit fixture. It does not skip any update authority check.
        with patch("planner.environments.deployment.admit_runtime", lambda *_: None):
            result = deploy_app(
                candidate_app=self.candidate, current_root=self.root, source_db=database,
                backup=backup, restore=restore, service=self.process,
                health=HttpHealthClient(self.process.url + "/api/health", poll_seconds=0.02),
                health_timeout_seconds=0.5, before_stop=claim, lifecycle_transition=publish,
                deployment_id=receipt.request_id,
                installation_guard=partial(store.installation_guard, receipt.request_id),
            )
        observed = self.observed_sha()
        assert result.status == ("rolled_back" if fail else "succeeded")
        assert observed == (SHA_A if fail else SHA_B)
        self.outcomes.append({
            "request_id": receipt.request_id, "outcome": result.status,
            "observedSHA": observed, "seconds": round(time.monotonic() - started, 3),
        })
        return observed
