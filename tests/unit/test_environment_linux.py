from __future__ import annotations

import subprocess
from pathlib import Path

from planner.environments.contracts import EnvironmentManifest
from planner.environments.linux import (
    DEFAULT_LINUX_ENVIRONMENT_MANAGER_ROOT,
    render_linux_specification,
)
from planner.environments.logic.registry import resolve_environment_instance

ASSET_ROOT = Path(__file__).resolve().parents[2] / "ops" / "panels-environments"


def test_live_linux_render_keeps_fixed_external_service_contract(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "live")
    rendered = render_linux_specification(
        manifest, environment_manager_root=Path("/opt/panels/environment-manager")
    )
    assert rendered.required_account == "panels-live"
    assert rendered.unit_name == "panels-live.service"
    assert (
        "/opt/panels/environment-manager/.venv/bin/python -m planner environment run --kind live"
        in rendered.unit_text
    )
    assert str(manifest.repository_roots[0]) in rendered.unit_text


def test_staging_linux_render_uses_on_demand_dynamic_run_command(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, "staging")
    rendered = render_linux_specification(
        manifest, environment_manager_root=Path("/opt/panels/environment-manager")
    )
    assert rendered.required_account == "panels-worker"
    assert rendered.unit_name == "panels-staging.service"
    assert (
        "/opt/panels/environment-manager/.venv/bin/python -m planner environment run --kind staging"
        in rendered.unit_text
    )
    assert "--port" not in rendered.unit_text


def test_checked_in_linux_assets_have_no_preview_service_or_account_surface() -> None:
    assert not (ASSET_ROOT / "panels-preview@.service").exists()
    assert not (ASSET_ROOT / "preview.env.example").exists()
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in ASSET_ROOT.iterdir() if path.is_file()
    )
    assert "preview" not in combined.lower()


def test_checked_in_linux_units_use_the_shared_pinned_manager_and_private_accounts() -> None:
    live = (ASSET_ROOT / "panels-live.service").read_text(encoding="utf-8")
    staging = (ASSET_ROOT / "panels-staging.service").read_text(encoding="utf-8")
    manager_launcher = (
        "/opt/panels/environment-manager/.venv/bin/python -m planner environment run"
    )

    assert "User=panels-live" in live
    assert "User=panels-worker" in staging
    assert manager_launcher in live
    assert manager_launcher in staging
    assert "--repository-root /opt/panels/live" in live
    assert "--repository-root /opt/panels/staging" in staging
    assert DEFAULT_LINUX_ENVIRONMENT_MANAGER_ROOT == Path(
        "/opt/panels/environment-manager"
    )

    setup = (ASSET_ROOT / "setup-accounts.sh").read_text(encoding="utf-8")
    assert (
        "install -d -m 0755 -o root -g root /opt/panels/environment-manager" in setup
    )


def test_backup_inputs_resolve_the_deployed_revision_from_the_live_checkout() -> None:
    service = (ASSET_ROOT / "panels-db-backup.service").read_text(encoding="utf-8")
    pre_deploy = (ASSET_ROOT / "pre-deploy-backup.sh").read_text(encoding="utf-8")
    environment = (ASSET_ROOT / "backup.env.example").read_text(encoding="utf-8")

    assert "PANELS_LIVE_REPOSITORY=" in environment
    assert "PANELS_BACKUP_DEPLOYED_REVISION" not in environment
    assert 'git -C "$PANELS_LIVE_REPOSITORY" rev-parse HEAD' in service
    assert 'git -C "$PANELS_LIVE_REPOSITORY" rev-parse HEAD' in pre_deploy
    assert '--deployed-revision "$revision"' in service
    assert '--deployed-revision "$revision"' in pre_deploy


def _manifest(tmp_path: Path, kind: str) -> EnvironmentManifest:
    repository = tmp_path / f"{kind}-repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    instance = resolve_environment_instance(
        kind=kind,  # type: ignore[arg-type]
        environment_root=Path("/tmp") / f"pe-{tmp_path.parent.name}-{tmp_path.name}",
        allowed_repository_roots=(repository,),
        requested_repository_roots=(repository,),
        prepared=True,
    )
    return EnvironmentManifest(
        kind=instance.kind,
        instance_id=instance.instance_id,
        environment_root=instance.environment_root,
        instance_root=instance.instance_root,
        db_path=instance.db_path,
        managed_files_root=instance.managed_files_root,
        hermes_home=instance.hermes_home,
        runtime_user_home=instance.runtime_user_home,
        logs_dir=instance.logs_dir,
        dispatcher_lock_path=instance.dispatcher_lock_path,
        server_control_socket_path=instance.server_control_socket_path,
        port_policy=instance.port_policy,
        credentials_env_file=None,
        expected_linux_account=instance.expected_linux_account,
        fixture_version=instance.fixture_version,
        prepared_at=123,
        repository_roots=instance.allowed_repository_roots,
    )
