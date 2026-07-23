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
    assert "upload-artifact" not in workflow
    assert "download-artifact" not in workflow
    assert "jobs:\n  deploy:" in workflow
    assert workflow.index("./verify") < workflow.index("release-build") < workflow.index(
        "environment deploy"
    )
    assert "actions/checkout@" in workflow and "@v4" not in workflow


def test_github_verify_provisions_fresh_runner_before_verify() -> None:
    workflow = (WORKFLOW_ROOT / "verify.yml").read_text(encoding="utf-8")
    assert "actions/setup-python@" in workflow
    assert "actions/setup-node@" in workflow
    assert "python -m pip install -r requirements.txt" in workflow
    assert "python -m pip install --editable ." in workflow
    assert "npm ci --prefix web" in workflow
    assert "npm ci --prefix agent_backends" in workflow
    assert workflow.index("setup-python") < workflow.index("./verify")
    assert workflow.index("npm ci --prefix agent_backends") < workflow.index("./verify")


def test_deploy_workflow_preserves_one_runner_and_exact_release_path() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert workflow.count("runs-on: [self-hosted, production]") == 1
    assert 'release/${{ github.sha }}' not in workflow
    assert '"$PANELS_RELEASE_ROOT/${{ github.sha }}"' in workflow
    assert "--candidate \"$PANELS_RELEASE_ROOT/${{ github.sha }}\"" in workflow


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


def test_deploy_identity_owns_release_controls_and_live_is_read_only() -> None:
    setup = (ASSET_ROOT / "setup-accounts.sh").read_text(encoding="utf-8")
    tmpfiles = (ASSET_ROOT / "panels-environments.tmpfiles").read_text(encoding="utf-8")
    live = (ASSET_ROOT / "panels-live.service").read_text(encoding="utf-8")
    docs = (Path(__file__).resolve().parents[2] / "docs" / "deployment.md").read_text(
        encoding="utf-8"
    )
    assert "panels-deploy" in setup
    assert "usermod --append --groups panels-deploy panels-live" not in setup
    assert "panels-deploy" in tmpfiles
    assert "chown -R panels-deploy:panels-deploy /opt/panels/releases" in setup
    assert "install -d -m 0755 -o panels-deploy -g panels-deploy /opt/panels/current" not in setup
    assert "d /opt/panels/current" not in tmpfiles
    assert "ReadOnlyPaths=/opt/panels/releases /opt/panels/current" in live
    assert "never as root" in docs
