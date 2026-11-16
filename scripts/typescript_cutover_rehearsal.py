"""Hosted-only real-service Python bridge, Node cutover, and rollback proof.

Publication discovery is replaced by locally built fixture archives. Everything
from the external installer launch through service health uses production code.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

BASELINE = "04ee43914bdff2095284741248eeda8bbdb5ce9a"
SOURCE = Path(__file__).resolve().parents[1]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if check and result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        result.check_returncode()
    return result


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def api(url: str, body: dict[str, Any] | None = None) -> Any:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Tailscale-User-Login": "cutover@example.invalid",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def health(url: str, sha: str, timeout: float = 120) -> dict[str, Any]:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            result = api(url + "?expected_sha=" + sha)
            if result.get("ready") is True and result.get("app_sha") == sha:
                return cast(dict[str, Any], result)
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"exact release health failed: {sha}")


def build_v1(root: Path, sha: str, name: str) -> tuple[Path, dict[str, Any]]:
    from planner.environments.app import build_exported_app
    from planner.environments.update_artifacts import create_app_archive

    source = root / (name + "-source")
    run("git", "clone", "--shared", "--no-checkout", str(SOURCE), str(source))
    run("git", "-C", str(source), "checkout", "--detach", sha)
    candidate = root / (name + "-candidate")
    build_exported_app(
        source, requested_sha=sha, candidate_app=candidate, install_dependencies=True
    )
    archive = root / (name + ".tar.gz")
    digest = create_app_archive(candidate, archive)
    manifest = json.loads((candidate / "manifest.json").read_text())
    shutil.rmtree(candidate)
    return archive, {"manifest": manifest, "archive_sha256": digest}


def fixture(current: Path, url: str, root: Path, service: str) -> dict[str, Any]:
    """Copy a baseline-created synthetic database, never stamp an old revision."""
    project = api(
        url.removesuffix("/health") + "/projects",
        {"name": "Cutover fixture", "summary": "Synthetic hosted migration proof"},
    )
    ids = []
    for title in ("Unfinished Consequences fixture", "Done fixture"):
        record = api(
            url.removesuffix("/health") + "/tickets",
            {
                "title": title,
                "brief": "Synthetic fixture. Never dispatch this Ticket.",
                "project_id": project["id"],
                "worker_type": "coding",
                "ceiling": "needs_brief",
            },
        )
        ids.append(record["id"])
    run("systemctl", "--user", "stop", service)
    database = current / "data/planner.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE tickets SET stage='needs_consequences',ceiling='done',"
            "pending_proposal=NULL WHERE id=?",
            (ids[0],),
        )
        connection.execute(
            "UPDATE tickets SET stage='done',ceiling='done',pending_proposal=NULL WHERE id=?",
            (ids[1],),
        )
        connection.commit()
        with sqlite3.connect(root / "fixture.db") as destination:
            connection.backup(destination)
    return {"unfinished": ids[0], "done": ids[1], "project": project["id"]}


def schema(database: Path, ids: dict[str, Any]) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        navigation = connection.execute(
            "SELECT value FROM navigation_records WHERE kind='ticket' AND id=?",
            (ids["unfinished"],),
        ).fetchone()
        navigation_value = None if navigation is None else json.loads(navigation[0])
        return {
            "head": connection.execute("SELECT version_num FROM alembic_version").fetchall(),
            "agent_launch_tokens": bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_launch_tokens'"
                ).fetchone()
            ),
            "tickets": connection.execute(
                "SELECT id,stage,ceiling FROM tickets WHERE id IN (?,?) ORDER BY id",
                (ids["unfinished"], ids["done"]),
            ).fetchall(),
            "navigation_value": navigation_value,
            "navigation_ceiling": None
            if navigation_value is None
            else navigation_value.get("ceiling"),
            "project": connection.execute(
                "SELECT id,name FROM projects WHERE id=?", (ids["project"],)
            ).fetchone(),
        }


def install(
    current: Path,
    archive: Path,
    receipt: dict[str, Any],
    service: str,
    url: str,
    root: Path,
    label: str,
    *,
    fail: bool = False,
    mac: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from planner.environments.update_artifacts import extract_app_archive
    from planner.environments.update_attempts import (
        FileUpdateAttemptStore,
        attempt_path,
        parse_release,
        utc_now,
    )
    from planner.environments.update_contracts import SUPPORTED_UPDATE_SCHEMA_FLOOR, UpdateAttempt
    from planner.environments.update_install import (
        installer_command,
        require_exact_schema_frontier,
        schema_change_pending,
        write_installer,
    )

    sha = receipt["manifest"]["app_sha"]
    previous = json.loads((current / "app/manifest.json").read_text())["app_sha"]
    staged = current / (".app-next-" + sha)
    if not staged.exists():
        extract_app_archive(archive, staged, receipt["archive_sha256"], sha)
    if receipt["manifest"]["runtime_contract"]["version"] == 2:
        require_exact_schema_frontier(current / "data/planner.db", staged)
    backup = None
    if schema_change_pending(current / "data/planner.db", staged):
        backup = run(
            str(current / "app/bin/panels"),
            "environment",
            "backup-current",
            "--source-db",
            str(current / "data/planner.db"),
            "--backup-dir",
            str(current / "data/backups"),
            "--current-app",
            str(current / "app"),
        ).stdout.strip()
    attempt_id = "cutover-" + label
    now = utc_now()
    store = FileUpdateAttemptStore(attempt_path(current / "data"))
    release = parse_release(
        {
            **receipt,
            "publication_run_id": int(os.environ["GITHUB_RUN_ID"]),
            "artifact_id": 0,
            "proof_artifact_id": 0,
            "schema_floor": SUPPORTED_UPDATE_SCHEMA_FLOOR,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "mac": mac,
        }
    )
    store.create(
        UpdateAttempt(
            attempt_id=attempt_id,
            from_sha=previous,
            to_sha=sha,
            mode="now",
            confirm_activity=False,
            state="installing",
            created_at=now,
            updated_at=now,
            release=release,
            backup=backup,
            mac_attempt_id=None if mac is None else "cutover-v2.mac",
            deadline_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
        )
    )
    # A loopback health witness refuses only the candidate SHA. Previous health
    # still comes from the real service, so the installer proves rollback health.
    proxy = None
    installer_url = url
    if fail:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        class Witness(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.endswith("expected_sha=" + sha):
                    self.send_response(503)
                    self.end_headers()
                    return
                try:
                    data = json.dumps(api(url + "?" + self.path.split("?", 1)[1])).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    self.send_response(503)
                    self.end_headers()

            def log_message(self, *_args: object) -> None:
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Witness)
        proxy_thread = Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()
        installer_url = f"http://127.0.0.1:{proxy.server_port}/api/health"
    try:
        command = installer_command(
            write_installer(current / "data"),
            current_root=current,
            attempt_id=attempt_id,
            service=service,
            health_url=installer_url,
            health_timeout_seconds=15 if fail else 180,
        )
        result = run(
            "systemd-run",
            "--user",
            "--wait",
            "--collect",
            "--pipe",
            "--quiet",
            "--unit=" + attempt_id,
            *command,
            check=False,
        )
        (root / (label + "-installer.log")).write_text(result.stdout + result.stderr)
    finally:
        if proxy:
            proxy.shutdown()
            proxy.server_close()
            proxy_thread.join()
    attempt: dict[str, Any] = json.loads(attempt_path(current / "data").read_text())
    write(root / (label + "-attempt.json"), attempt)
    expected = "failed" if fail else "done"
    if attempt["state"] != expected:
        raise RuntimeError(f"{label}: expected {expected}, got {attempt}")
    steps = attempt["steps"]
    required = {"service_stopped", "app_swapped", "service_started"}
    required |= {"rollback_app_restored", "rollback_healthy"} if fail else {"healthy", "done"}
    if not required <= steps.keys():
        raise RuntimeError(f"{label}: missing installer steps: {required - steps.keys()}")
    write(root / (label + "-health.json"), health(url, previous if fail else sha))
    return attempt


def main() -> None:
    if (
        os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("GITHUB_ACTIONS") != "true"
    ):
        raise RuntimeError("disposable hosted runner required")
    root = Path(os.environ["CUTOVER_ROOT"]).resolve()
    if not root.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise RuntimeError("proof root must belong to RUNNER_TEMP")
    current = root / "current"
    bridge = os.environ["CUTOVER_BRIDGE_SHA"]
    candidate = run("git", "rev-parse", "HEAD").stdout.strip()
    if BASELINE == bridge or bridge == candidate:
        raise RuntimeError("three distinct actual commits required")
    run("git", "merge-base", "--is-ancestor", bridge, candidate)
    service_base = "panels-cutover-" + os.environ["GITHUB_RUN_ID"]
    service = service_base + ".service"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}/api/health"
    began = time.monotonic()
    outcome: dict[str, Any] = {
        "completed": False,
        "baseline": BASELINE,
        "bridge": bridge,
        "candidate": candidate,
        "boundary": "Fixture staging, real first installer and external update installer.",
    }
    try:
        baseline_archive, baseline_receipt = build_v1(root, BASELINE, "baseline")
        receipt_file = root / "baseline-receipt.json"
        write(receipt_file, baseline_receipt)
        result = run(
            sys.executable,
            str(SOURCE / "scripts/panels_install.py"),
            "home",
            "--archive",
            str(baseline_archive),
            "--receipt",
            str(receipt_file),
            "--root",
            str(current),
            "--service",
            service_base,
            "--port",
            str(port),
            "--isolated-providers",
        )
        (root / "first-install.log").write_text(result.stdout + result.stderr)
        write(root / "baseline-health.json", health(url, BASELINE))
        run("systemctl", "--user", "stop", service)
        config_path = current / "data/config.yaml"
        config = json.loads(config_path.read_text())
        config.update(
            dispatch_enabled=False,
            trusted_ingress_provider="tailscale",
            trusted_ingress_allowed_login="cutover@example.invalid",
            trusted_ingress_canonical_origin="https://cutover.invalid",
        )
        write(config_path, config)
        run("systemctl", "--user", "start", service)
        health(url, BASELINE)
        ids = fixture(current, url, root, service)
        with (
            sqlite3.connect(root / "fixture.db") as source,
            sqlite3.connect(current / "data/planner.db") as destination,
        ):
            source.backup(destination)
        run("systemctl", "--user", "start", service)
        health(url, BASELINE)
        before = schema(current / "data/planner.db", ids)
        write(root / "schema-before.json", before)
        if before["agent_launch_tokens"] or before["head"] == [("restore_consequences_review",)]:
            raise RuntimeError("baseline fixture does not exercise the two migrations")
        bridge_archive, bridge_receipt = build_v1(root, bridge, "bridge")
        install(current, bridge_archive, bridge_receipt, service, url, root, "bridge")
        after = schema(current / "data/planner.db", ids)
        write(root / "schema-after.json", after)
        tickets = {row[0]: row[1:] for row in after["tickets"]}
        assert after["navigation_ceiling"] == "needs_consequences"
        assert after["head"] == [("restore_consequences_review",)] and after["agent_launch_tokens"]
        assert tickets[ids["unfinished"]] == ("needs_consequences", "needs_consequences")
        assert tickets[ids["done"]] == ("done", "done") and after["project"] == before["project"]
        baseline_archive.unlink()
        request = root / "v2-request.json"
        output = root / "v2"
        write(
            request,
            {
                "source_root": str(SOURCE),
                "requested_sha": candidate,
                "output_directory": str(output),
                "public_cli_entrypoint": "server/cli/main.ts",
            },
        )
        result = run(
            "node", str(SOURCE / "server/environments/release/publication_main.ts"), str(request)
        )
        (root / "v2-build.log").write_text(result.stdout + result.stderr)
        archive = output / "panels-app.tar.gz"
        from planner.environments.update_artifacts import hash_archive

        receipt = {
            "manifest": json.loads((output / "linux-publication-build.json").read_text())[
                "manifest"
            ],
            "archive_sha256": hash_archive(archive),
        }
        import tarfile

        with tarfile.open(archive) as packed:
            migrations = [
                name
                for name in packed.getnames()
                if name.startswith("src/planner/core/migrations/versions/")
            ]
        assert migrations and any(
            name.endswith("restore_consequences_review.py") for name in migrations
        )
        write(root / "archive-migrations.json", migrations)
        from typescript_cutover_mac import check, prepare

        mac = prepare(current, url, candidate)
        install(current, archive, receipt, service, url, root, "failed-health", fail=True, mac=mac)
        assert schema(current / "data/planner.db", ids) == after
        install(current, archive, receipt, service, url, root, "v2", mac=mac)
        status = api(url.removesuffix("/health") + "/updates")
        assert (
            status["attempt"]["attempt_id"] == "cutover-v2" and status["attempt"]["state"] == "done"
        )
        write(root / "v2-status.json", status)
        write(root / "v2-mac-target.json", check(current, url, candidate))
        # Manual code rollback retains the database that passed candidate health.
        run("systemctl", "--user", "stop", service)
        with sqlite3.connect(current / "data/planner.db") as connection:
            connection.execute(
                "UPDATE projects SET name='After-health sentinel' WHERE id=?", (ids["project"],)
            )
            connection.commit()
        retained = schema(current / "data/planner.db", ids)
        (current / "app").rename(current / ".app-after-health")
        (current / (".app-previous-" + bridge)).rename(current / "app")
        run("systemctl", "--user", "start", service)
        write(root / "manual-rollback-health.json", health(url, bridge))
        assert schema(current / "data/planner.db", ids) == retained
        write(root / "manual-rollback-data.json", retained)
        outcome["completed"] = True
    except BaseException as error:
        outcome["error"] = str(error)
        raise
    finally:
        outcome["seconds"] = time.monotonic() - began
        journal = run("journalctl", "--user", "-u", service, "--no-pager", check=False)
        (root / "service.log").write_text(journal.stdout + journal.stderr)
        cleanup = []
        for unit in (service, service_base + ".backup.timer", service_base + ".backup.service"):
            result = run("systemctl", "--user", "disable", "--now", unit, check=False)
            inactive = run("systemctl", "--user", "is-active", unit, check=False)
            cleanup.append(
                {
                    "unit": unit,
                    "exit_code": result.returncode,
                    "detail": result.stderr,
                    "active": inactive.stdout.strip(),
                }
            )
            if inactive.returncode == 0:
                outcome["completed"] = False
                outcome["cleanup_failure"] = unit
        write(root / "cleanup.json", cleanup)
        write(root / "proof.json", outcome)
        if outcome.get("cleanup_failure"):
            raise RuntimeError("rehearsal service remained active")


if __name__ == "__main__":
    main()
