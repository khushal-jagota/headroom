from __future__ import annotations

from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / "ops" / "panels-environments"
WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def test_linux_live_service_uses_current_launcher_and_external_state() -> None:
    service = (ASSET_ROOT / "panels-live.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/panels/current/bin/panels-launcher serve" in service
    assert "/opt/panels/live" not in service
    assert "ReadWritePaths=/var/lib/panels/environments/live" in service


def test_macos_input_is_supervised_and_uses_current_launcher() -> None:
    plist = (ASSET_ROOT / "panels-launchd.plist").read_text(encoding="utf-8")
    assert "com.panels.live" in plist
    assert "/opt/panels/current/bin/panels-launcher" in plist
    assert "RunAtLoad" in plist and "KeepAlive" in plist


def test_github_deployment_checks_out_and_proves_exact_sha_before_deploy() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert "ref: ${{ github.sha }}" in workflow
    assert 'git rev-parse HEAD)" = "${{ github.sha }}' in workflow
    assert "./verify" in workflow
    assert "PANELS_RELEASE_ROOT/${{ github.sha }}" in workflow
    assert "release-build" in workflow
    assert "actions/checkout@" in workflow and "@v4" not in workflow


def test_service_control_uses_real_manager_verbs_and_backup_uses_shared_identity_cli() -> None:
    control = (ASSET_ROOT / "service-control.sh").read_text(encoding="utf-8")
    assert "launchctl kickstart" in control
    assert "launchctl kill" in control
    service = (ASSET_ROOT / "panels-db-backup.service").read_text(encoding="utf-8")
    pre_deploy = (ASSET_ROOT / "pre-deploy-backup.sh").read_text(encoding="utf-8")
    assert "json.load" not in service
    assert "json.load" not in pre_deploy
    assert "backup-current" in service
    assert "release-identity" in pre_deploy
