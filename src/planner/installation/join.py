"""Install a new executor with an owner-issued credential; never replace an installation."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import subprocess
import time
from pathlib import Path
from uuid import UUID

from planner.environments.mac_app.build import build_app
from planner.environments.mac_app.configuration import validate_identity
from planner.environments.mac_app.verify import verify_app
from planner.environments.mac_executor import ServiceConfiguration, render_launch_agent
from planner.environments.mac_runtime import extract_runtime
from planner.executor.configuration import validate_server_url
from planner.executor.credential_file import credential_path, write_credential


def check_mac_prerequisites(signing_identity: str | None) -> str:
    """Refuse unavailable native tools and signing authority before installation writes."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Mac join needs an Apple Silicon Mac")
    if int(platform.mac_ver()[0].split(".")[0]) < 15:
        raise ValueError("Mac join needs macOS 15 or newer")
    if signing_identity is None or re.fullmatch(r"[0-9A-Fa-f]{40}", signing_identity) is None:
        raise ValueError("Mac join needs --signing-identity with an Apple Development fingerprint")
    identities = subprocess.run(
        ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout
    if not any(
        signing_identity.lower() in line.lower() and "Apple Development:" in line
        for line in identities.splitlines()
    ):
        raise ValueError("Install the selected Apple Development certificate and private key")
    subprocess.run(
        ["/usr/bin/xcrun", "--find", "clang"], check=True, capture_output=True, timeout=10
    )
    subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return signing_identity


def wait_for_lease(python: Path, root: Path, revision: str, *, timeout: float = 45) -> None:
    """Use the new private socket to prove that the home accepted this service's lease."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment["PLAN_EXECUTOR_ROOT"] = str(root)
    started_ms = time.time_ns() // 1_000_000
    deadline = time.monotonic() + timeout
    detail = "The executor did not report an accepted lease"
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-B",
                    "-c",
                    "from planner.executor.cli import health; health.main(args=[])",
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                status = json.loads(result.stdout)
                if (
                    status.get("identity", {}).get("application_revision") == revision
                    and isinstance(status.get("lease_accepted_at_mac_ms"), int)
                    and status["lease_accepted_at_mac_ms"] >= started_ms
                    and status.get("refusal") is None
                ):
                    return
                detail = str(status.get("last_lease_failure") or status.get("refusal") or detail)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.5)
    raise ValueError(f"Join did not complete: {detail}. Inspect {root / 'logs'}")


def provider_environment(root: Path, isolated_providers: bool) -> dict[str, str]:
    """Keep disposable executor credentials and skill links inside its selected root."""
    if not isolated_providers:
        return {}
    environment = {}
    for variable, name in (
        ("CODEX_HOME", "codex"),
        ("CLAUDE_CONFIG_DIR", "claude"),
        ("PLAN_HERMES_HOME", "hermes"),
    ):
        path = root / "providers" / name
        path.mkdir(parents=True, mode=0o700)
        environment[variable] = str(path)
    return environment


def install_executor(
    *,
    archive: Path,
    archive_sha256: str,
    archive_size: int,
    revision: str,
    home_url: str,
    machine_id: str,
    credential: str,
    root: Path,
    signing_identity: str | None = None,
    label: str = "com.panels.executor",
    isolated_providers: bool = False,
) -> None:
    """Create one independent Mac installation, then start and check only its service."""
    home_url = validate_server_url(home_url)
    UUID(machine_id)
    if platform.system() != "Linux":
        validate_identity(label)
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
        raise ValueError("The private machine credential is invalid")
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Join needs the full release revision")
    root = root.expanduser().absolute()
    if root.exists() or root.is_symlink():
        raise ValueError(f"The installation root already exists: {root}")
    if platform.system() == "Linux":
        from planner.installation.join_linux import install_linux_executor

        install_linux_executor(
            archive=archive,
            archive_sha256=archive_sha256,
            archive_size=archive_size,
            revision=revision,
            home_url=home_url,
            machine_id=machine_id,
            credential=credential,
            root=root.resolve(),
            label=label,
            isolated_providers=isolated_providers,
        )
        return
    identity = check_mac_prerequisites(signing_identity)
    plist_path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    if plist_path.exists() or plist_path.is_symlink():
        raise ValueError(f"The executor service already exists: {plist_path}")
    service = f"gui/{os.getuid()}/{label}"
    loaded = subprocess.run(["/bin/launchctl", "print", service], capture_output=True, timeout=10)
    if loaded.returncode == 0:
        raise ValueError(f"The executor service is already loaded: {label}")
    # Validate render inputs before the first installation write.
    root = root.resolve()
    app = root / "Panels.app"
    bundle_id = label + ".app"
    configuration = ServiceConfiguration(
        home=Path.home(),
        executor_id=machine_id,
        application_revision=revision,
        panels_server_url=home_url,
        executor_root=root / "data",
        source_path=root / "runtime",
        venv_path=root / "runtime/python",
        app_path=app,
        job_label=label,
        bundle_id=bundle_id,
    )
    render_launch_agent(configuration)
    root.mkdir(parents=True, mode=0o700)
    runtime = extract_runtime(
        archive,
        root / "releases",
        expected_sha256=archive_sha256,
        expected_size=archive_size,
        expected_revision=revision,
    )
    # Keep the permanent supervisor runtime at its extracted, versioned path.
    configuration = ServiceConfiguration(
        home=Path.home(),
        executor_id=machine_id,
        application_revision=revision,
        panels_server_url=home_url,
        executor_root=root / "data",
        source_path=runtime,
        venv_path=runtime / "python",
        app_path=app,
        job_label=label,
        bundle_id=bundle_id,
    )
    python = runtime / "python/bin/python3"
    build_app(Path.home(), app, revision, python=python, bundle_id=bundle_id)
    subprocess.run(
        ["/usr/bin/codesign", "--sign", identity, "--timestamp=none", str(app)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    verify_app(app, identity, python=python, bundle_id=bundle_id)
    data = root / "data"
    (data / "logs").mkdir(parents=True, mode=0o700)
    write_credential(credential_path(data), credential)
    service_definition = plistlib.loads(render_launch_agent(configuration))
    service_environment = service_definition["EnvironmentVariables"]
    service_environment.update(provider_environment(root, isolated_providers))
    service_environment["PLAN_APP_ROOT"] = str(runtime)
    service_environment["PLAN_APP_SHA"] = revision
    service_environment["PATH"] = (
        str(runtime / "agent_backends/node_modules/.bin") + os.pathsep + service_environment["PATH"]
    )
    plist = plistlib.dumps(service_definition)
    (root / "agents").mkdir(mode=0o700)
    (root / "agents" / f"{label}.plist").write_bytes(plist)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("xb") as stream:
        stream.write(plist)
    plist_path.chmod(0o600)
    subprocess.run(
        ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    wait_for_lease(python, data, revision)
    print("Machine joined. The home accepted its credential and lease.")


def main() -> None:
    """Receive bootstrap inputs through stdin, so the secret never enters argv."""
    import sys

    values = json.load(sys.stdin)
    if not isinstance(values, dict):
        raise ValueError("Join inputs must be an object")
    values["archive"] = Path(values["archive"])
    values["root"] = Path(values["root"])
    try:
        install_executor(**values)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        detail = (
            error.stderr
            if isinstance(error, subprocess.CalledProcessError) and error.stderr
            else str(error)
        )
        raise SystemExit(f"Join refused: {detail}") from None


if __name__ == "__main__":
    main()
