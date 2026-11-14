"""Install an isolated Linux executor and its systemd user service."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
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


def _quoted(value: str) -> str:
    if any(character in value for character in "\n\r\0"):
        raise ValueError("service values must not contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_service(configuration: LinuxExecutorInstallation, runtime: Path) -> str:
    validate_executor_launch(
        configuration.executor_id, configuration.revision, configuration.server_url
    )
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
    from planner.platform_runtime.linux.runtime import LinuxRuntime

    installation = configuration.installation.resolve()
    data = configuration.data.resolve()
    if installation == data or installation in data.parents or data in installation.parents:
        raise ValueError("executor data and installation must be separate folders")
    if (
        (data.is_dir() and any(data.iterdir()))
        or ((installation / "releases").is_dir() and any((installation / "releases").iterdir()))
        or (unit_directory / "panels-executor.service").exists()
    ):
        raise ValueError(
            "This executor is already installed. Use its update path instead of reinstalling."
        )
    credential = read_credential(credential_file)
    if credential is None:
        raise ValueError("machine credential file is missing")
    # Validate service inputs before an install can mutate the destination.
    render_service(configuration, installation / "releases/validation")
    releases = installation / "releases"
    new_directories: set[Path] = set()
    for root in (releases, data, unit_directory):
        for directory in (root, *root.parents):
            if directory.exists():
                break
            new_directories.add(directory)
    data_mode = stat.S_IMODE(data.stat().st_mode) if data.exists() else None
    runtime: Path | None = None
    created_files: list[Path] = []
    try:
        runtime = extract_runtime(
            archive,
            releases,
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
        with tempfile.TemporaryDirectory(prefix="panels-linux-install-") as state:
            LinuxRuntime().verify_host(manifest.runtime_contract, state=Path(state))
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        data.chmod(0o700)
        created_files.append(credential_path(data))
        write_credential(credential_path(data), credential)
        unit_directory.mkdir(parents=True, exist_ok=True)
        unit = unit_directory / "panels-executor.service"
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=".panels-executor-",
            suffix=".service",
            dir=unit_directory,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            created_files.append(temporary_path)
            temporary.write(render_service(configuration, runtime))
        created_files.append(unit)
        temporary_path.replace(unit)
        return unit
    except BaseException as error:
        for path in reversed(created_files):
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(f"Could not remove installer file {path}: {cleanup_error}")
        if runtime is not None and runtime.parent == releases:
            try:
                shutil.rmtree(runtime)
            except OSError as cleanup_error:
                error.add_note(f"Could not remove installer runtime {runtime}: {cleanup_error}")
        if data_mode is not None:
            try:
                data.chmod(data_mode)
            except OSError as cleanup_error:
                error.add_note(f"Could not restore data folder permissions: {cleanup_error}")
        for directory in sorted(new_directories, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                # A foreign file makes this folder nonempty. Preserve it.
                pass
        raise


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
    args = parser.parse_args()
    configuration = LinuxExecutorInstallation(
        args.installation,
        args.data,
        args.executor_id,
        args.server_url,
        args.revision,
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
