"""Install an isolated Linux executor and its systemd user service."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from planner.executor.configuration import validate_executor_launch
from planner.executor.credential_file import credential_path, read_credential, write_credential
from planner.platform_runtime.targets import LINUX_EXECUTOR
from planner.worker_supervision.config import STOP_SECONDS


@dataclass(frozen=True)
class LinuxExecutorInstallation:
    installation: Path
    data: Path
    executor_id: str
    server_url: str
    revision: str
    service_name: str = "panels-executor.service"


def _quoted(value: str) -> str:
    if any(character in value for character in "\n\r\0"):
        raise ValueError("service values must not contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_service(configuration: LinuxExecutorInstallation, runtime: Path) -> str:
    validate_executor_launch(
        configuration.executor_id, configuration.revision, configuration.server_url
    )
    if re.fullmatch(r"[A-Za-z0-9_-]+\.service", configuration.service_name) is None:
        raise ValueError("invalid executor service name")
    for path in (configuration.installation, configuration.data, runtime):
        if not path.is_absolute():
            raise ValueError("executor installation paths must be absolute")
    command = [
        str(runtime / ".venv/bin/python"),
        "-I",
        "-B",
        "-m",
        "planner.executor.entrypoint",
        "executor",
        "run",
        "--executor-id",
        configuration.executor_id,
        "--application-revision",
        configuration.revision,
        "--panels-server-url",
        configuration.server_url,
    ]
    return "\n".join(
        [
            "[Unit]",
            "Description=Panels extra machine",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "UMask=0077",
            "Environment=" + _quoted("PLAN_EXECUTOR_ROOT=" + str(configuration.data)),
            "Environment=" + _quoted("PLAN_APP_ROOT=" + str(runtime)),
            "Environment=" + _quoted("PLAN_APP_SHA=" + configuration.revision),
            "Environment="
            + _quoted(
                "PATH="
                + os.pathsep.join(
                    [
                        str(runtime / ".venv/bin"),
                        str(runtime / "agent_backends/node_modules/.bin"),
                        str(Path.home() / ".local/bin"),
                        "/usr/local/bin",
                        "/usr/bin",
                        "/bin",
                    ]
                )
            ),
            "ExecStart=" + " ".join(_quoted(argument.replace("$", "$$")) for argument in command),
            "Restart=on-failure",
            "RestartSec=10",
            "KillMode=mixed",
            f"TimeoutStopSec={int(STOP_SECONDS) + 20}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def install(
    configuration: LinuxExecutorInstallation,
    *,
    archive: Path,
    archive_sha256: str,
    archive_size: int,
    credential_file: Path,
    unit_directory: Path,
) -> Path:
    """Verify bytes and host before any credential or service file changes."""
    from planner.environments.app import validate_app_manifest
    from planner.environments.linux_executor_runtime import extract_runtime

    installation = configuration.installation.resolve()
    data = configuration.data.resolve()
    if installation == data or installation in data.parents or data in installation.parents:
        raise ValueError("executor data and installation must be separate folders")
    credential = read_credential(credential_file)
    if credential is None:
        raise ValueError("machine credential file is missing")
    # Validate service inputs before an install can mutate the destination.
    render_service(configuration, installation / "releases/validation")
    runtime = extract_runtime(
        archive,
        installation / "releases",
        expected_sha256=archive_sha256,
        expected_size=archive_size,
        expected_revision=configuration.revision,
    )
    manifest = validate_app_manifest(
        runtime / "manifest.json",
        require_runtime=True,
        verify_artifact_digest=False,
        expected_target=LINUX_EXECUTOR,
    )
    if manifest.runtime_contract is None:
        raise ValueError("Linux executor requires its runtime contract")
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.chmod(0o700)
    write_credential(credential_path(data), credential)
    unit_directory.mkdir(parents=True, exist_ok=True)
    unit = unit_directory / configuration.service_name
    temporary = unit.with_suffix(".service.tmp")
    temporary.write_text(render_service(configuration, runtime))
    temporary.replace(unit)
    return unit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--executor-id", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--archive-size", type=int, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--service-name", default="panels-executor.service")
    args = parser.parse_args()
    configuration = LinuxExecutorInstallation(
        args.installation,
        args.data,
        args.executor_id,
        args.server_url,
        args.revision,
        args.service_name,
    )
    try:
        unit = install(
            configuration,
            archive=args.archive,
            archive_sha256=args.archive_sha256,
            archive_size=args.archive_size,
            credential_file=args.credential_file,
            unit_directory=Path.home() / ".config/systemd/user",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=30)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", unit.name], check=True, timeout=60
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(json.dumps({"service": unit.name, "data": str(configuration.data)}))


if __name__ == "__main__":
    main()
