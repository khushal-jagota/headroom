from __future__ import annotations

import shlex
from configparser import ConfigParser
from pathlib import Path

from planner.environments.contracts import EnvironmentManifest
from planner.environments.linux import render_linux_specification

ASSET_ROOT = Path(__file__).resolve().parents[2] / "ops" / "panels-environments"


def test_checked_in_linux_assets_define_account_ownership_and_render_boundaries() -> None:
    guidance = (ASSET_ROOT / "README.md").read_text(encoding="utf-8")
    setup = (ASSET_ROOT / "setup-accounts.sh").read_text(encoding="utf-8")

    assert "panels-live" in guidance
    assert "panels-worker" in guidance
    assert "live state, config, and credentials" in guidance
    assert "render/install inputs only" in guidance
    assert "panels environment run" in guidance
    assert "traversable by the service users" in guidance
    assert "/opt/panels/live" in guidance
    assert "/opt/panels/staging" in guidance
    assert "/opt/panels/previews/<preview-id>" in guidance
    assert "useradd --system --home-dir /var/lib/panels/live panels-live" in setup
    assert "useradd --system --home-dir /var/lib/panels/nonproduction panels-worker" in setup
    assert "install -d -m 0755 -o root -g root /etc/panels/environments" in setup
    assert (
        "install -m 0640 -o panels-live -g panels-live "
        "/dev/null /etc/panels/environments/live.env"
    ) in setup
    assert (
        "install -m 0640 -o panels-worker -g panels-worker "
        "/dev/null /etc/panels/environments/staging.env"
    ) in setup
    assert (
        "install -m 0640 -o panels-worker -g panels-worker "
        "/dev/null /etc/panels/environments/previews/<preview-id>.env"
    ) in setup
    assert "chown -R panels-live:panels-live /var/lib/panels/environments/live" in setup
    assert "chown -R panels-worker:panels-worker /var/lib/panels/environments/staging" in setup
    assert "chown -R panels-worker:panels-worker /var/lib/panels/environments/previews" in setup
    assert "systemctl start" not in setup
    assert "systemctl enable" not in setup


def test_checked_in_live_unit_template_uses_live_account_and_write_boundary() -> None:
    unit = _parse_unit(ASSET_ROOT / "panels-live.service")

    assert unit["Service"]["User"] == "panels-live"
    assert unit["Service"]["WorkingDirectory"] == "/opt/panels/live"
    assert unit["Service"]["EnvironmentFile"] == "/etc/panels/environments/live.env"
    assert unit["Service"]["ExecStart"] == (
        "/usr/bin/env panels environment run --kind live "
        "--environment-root /var/lib/panels/environments "
        "--repository-root /opt/panels/live"
    )
    assert _working_directory_repository_and_writable_path_agree(unit, "/opt/panels/live")
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Service"]["ProtectSystem"] == "strict"
    assert unit["Service"]["ProtectHome"] == "yes"
    assert unit["Service"]["ReadWritePaths"] == (
        "/var/lib/panels/environments/live /opt/panels/live"
    )


def test_checked_in_staging_unit_invokes_ordinary_cli_without_env_argv_expansion() -> None:
    unit = _parse_unit(ASSET_ROOT / "panels-staging.service")

    assert unit["Service"]["User"] == "panels-worker"
    assert unit["Service"]["WorkingDirectory"] == "/opt/panels/staging"
    assert unit["Service"]["EnvironmentFile"] == "/etc/panels/environments/staging.env"
    assert unit["Service"]["ExecStart"] == (
        "/usr/bin/env panels environment run --kind staging "
        "--environment-root /var/lib/panels/environments "
        "--repository-root /opt/panels/staging"
    )
    assert _working_directory_repository_and_writable_path_agree(unit, "/opt/panels/staging")
    assert _exec_start_argv(unit) == [
        "/usr/bin/env",
        "panels",
        "environment",
        "run",
        "--kind",
        "staging",
        "--environment-root",
        "/var/lib/panels/environments",
        "--repository-root",
        "/opt/panels/staging",
    ]
    assert "${" not in unit["Service"]["ExecStart"]
    assert "PANELS_ENVIRONMENT_INSTANCE_ARGS" not in unit["Service"]["ExecStart"]
    assert "PANELS_ENVIRONMENT_KIND" not in unit["Service"]["ExecStart"]
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Service"]["ProtectSystem"] == "strict"
    assert unit["Service"]["ProtectHome"] == "yes"
    assert unit["Service"]["ReadWritePaths"] == (
        "/var/lib/panels/environments /opt/panels/staging"
    )


def test_checked_in_preview_template_uses_instance_specifier_as_preview_id() -> None:
    unit = _parse_unit(ASSET_ROOT / "panels-preview@.service")

    assert unit["Service"]["User"] == "panels-worker"
    assert unit["Service"]["WorkingDirectory"] == "/opt/panels/previews/%i"
    assert unit["Service"]["EnvironmentFile"] == "/etc/panels/environments/previews/%i.env"
    assert unit["Service"]["ExecStart"] == (
        "/usr/bin/env panels environment run --kind preview --instance-id %i "
        "--environment-root /var/lib/panels/environments "
        "--repository-root /opt/panels/previews/%i"
    )
    assert _working_directory_repository_and_writable_path_agree(
        unit,
        "/opt/panels/previews/%i",
    )
    assert _exec_start_argv(unit) == [
        "/usr/bin/env",
        "panels",
        "environment",
        "run",
        "--kind",
        "preview",
        "--instance-id",
        "%i",
        "--environment-root",
        "/var/lib/panels/environments",
        "--repository-root",
        "/opt/panels/previews/%i",
    ]
    assert "${" not in unit["Service"]["ExecStart"]
    assert "PANELS_ENVIRONMENT_INSTANCE_ARGS" not in unit["Service"]["ExecStart"]
    assert "PANELS_ENVIRONMENT_KIND" not in unit["Service"]["ExecStart"]
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Service"]["ProtectSystem"] == "strict"
    assert unit["Service"]["ProtectHome"] == "yes"
    assert unit["Service"]["ReadWritePaths"] == (
        "/var/lib/panels/environments /opt/panels/previews/%i"
    )


def test_checked_in_tmpfiles_template_keeps_live_and_worker_paths_separate() -> None:
    lines = _meaningful_lines(ASSET_ROOT / "panels-environments.tmpfiles")

    assert "d /var/lib/panels/environments/live 0750 panels-live panels-live -" in lines
    assert "d /var/lib/panels/environments/staging 0750 panels-worker panels-worker -" in lines
    assert "d /var/lib/panels/environments/previews 0750 panels-worker panels-worker -" in lines
    assert "d /etc/panels/environments 0755 root root -" in lines
    assert "z /etc/panels/environments/live.env 0640 panels-live panels-live -" in lines
    assert "z /etc/panels/environments/staging.env 0640 panels-worker panels-worker -" in lines
    assert "d /etc/panels/environments/previews 0750 panels-worker panels-worker -" in lines
    assert "z /etc/panels/environments/previews/*.env 0640 panels-worker panels-worker -" in lines


def test_checked_in_environment_examples_are_names_only() -> None:
    for name in ("live.env.example", "staging.env.example", "preview.env.example"):
        lines = _meaningful_lines(ASSET_ROOT / name)
        assert lines == [
            "ANTHROPIC_API_KEY=",
            "OPENAI_API_KEY=",
            "GOOGLE_API_KEY=",
        ]


def test_linux_renderer_exposes_live_account_unit_and_no_local_install(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    manifest = _manifest(
        kind="live",
        instance_id="live",
        environment_root=tmp_path / "envs",
        port=8767,
        credentials_env_file=tmp_path / "ops" / "live.env",
        repository_roots=(repository_root,),
        expected_linux_account="panels-live",
        fixture_version=None,
    )

    rendered = render_linux_specification(manifest)

    assert rendered.required_account == "panels-live"
    assert rendered.unit_name == "panels-live.service"
    assert "User=panels-live" in rendered.unit_text
    assert "WorkingDirectory=" + str(repository_root.resolve()) in rendered.unit_text
    assert "EnvironmentFile=" + str((tmp_path / "ops" / "live.env").resolve()) in rendered.unit_text
    assert "ExecStart=/usr/bin/env panels environment run --kind live" in rendered.unit_text
    assert "--environment-root " + str((tmp_path / "envs").resolve()) in rendered.unit_text
    assert "--repository-root " + str(repository_root.resolve()) in rendered.unit_text
    assert "Restart=on-failure" in rendered.unit_text
    assert rendered.credential_file_reference == (tmp_path / "ops" / "live.env").resolve()
    assert rendered.repository_path_policy == (
        "repository roots are working directories only; the service writes runtime "
        "state under the prepared instance root, while the VPS service may write "
        "inside its own repository checkout"
    )
    assert rendered.local_effects == (
        "render-only: no local users, permissions, units, networking, or services are installed"
    )
    assert rendered.vps_enforcement_verified is False
    assert "ProtectSystem=strict" in rendered.unit_text
    assert "ProtectHome=yes" in rendered.unit_text
    assert (
        "ReadWritePaths="
        + str(manifest.instance_root)
        + " "
        + str(repository_root.resolve())
    ) in rendered.unit_text
    assert rendered.strict_writable_paths == (
        manifest.instance_root,
        manifest.db_path.parent,
        manifest.managed_files_root,
        manifest.hermes_home,
        manifest.logs_dir,
        manifest.dispatcher_lock_path.parent,
        manifest.server_control_socket_path.parent,
        repository_root.resolve(),
    )
    assert "d " + str(manifest.instance_root) + " 0750 panels-live panels-live -" in (
        rendered.tmpfiles_text
    )
    assert "Z " + str(manifest.instance_root) + " 0750 panels-live panels-live -" in (
        rendered.ownership_text
    )


def test_linux_renderer_exposes_nonproduction_worker_account_and_preview_identity(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root(tmp_path)
    manifest = _manifest(
        kind="preview",
        instance_id="feature-123",
        environment_root=tmp_path / "envs",
        port=9012,
        credentials_env_file=tmp_path / "ops" / "preview.env",
        repository_roots=(repository_root,),
        expected_linux_account="panels-worker",
        fixture_version="fake-fixture-v1",
    )

    rendered = render_linux_specification(manifest)

    assert rendered.required_account == "panels-worker"
    assert rendered.unit_name == "panels-preview-feature-123.service"
    assert "User=panels-worker" in rendered.unit_text
    assert "ExecStart=/usr/bin/env panels environment run --kind preview" in rendered.unit_text
    assert "--instance-id feature-123" in rendered.unit_text
    assert "--repository-root " + str(repository_root.resolve()) in rendered.unit_text
    assert (
        "ReadWritePaths="
        + str(manifest.instance_root)
        + " "
        + str(repository_root.resolve())
    ) in rendered.unit_text
    assert "--port 9012" not in rendered.unit_text
    assert rendered.unit_text.count("panels environment run") == 1
    assert str(manifest.hermes_home) in rendered.tmpfiles_text
    assert str(manifest.logs_dir) in rendered.tmpfiles_text
    assert "panels-live" not in rendered.tmpfiles_text


def _manifest(
    *,
    kind: str,
    instance_id: str,
    environment_root: Path,
    port: int,
    credentials_env_file: Path | None,
    repository_roots: tuple[Path, ...],
    expected_linux_account: str,
    fixture_version: str | None,
) -> EnvironmentManifest:
    environment_root = environment_root.resolve()
    instance_root = (
        environment_root / "previews" / instance_id
        if kind == "preview"
        else environment_root / instance_id
    )
    return EnvironmentManifest(
        kind=kind,  # type: ignore[arg-type]
        instance_id=instance_id,
        environment_root=environment_root,
        instance_root=instance_root,
        db_path=instance_root / "data" / "planner.db",
        managed_files_root=instance_root / "data" / "files",
        hermes_home=instance_root / "hermes-home",
        logs_dir=instance_root / "logs",
        dispatcher_lock_path=instance_root / "run" / "dispatcher.lock",
        server_control_socket_path=instance_root / "run" / "server-control.sock",
        port=port,
        credentials_env_file=credentials_env_file.resolve()
        if credentials_env_file is not None
        else None,
        expected_linux_account=expected_linux_account,  # type: ignore[arg-type]
        fixture_version=fixture_version,
        prepared_at=1_800_000_000,
        repository_roots=tuple(root.resolve() for root in repository_roots),
    )


def _repository_root(tmp_path: Path) -> Path:
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root


def _parse_unit(path: Path) -> ConfigParser:
    parser = ConfigParser(interpolation=None, strict=False)
    with path.open(encoding="utf-8") as unit_file:
        parser.read_file(unit_file)
    return parser


def _exec_start_argv(unit: ConfigParser) -> list[str]:
    return shlex.split(unit["Service"]["ExecStart"])


def _working_directory_repository_and_writable_path_agree(
    unit: ConfigParser,
    repository_root: str,
) -> bool:
    return (
        unit["Service"]["WorkingDirectory"] == repository_root
        and _exec_start_argv(unit)[-1] == repository_root
        and repository_root in unit["Service"]["ReadWritePaths"].split()
    )


def _meaningful_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
