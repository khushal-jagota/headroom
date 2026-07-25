"""Render Linux runtime specifications from prepared environment manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError

DEFAULT_LINUX_ENVIRONMENT_MANAGER_ROOT = Path("/opt/panels/environment-manager")
DEFAULT_LIVE_LAUNCHER = Path("/opt/panels/current/app/bin/panels-launcher")


@dataclass(frozen=True)
class LinuxEnvironmentSpecification:
    required_account: str
    unit_name: str
    unit_text: str
    tmpfiles_text: str
    ownership_text: str
    strict_writable_paths: tuple[Path, ...]
    repository_path_policy: str
    credential_file_reference: Path | None
    local_effects: str
    vps_enforcement_verified: bool


def render_linux_specification(
    manifest: EnvironmentManifest,
    *,
    environment_manager_root: Path | None = None,
) -> LinuxEnvironmentSpecification:
    """Return the Linux account/unit/tmpfiles intent without changing local state."""
    if manifest.prepared_at is None:
        raise EnvironmentValidationError("Linux specification requires a prepared manifest")
    if manifest.kind != "live" and not manifest.repository_roots:
        raise EnvironmentValidationError("Linux specification requires a repository root")

    unit_name = _unit_name(manifest)
    writable_paths: list[Path] = [
        manifest.instance_root,
        manifest.db_path.parent,
        manifest.managed_files_root,
        manifest.hermes_home,
        manifest.logs_dir,
        manifest.dispatcher_lock_path.parent,
        manifest.server_control_socket_path.parent,
    ]
    if manifest.kind != "live":
        writable_paths.extend(manifest.repository_roots)
    tmpfiles_text = "\n".join(
        f"d {path} 0750 {manifest.expected_linux_account} {manifest.expected_linux_account} -"
        for path in writable_paths
    )
    ownership_text = "\n".join(
        f"Z {path} 0750 {manifest.expected_linux_account} {manifest.expected_linux_account} -"
        for path in writable_paths
    )

    return LinuxEnvironmentSpecification(
        required_account=manifest.expected_linux_account,
        unit_name=unit_name,
        unit_text=_unit_text(
            manifest,
            unit_name,
            environment_manager_root=(
                environment_manager_root.resolve()
                if environment_manager_root is not None
                else DEFAULT_LINUX_ENVIRONMENT_MANAGER_ROOT
            ),
        ),
        tmpfiles_text=tmpfiles_text,
        ownership_text=ownership_text,
        strict_writable_paths=tuple(writable_paths),
        repository_path_policy=(
            "live runs from the operator-owned current app launcher; persistent state is "
            "external and staging may use its working checkout"
        ),
        credential_file_reference=manifest.credentials_env_file,
        local_effects=(
            "render-only: no local users, permissions, units, networking, or services are installed"
        ),
        vps_enforcement_verified=False,
    )


def _unit_name(manifest: EnvironmentManifest) -> str:
    if manifest.kind == "live":
        return "panels-live.service"
    if manifest.kind == "staging":
        return "panels-staging.service"
    raise EnvironmentValidationError(f"unknown environment kind: {manifest.kind}")


def _unit_text(
    manifest: EnvironmentManifest,
    unit_name: str,
    *,
    environment_manager_root: Path,
) -> str:
    working_directory = str(manifest.repository_roots[0])
    read_write_paths = " ".join(
        (str(manifest.instance_root), *(str(root) for root in manifest.repository_roots))
    )
    if manifest.kind == "live":
        command = [str(DEFAULT_LIVE_LAUNCHER), "serve"]
        working_directory = str(manifest.instance_root)
        read_write_paths = " ".join(
            (
                str(manifest.db_path.parent),
                str(manifest.managed_files_root),
                str(manifest.hermes_home),
                str(manifest.logs_dir),
                str(manifest.dispatcher_lock_path.parent),
                str(manifest.server_control_socket_path.parent),
            )
        )
    else:
        command = [
            str(environment_manager_root / ".venv" / "bin" / "python"),
            "-m",
            "planner",
            "environment",
            "run",
            "--kind",
            manifest.kind,
        ]
        command.extend(
            [
                "--environment-root",
                str(manifest.environment_root),
                "--repository-root",
                str(manifest.repository_roots[0]),
            ]
        )
    credential_line = (
        f"EnvironmentFile={manifest.credentials_env_file}"
        if manifest.credentials_env_file is not None
        else "# EnvironmentFile not configured"
    )
    return "\n".join(
        [
            "[Unit]",
            f"Description=Panels environment {manifest.kind} ({manifest.instance_id})",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={manifest.expected_linux_account}",
            f"WorkingDirectory={working_directory}",
            credential_line,
            f"ExecStart={' '.join(command)}",
            "Restart=on-failure",
            "RestartSec=5",
            f"StateDirectory=panels/{manifest.instance_id}",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            f"ReadWritePaths={read_write_paths}",
            "NoNewPrivileges=yes",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
            f"# Rendered unit name: {unit_name}",
            f"# Instance root: {manifest.instance_root}",
        ]
    )
