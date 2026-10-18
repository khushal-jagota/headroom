"""Executable native-host preparation and owner-session activation.

Native preparation uses only the standard library. Activation runs under the
new permanent executor interpreter, whose declared dependencies include IPC codecs.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    HostDeploymentAdapter,
    HostDeploymentResult,
    PreparedResidentPackage,
    ResidentDeploymentRecord,
    ResidentError,
    ResidentPaths,
    ServiceConfiguration,
)
from planner.environments.resident.native_builder import NativeRuntimeBuilder
from planner.environments.resident.packaging import (
    MANIFEST_NAME,
    build_source_package,
    package_from_dict,
    prepare_runtime,
    validate_runtime,
)
from planner.environments.resident.paths import data_paths, resident_paths


def read_prepared(app: Path, paths: ResidentPaths) -> PreparedResidentPackage:
    """Recover permanent runtime identity from the validated installed manifest."""
    manifest = app / MANIFEST_NAME
    raw = manifest.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("package"), dict):
        raise ResidentError("installed source package has no package identity")
    package = package_from_dict(data["package"])
    digest = hashlib.sha256(raw).hexdigest()
    prepared = PreparedResidentPackage(package, app, paths.runtimes / digest, digest)
    validate_runtime(prepared)
    return prepared


def activate(
    app: Path,
    *,
    home: Path,
    launch: ExecutorLaunchOptions,
    timeout_seconds: float,
) -> HostDeploymentResult:
    """Connect production owner IPC, launchd service and the fenced transaction."""
    from planner.environments.resident.control import OwnerExecutorControl
    from planner.environments.resident.logic.update import fresh_health
    from planner.environments.resident.service import LaunchAgentService
    from planner.environments.resident.update import update_resident

    if sys.platform != "darwin" or home != Path.home().absolute():
        raise ResidentError("resident activation requires the actual Mac owner's HOME")
    paths = resident_paths(home)
    candidate = read_prepared(app, paths)
    revision = candidate.package.application_revision
    if launch.application_revision != revision:
        raise ResidentError("launch revision does not match candidate package")
    control = OwnerExecutorControl(data_paths(paths).ipc_socket, timeout_seconds=timeout_seconds)
    service = LaunchAgentService(
        ServiceConfiguration(paths, os.getuid(), launch=launch),
        control,
        timeout_seconds=timeout_seconds,
        poll_seconds=0.1,
    )
    control.before_quiesce = service.prepare_quiesce
    install_marker = paths.deployment_root / "initial-install.json"
    if paths.app.exists():
        previous = read_prepared(paths.app, paths)
        if install_marker.exists():
            from planner.environments.resident.update import _update_lock

            with _update_lock(paths.deployment_root):
                if install_marker.is_symlink() or install_marker.stat().st_mode & 0o077:
                    raise ResidentError("initial installation marker is not owner-only")
                intent = json.loads(install_marker.read_bytes())
                if intent != {"runtime_digest": previous.runtime_digest}:
                    raise ResidentError(
                        "initial installation marker does not match installed source"
                    )
                if candidate.runtime_digest != previous.runtime_digest:
                    raise ResidentError(
                        "incomplete first install must resume the same exact package"
                    )
                observed_at = time.time_ns() // 1_000_000
                health = control.observe()
                if fresh_health(
                    health,
                    previous.package,
                    not_before_ms=observed_at,
                    now_ms=time.time_ns() // 1_000_000,
                ):
                    install_marker.unlink()
                    return HostDeploymentResult(
                        "mac",
                        revision,
                        "verified",
                        revision,
                        "Previously started first installation now proves exact readiness",
                    )
                if (
                    health is not None
                    or data_paths(paths).spool.exists()
                    or data_paths(paths).ipc_socket.exists()
                ):
                    raise ResidentError(
                        "first install may have run; automatic bootstrap retry refused"
                    )
                # install() independently requires positive launchd absence. No
                # replacement process is stopped and no source is changed.
                service.install(previous)
                install_marker.unlink()
                return HostDeploymentResult(
                    "mac",
                    revision,
                    "verified",
                    revision,
                    "Resumed identical first installation; exact readiness observed",
                )
        result = update_resident(
            paths,
            candidate,
            previous,
            control=control,
            service=service,
            validate_package=validate_runtime,
            now_ms=lambda: time.time_ns() // 1_000_000,
        )
        return HostDeploymentResult(
            "mac", revision, result.disposition, result.observed_revision, result.detail
        )
    started_at = time.time_ns() // 1_000_000
    from planner.environments.resident.update import _refuse_symlinks, _update_lock

    with _update_lock(paths.deployment_root):
        for path in (app, paths.app, paths.executor_root):
            _refuse_symlinks(path)
        if paths.app.exists() or app.resolve().is_relative_to(paths.executor_root.resolve()):
            raise ResidentError("first installation source or destination is unsafe")
        if control.observe() is not None:
            raise ResidentError("first installation found an existing executor; no source changes")
        if control.compatible(candidate.package) != "compatible":
            raise ResidentError("first installation spool compatibility is unproved")
        paths.app.parent.mkdir(parents=True, exist_ok=True)
        if install_marker.exists():
            if (
                install_marker.is_symlink()
                or install_marker.stat().st_mode & 0o077
                or json.loads(install_marker.read_bytes())
                != {"runtime_digest": candidate.runtime_digest}
                or data_paths(paths).spool.exists()
                or data_paths(paths).ipc_socket.exists()
            ):
                raise ResidentError("interrupted installation intent is unsafe to resume")
        else:
            marker_fd = os.open(
                install_marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(marker_fd, "w") as marker:
                json.dump({"runtime_digest": candidate.runtime_digest}, marker)
                marker.flush()
                os.fsync(marker.fileno())
        app.rename(paths.app)
        installed = replace(candidate, app=paths.app)
        validate_runtime(installed)
        service.install(installed)
        install_marker.unlink()
    health = control.observe()
    if not fresh_health(
        health, candidate.package, not_before_ms=started_at, now_ms=time.time_ns() // 1_000_000
    ):
        return HostDeploymentResult(
            "mac", revision, "failed", None, "Installed agent did not prove exact fresh readiness"
        )
    return HostDeploymentResult(
        "mac",
        revision,
        "verified",
        revision,
        "Exact installed executor revision ready; unattended/keychain proof remains separate",
    )


def prepare_and_activate(
    app: Path,
    *,
    python: Path,
    home: Path,
    launch: ExecutorLaunchOptions,
    timeout_seconds: float,
    tool_source: Path | None = None,
    npm: Path | None = None,
    node: Path | None = None,
) -> HostDeploymentResult:
    """Build natively, then execute activation with the declared runtime codecs."""
    if sys.platform != "darwin" or home != Path.home().absolute():
        raise ResidentError("native deployment requires the actual Mac owner's HOME")
    manifest = json.loads((app / MANIFEST_NAME).read_bytes())
    package = package_from_dict(manifest["package"])
    if package.application_revision != launch.application_revision:
        raise ResidentError("requested revision does not match package")
    prepared = prepare_runtime(
        app, package, resident_paths(home), NativeRuntimeBuilder(python, npm=npm, node=node)
    )
    source = tool_source or Path(__file__).resolve().parents[3]
    bootstrap = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from planner.environments.resident.cli import main;raise SystemExit(main())"
    )
    command = [
        str(prepared.runtime / "bin/python"),
        "-I",
        "-B",
        "-c",
        bootstrap,
        str(source),
        "activate",
        "--candidate-app",
        str(app),
        "--home",
        str(home),
        "--requested-sha",
        launch.application_revision,
        "--executor-ssh-host",
        launch.ssh_host,
        "--panels-server-url",
        launch.panels_server_url,
        "--relay-command",
        launch.remote_command,
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if launch.backend_path is not None:
        command += ["--backend-path", launch.backend_path]
    # The command never runs with server/db environment or an alternate HOME.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_seconds * 4,
            env=environment,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResidentError("native activation command did not complete") from exc
    from planner.environments.resident.transport import parse_host_result

    return parse_host_result(completed.stdout, launch.application_revision)


def deploy_local_source(
    source: Path,
    manifest: Path,
    candidate: Path,
    *,
    python: Path,
    home: Path,
    launch: ExecutorLaunchOptions,
    timeout_seconds: float,
    npm: Path | None = None,
    node: Path | None = None,
) -> HostDeploymentResult:
    payload = json.loads(manifest.read_bytes())
    package = package_from_dict(payload)
    if package.application_revision != launch.application_revision:
        raise ResidentError("requested revision does not match manifest")
    build_source_package(source, package, candidate)
    return prepare_and_activate(
        candidate,
        python=python,
        home=home,
        launch=launch,
        timeout_seconds=timeout_seconds,
        npm=npm,
        node=node,
    )


def print_host_result(result: HostDeploymentResult) -> int:
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.disposition == "verified" else 3


def deploy_and_record_remote(
    mac: HostDeploymentAdapter,
    revision: str,
    vps_health_url: str,
    report: Path,
    deployment_id: str,
) -> ResidentDeploymentRecord:
    """Record both observed hosts even when the VPS deploy did not complete."""
    import urllib.error
    import urllib.parse
    import urllib.request

    from planner.environments.resident.contracts import (
        ResidentDeploymentRecord,
        TwoHostDeploymentResult,
    )
    from planner.environments.resident.report import write_deployment_record

    if not vps_health_url.startswith("http://127.0.0.1:"):
        raise ResidentError("VPS health must be read from its local server")
    query = urllib.parse.urlencode({"expected_sha": revision})
    try:
        with urllib.request.urlopen(vps_health_url + "?" + query, timeout=10.0) as response:
            payload = json.loads(response.read(16385))
        observed = payload.get("app_sha")
        ready = payload.get("ready") is True and observed == revision
        vps = HostDeploymentResult(
            "vps",
            revision,
            "verified" if ready else "failed",
            observed if ready else None,
            "Exact VPS revision ready" if ready else "VPS did not prove exact revision readiness",
        )
    except (OSError, ValueError, urllib.error.URLError):
        vps = HostDeploymentResult("vps", revision, "failed", None, "VPS exact health unavailable")
    pending = HostDeploymentResult(
        "mac",
        revision,
        "deferred",
        None,
        "Explicit delivery requested; exact outcome not yet observed",
    )
    write_deployment_record(
        report,
        ResidentDeploymentRecord(
            1, deployment_id, TwoHostDeploymentResult(revision, vps, pending, False)
        ),
    )
    try:
        result = mac.deploy(revision)
    except Exception:
        result = HostDeploymentResult(
            "mac", revision, "failed", None, "Mac deployment did not complete"
        )
    record = ResidentDeploymentRecord(
        1,
        deployment_id,
        TwoHostDeploymentResult(
            revision,
            vps,
            result,
            vps.disposition == "verified" and result.disposition == "verified",
        ),
    )
    write_deployment_record(report, record)
    return record


def write_package_spec(
    source: Path,
    revision: str,
    build_contract: Path,
    architecture: str,
    python_version: str,
    output: Path,
) -> None:
    """Materialize the executor-owned closure from exact committed source paths."""
    from planner.environments.resident.contracts import ExecutorSourceClosure, ResidentPackage
    from planner.environments.resident.packaging import validate_package

    contract = json.loads(build_contract.read_bytes())
    # The handoff is immutable. Later executor contracts require explicit review,
    # rather than silently changing source or dependency scope through a manifest.
    if (
        contract.get("source_revision") != "58698d4e8777b4e64d4a72c244c14e9f20da0177"
        or contract.get("imported_source_revision") != "58698d4e8777b4e64d4a72c244c14e9f20da0177"
        or contract.get("entry_module") != "planner.executor.entrypoint:main"
        or contract.get("package_inputs")
        != [
            "pyproject.toml",
            "requirements.txt",
            "src/planner (entire Python package and declared package-data)",
            "agent_backends/package.json",
            "agent_backends/package-lock.json",
        ]
    ):
        raise ResidentError("executor build contract differs from the reviewed immutable handoff")
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "merge-base",
                "--is-ancestor",
                contract["imported_source_revision"],
                revision,
            ],
            check=True,
            capture_output=True,
        )
        files = subprocess.check_output(
            [
                "git",
                "-C",
                str(source),
                "ls-tree",
                "-r",
                "--name-only",
                revision,
                "--",
                "src/planner",
            ],
            text=True,
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        raise ResidentError(
            "requested release does not contain the pinned executor source"
        ) from exc
    files += [
        "pyproject.toml",
        "requirements.txt",
        "agent_backends/package.json",
        "agent_backends/package-lock.json",
    ]
    from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION

    package = ResidentPackage(
        revision,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        "darwin",
        architecture,
        python_version,
        ExecutorSourceClosure(
            tuple(sorted(files)),
            "planner.executor.entrypoint",
            "requirements.txt",
            ("agent_backends",),
        ),
    )
    validate_package(package)
    output.write_text(json.dumps(asdict(package), sort_keys=True) + "\n")
