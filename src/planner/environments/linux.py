"""Render Linux runtime specifications from prepared environment manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError


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
) -> LinuxEnvironmentSpecification:
    """Return the Linux account/unit/tmpfiles intent without changing local state."""
    if manifest.prepared_at is None:
        raise EnvironmentValidationError("Linux specification requires a prepared manifest")
    if not manifest.repository_roots:
        raise EnvironmentValidationError("Linux specification requires a repository root")

    unit_name = _unit_name(manifest)
    writable_paths = (
        manifest.instance_root,
        manifest.db_path.parent,
        manifest.managed_files_root,
        manifest.hermes_home,
        manifest.logs_dir,
        manifest.dispatcher_lock_path.parent,
        manifest.server_control_socket_path.parent,
        *manifest.repository_roots,
    )
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
        unit_text=_unit_text(manifest, unit_name),
        tmpfiles_text=tmpfiles_text,
        ownership_text=ownership_text,
        strict_writable_paths=writable_paths,
        repository_path_policy=(
            "repository roots are working directories only; the service writes runtime "
            "state under the prepared instance root, while the VPS service may write "
            "inside its own repository checkout"
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
    return f"panels-preview-{manifest.instance_id}.service"


def _unit_text(manifest: EnvironmentManifest, unit_name: str) -> str:
    read_write_paths = " ".join(
        (str(manifest.instance_root), *(str(root) for root in manifest.repository_roots))
    )
    command = [
        "/usr/bin/env",
        "panels",
        "environment",
        "run",
        "--kind",
        manifest.kind,
    ]
    if manifest.kind == "preview":
        command.extend(["--instance-id", manifest.instance_id])
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
            f"WorkingDirectory={manifest.repository_roots[0]}",
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
