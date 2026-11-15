"""One hosted-only real systemd, TLS, credential and protocol-26 enrollment proof."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from uuid import uuid4

REVISION = "e0bbb4cc49cc701d9f28c13e3a24b57fcad6c6ce"
EXECUTOR_UNIT = "panels-executor-typescript.service"
UNSETTLED_COMMANDS: list[str] = []


def run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    private: str | None = None,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv, env=env, input=private, capture_output=True, text=True, timeout=timeout
        )
    except (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit):
        UNSETTLED_COMMANDS.append(argv[0])
        raise
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed: {argv[0]}: {result.stdout[-3000:]} {result.stderr[-3000:]}"
        )
    return result


def birth(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def service(name: str) -> dict[str, str]:
    result = run(
        [
            "systemctl",
            "--user",
            "show",
            name,
            "--property=LoadState,ActiveState,MainPID,ControlGroup",
        ],
        check=False,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def members(name: str) -> dict[int, str | None]:
    group = service(name).get("ControlGroup", "")
    result: dict[int, str | None] = {}
    if group:
        for path in (Path("/sys/fs/cgroup") / group.lstrip("/")).rglob("cgroup.procs"):
            for value in path.read_text().split():
                pid = int(value)
                result[pid] = birth(pid)
    return result


def port_free(port: int) -> None:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))


def main() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("Enrollment acceptance requires a disposable GitHub-hosted runner")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    product, evidence = (Path(value).resolve() for value in sys.argv[1:])
    evidence.mkdir(parents=True, exist_ok=True)
    if run(["git", "-C", str(product), "rev-parse", "HEAD"]).stdout.strip() != REVISION:
        raise RuntimeError("Product source is not the approved revision")
    # These imports and the proxy's subprocess resolve exclusively from the pinned product.
    sys.path.insert(0, str(product))
    from tests.support.executor_https import isolated_https_proxy
    from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

    from planner.core.db import connect, create_schema

    root = Path(tempfile.mkdtemp(prefix="p5enroll-", dir="/tmp"))
    report: dict[str, object] = {
        "product_revision": REVISION,
        "harness_revision": os.environ["HARNESS_REVISION"],
        "root": str(root),
        "connected_cases": 1,
        "cleanup_verified": False,
    }
    began = time.monotonic()
    node = str(Path(shutil.which("node") or "").resolve())
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    unit_file = account / ".config/systemd/user" / EXECUTOR_UNIT
    home_unit = "panels-enrollment-home-" + uuid4().hex + ".service"
    # Fixed production unit has to be absent before this proof may own it.
    if unit_file.exists() or service(EXECUTOR_UNIT).get("LoadState") != "not-found":
        raise RuntimeError("Executor unit already exists on the disposable account")
    home_env = dict(os.environ)
    home_env["PYTHONPATH"] = str(product)
    home_env["PLAN_APP_ROOT"] = str(product)
    (root / "home-state").mkdir()
    homes = create_synthetic_homes(root / "home-state")
    home_env.update(homes)
    home_state = root / "home-state"
    for directory in ["data", "logs", "backups", "workspace", "run", "scratch"]:
        (home_state / directory).mkdir(parents=True, exist_ok=True)
    home_env.update(
        {
            "PLAN_CONFIG_PATH": str(home_state / "config.yaml"),
            "PLAN_DB_PATH": str(home_state / "data/planning.db"),
            "PLAN_DATA_DIR": str(home_state / "data"),
            "PLAN_LOGS_DIR": str(home_state / "logs"),
            "PLAN_BACKUP_DIR": str(home_state / "backups"),
            "PLAN_WORKSPACE_ROOT": str(home_state / "workspace"),
            "PLAN_TICKET_SCRATCH_ROOT": str(home_state / "scratch"),
            "PLAN_TICKET_FILES_DIR": str(home_state / "data/files"),
            "PLAN_CREDENTIAL_KEY_PATH": str(home_state / "data/credential-key"),
            "PLAN_DISPATCHER_LOCK_PATH": str(home_state / "run/dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(home_state / "run/control.sock"),
            "PLAN_SERVER_LIFECYCLE_LEASE_PATH": str(home_state / "run/lifecycle.lock"),
            "PLAN_APP_SHA": REVISION,
            "PLAN_TEST_MODE": "0",
            "PLAN_DISPATCH_ENABLED": "false",
            "PLAN_NOTIFICATIONS_ENABLED": "false",
        }
    )
    (home_state / "config.yaml").write_text("{}\n")
    with synthetic_home_environment(homes), closing(connect(home_env["PLAN_DB_PATH"])) as database:
        create_schema(database)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        home_port = listener.getsockname()[1]
    home_env["PLAN_PORT"] = str(home_port)
    home_env["PLAN_SERVER_URL"] = f"http://127.0.0.1:{home_port}"
    build = Path(os.environ["TMPDIR"]) / "enrollment-build"
    request = root / "build-request.json"
    request.write_text(
        json.dumps(
            {"source_root": str(product), "requested_sha": REVISION, "output_directory": str(build)}
        )
    )
    owned_units: list[str] = []
    observed: dict[int, str | None] = {}
    old_trust = None
    trust_set = False
    proxy_port = None
    success = False
    installed_unit: tuple[int, int, str] | None = None
    machine_id: str | None = None
    try:
        print("Build the pinned sealed Linux executor archive", flush=True)
        result = run(
            [node, str(product / "server/environments/linux_executor/build_main.ts"), str(request)],
            timeout=2100,
        )
        (evidence / "build.log").write_text(result.stdout + result.stderr)
        publication = json.loads((build / "linux-executor-publication.json").read_text())
        assert publication["application_revision"] == REVISION
        archive = build / "panels-linux-executor.tar.gz"
        assert archive.stat().st_size == publication["archive_size"]
        with archive.open("rb") as stream:
            assert (
                hashlib.file_digest(stream, "sha256").hexdigest() == publication["archive_sha256"]
            )
        report["archive"] = publication
        shutil.copy2(build / "linux-executor-publication.json", evidence / "publication.json")
        print("Boot the actual production home and validated loopback TLS ingress", flush=True)
        with isolated_https_proxy(root / "tls", home_env["PLAN_SERVER_URL"]) as proxy:
            proxy_port = int(proxy.origin.rsplit(":", 1)[1])
            home_env.update(
                {
                    "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
                    "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@example.com",
                    "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": proxy.origin,
                }
            )
            old_environment = dict(
                line.split("=", 1)
                for line in run(["systemctl", "--user", "show-environment"]).stdout.splitlines()
                if "=" in line
            )
            old_trust = old_environment.get("NODE_EXTRA_CA_CERTS")
            run(
                [
                    "systemctl",
                    "--user",
                    "set-environment",
                    "NODE_EXTRA_CA_CERTS=" + str(proxy.ca_path),
                ]
            )
            trust_set = True
            assert (
                "NODE_EXTRA_CA_CERTS=" + str(proxy.ca_path)
                in run(["systemctl", "--user", "show-environment"]).stdout.splitlines()
            )
            owned_units.append(home_unit)
            run(
                [
                    "systemd-run",
                    "--user",
                    "--unit=" + home_unit,
                    "--collect",
                    "--property=Type=exec",
                    "--property=KillMode=control-group",
                    "--property=TimeoutStopSec=100",
                    "--property=Delegate=yes",
                    "--working-directory=" + str(product),
                    "/usr/bin/env",
                    "-i",
                    *[f"{key}={value}" for key, value in home_env.items()],
                    node,
                    str(product / "server/main.ts"),
                ]
            )
            context = ssl.create_default_context(cafile=str(proxy.ca_path))
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
            )
            deadline = time.monotonic() + 45
            while True:
                try:
                    with opener.open(
                        proxy.origin + "/api/health?expected_sha=" + REVISION, timeout=3
                    ) as response:
                        assert response.status == 200
                    break
                except (urllib.error.URLError, TimeoutError, AssertionError):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Actual home failed readiness") from None
                    time.sleep(0.1)
            home_state_now = service(home_unit)
            home_births = members(home_unit)
            home_pid = int(home_state_now["MainPID"])
            assert home_pid in home_births and home_births[home_pid] is not None
            observed.update(home_births)
            report["home_service"] = {"unit": home_unit, **home_state_now}
            # An untrusted HTTPS client must refuse this certificate.
            untrusted = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                untrusted.open(proxy.origin + "/api/health?expected_sha=" + REVISION, timeout=3)
            except urllib.error.URLError as error:
                assert isinstance(error.reason, ssl.SSLCertVerificationError)
                report["untrusted_certificate_refused"] = True
            else:
                raise RuntimeError("Untrusted client accepted the test certificate")
            with opener.open(proxy.origin + "/api/meta", timeout=5) as response:
                assert json.load(response)["test_mode"] is False
            registration = urllib.request.Request(
                proxy.origin + "/api/machines",
                method="POST",
                data=json.dumps({"name": "Hosted Linux enrollment", "platform": "linux"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with opener.open(registration, timeout=10) as response:
                grant = json.load(response)
            machine_id = grant["machine_id"]
            report["machine_id"] = machine_id
            joined_at = time.time_ns() // 1_000_000
            installation = root / "executor"
            private = dict(
                archive=str(archive),
                archive_sha256=publication["archive_sha256"],
                archive_size=publication["archive_size"],
                revision=REVISION,
                home_url=proxy.origin,
                machine_id=machine_id,
                credential=grant["credential"],
                root=str(installation),
                home=str(account),
                node_executable=node,
                isolated_providers=True,
            )
            print("Join through the real private entry and user systemd unit", flush=True)
            owned_units.append(EXECUTOR_UNIT)
            result = run(
                [node, str(product / "server/environments/linux_executor/join_main.ts")],
                private=json.dumps(private),
                timeout=240,
            )
            assert grant["credential"] not in result.stdout + result.stderr
            (evidence / "join.log").write_text(result.stdout + result.stderr)
            status = service(EXECUTOR_UNIT)
            assert status["ActiveState"] == "active" and int(status["MainPID"]) > 0
            executor_births = members(EXECUTOR_UNIT)
            executor_pid = int(status["MainPID"])
            assert executor_pid in executor_births and executor_births[executor_pid] is not None
            observed.update(executor_births)
            report["executor_service"] = {"unit": EXECUTOR_UNIT, **status}
            runtime = (
                installation
                / "installation/releases"
                / (REVISION + "-" + publication["archive_sha256"])
            )
            health_env = {**os.environ, "PLAN_EXECUTOR_ROOT": str(installation / "data")}
            health = json.loads(
                run(
                    [
                        node,
                        str(runtime / "server/executor/mac/entrypoint.ts"),
                        "executor",
                        "health",
                    ],
                    env=health_env,
                ).stdout
            )
            unit_text = unit_file.read_text()
            unit_identity = unit_file.stat()
            installed_unit = (unit_identity.st_dev, unit_identity.st_ino, unit_text)
            assert machine_id in unit_text and str(installation) in unit_text
            assert health["identity"]["application_revision"] == REVISION
            assert health["identity"]["protocol_version"] == 26
            assert health["lease_accepted_at_mac_ms"] >= joined_at
            assert not health["last_lease_failure"] and not health["refusal"]
            assert not health["admission_stopped"] and not health["going_to_sleep"]
            with sqlite3.connect(f"file:{home_env['PLAN_DB_PATH']}?mode=ro", uri=True) as database:
                row = database.execute(
                    "SELECT identity_json,last_lease_at_ms,refusal_json "
                    "FROM mac_executor_presence WHERE machine_id=?",
                    (machine_id,),
                ).fetchone()
            assert row is not None
            identity = json.loads(row[0])
            assert identity == health["identity"]
            assert identity["protocol_version"] == 26
            assert identity["application_revision"] == REVISION and row[1] >= joined_at
            assert row[2] is None
            report.update(
                join_started_at_ms=joined_at,
                executor_health=health,
                server_identity=identity,
                server_last_lease_at_ms=row[1],
            )
            print("Fresh credentialed protocol 26 lease accepted on executor and home", flush=True)
            success = True
    except BaseException as error:
        success = False
        report["failure"] = str(error)
        raise
    finally:
        cleanup_errors: list[str] = []
        for unit in reversed(owned_units):
            try:
                observed.update(members(unit))
                journal = run(
                    ["journalctl", "--user-unit=" + unit, "--no-pager", "-n", "150"], check=False
                )
                (evidence / (unit + ".log")).write_text(journal.stdout)
                if service(unit).get("LoadState") != "not-found":
                    run(["systemctl", "--user", "stop", unit], timeout=120)
                state = service(unit)
                assert state.get("MainPID", "0") == "0"
                assert state.get("ActiveState", "inactive") in {"inactive", "failed"}
                if unit == EXECUTOR_UNIT and unit_file.exists():
                    current_unit = unit_file.stat()
                    text = unit_file.read_text()
                    if installed_unit is not None:
                        assert (current_unit.st_dev, current_unit.st_ino, text) == installed_unit
                    else:
                        assert machine_id is not None and machine_id in text
                        assert str(root / "executor") in text
                    run(["systemctl", "--user", "disable", unit])
                    unit_file.unlink()
                    run(["systemctl", "--user", "daemon-reload"])
                assert service(unit).get("LoadState") == "not-found"
            except Exception as error:
                cleanup_errors.append(f"{unit}: {error}")
        for pid, original_birth in observed.items():
            if original_birth is not None and birth(pid) == original_birth:
                cleanup_errors.append(f"Process {pid} is still alive with birth {original_birth}")
        if trust_set:
            try:
                if old_trust is None:
                    run(["systemctl", "--user", "unset-environment", "NODE_EXTRA_CA_CERTS"])
                else:
                    run(
                        [
                            "systemctl",
                            "--user",
                            "set-environment",
                            "NODE_EXTRA_CA_CERTS=" + old_trust,
                        ]
                    )
                restored = dict(
                    line.split("=", 1)
                    for line in run(["systemctl", "--user", "show-environment"]).stdout.splitlines()
                    if "=" in line
                )
                assert restored.get("NODE_EXTRA_CA_CERTS") == old_trust
                report["ca_trust_restored"] = True
            except Exception as error:
                cleanup_errors.append(f"CA environment: {error}")
        for port in [home_port, proxy_port]:
            if port is not None:
                try:
                    port_free(port)
                except OSError as error:
                    cleanup_errors.append(f"Listener {port}: {error}")
        cleanup_errors.extend(
            "Unsettled command after interruption: " + command for command in UNSETTLED_COMMANDS
        )
        if not cleanup_errors:
            for owned_root in (build, root):
                try:
                    if owned_root.exists():
                        shutil.rmtree(owned_root)
                    assert not owned_root.exists()
                except Exception as error:
                    cleanup_errors.append(f"Root deletion {owned_root}: {error}")
                    break
        report.update(
            seconds=time.monotonic() - began,
            cleanup_errors=cleanup_errors,
            observed_process_births=observed,
            cleanup_verified=not cleanup_errors,
            passed=success and not cleanup_errors,
            retained=bool(cleanup_errors),
            retained_paths=[str(path) for path in (root, build) if path.exists()],
        )
        (evidence / "enrollment.json").write_text(json.dumps(report, indent=2) + "\n")
        if cleanup_errors:
            raise RuntimeError(
                "Enrollment cleanup uncertain; retained "
                + str(root)
                + ": "
                + "; ".join(cleanup_errors)
            )


if __name__ == "__main__":
    main()
