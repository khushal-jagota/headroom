from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / "ops" / "panels-environments"
WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def test_linux_live_service_uses_current_launcher_and_external_state() -> None:
    service = (ASSET_ROOT / "panels-live.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/panels/current/bin/panels-launcher serve" in service
    assert "/opt/panels/live" not in service
    assert "ReadWritePaths=/var/lib/panels/environments/live" in service


def test_macos_input_is_supervised_and_uses_current_launcher() -> None:
    plist_path = ASSET_ROOT / "panels-launchd.plist"
    plist = plist_path.read_text(encoding="utf-8")
    plist_values = plistlib.loads(plist_path.read_bytes())
    assert "com.panels.live" in plist
    assert "$root/current/bin/panels-launcher" in plist
    assert 'PLAN_CONFIG_PATH="$root/config.yaml"' in plist
    assert "<key>UserName</key>" not in plist
    assert "Library/Application Support/Panels" in plist
    assert "RunAtLoad" in plist and "KeepAlive" in plist
    assert plist_values["KeepAlive"] == {"SuccessfulExit": False}


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
    assert "python -m venv .venv" in workflow
    assert ".venv/bin/python -m pip install -r requirements.txt" in workflow
    assert ".venv/bin/python -m pip install --editable ." in workflow
    assert "npm ci --prefix web" in workflow
    assert "npm ci --prefix agent_backends" in workflow
    assert ".venv/bin/python -m playwright install --with-deps chromium" in workflow
    assert workflow.index("setup-python") < workflow.index("./verify")
    assert workflow.index("npm ci --prefix agent_backends") < workflow.index("./verify")
    assert workflow.index(
        ".venv/bin/python -m playwright install --with-deps chromium"
    ) < workflow.index("./verify")


def test_github_deploy_provisions_playwright_before_release_gate() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert ".venv/bin/python -m playwright install --with-deps chromium" in workflow
    assert workflow.index(
        ".venv/bin/python -m playwright install --with-deps chromium"
    ) < workflow.index("./verify")


def test_deploy_workflow_preserves_one_runner_and_exact_release_path() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert workflow.count("runs-on: [self-hosted, production]") == 1
    assert 'release/${{ github.sha }}' not in workflow
    assert '"$PANELS_RELEASE_ROOT/${{ github.sha }}"' in workflow
    assert "--candidate \"$PANELS_RELEASE_ROOT/${{ github.sha }}\"" in workflow
    assert "python -m venv .venv" in workflow
    assert ".venv/bin/python -m planner environment release-build" in workflow
    assert ".venv/bin/python -m planner environment deploy" in workflow
    assert ".build-venv" not in workflow


def test_deploy_workflow_maps_operator_repository_variables_into_the_job() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    for name in (
        "PANELS_RELEASE_ROOT",
        "PANELS_CURRENT_POINTER",
        "PANELS_BACKUP_SOURCE_DB",
        "PANELS_BACKUP_DIRECTORY",
        "PANELS_DEPLOYMENT_RECORDS",
        "PANELS_HEALTH_URL",
        "PANELS_SERVICE_MANAGER",
        "PANELS_SERVICE_NAME",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow


def test_service_control_uses_real_manager_verbs_and_backup_uses_shared_identity_cli() -> None:
    control = (ASSET_ROOT / "service-control.sh").read_text(encoding="utf-8")
    assert 'launchctl=${PANELS_LAUNCHCTL:-/bin/launchctl}' in control
    assert '"$launchctl" kickstart' in control
    assert '"$launchctl" bootout' in control
    assert '"$launchctl" bootstrap' in control
    assert '"$launchctl" kill SIGTERM "$target" >/dev/null 2>&1 || true' in control
    assert 'domain=${PANELS_LAUNCHD_DOMAIN:-"gui/$(id -u)"}' in control
    assert "label=${PANELS_LAUNCHD_LABEL:-com.panels.live}" in control
    service = (ASSET_ROOT / "panels-db-backup.service").read_text(encoding="utf-8")
    pre_deploy = (ASSET_ROOT / "pre-deploy-backup.sh").read_text(encoding="utf-8")
    assert "json.load" not in service
    assert "json.load" not in pre_deploy
    assert "backup-current" in service
    assert "release-identity" in pre_deploy


def test_user_service_restart_recovers_when_loaded_job_has_no_process(tmp_path: Path) -> None:
    fake_launchctl = tmp_path / "launchctl"
    state = tmp_path / "state"
    calls = tmp_path / "calls"
    state.write_text("waiting\n", encoding="utf-8")
    fake_launchctl.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PANELS_TEST_CALLS\"\n"
        "case \"$1\" in\n"
        "  print) test \"$(cat \"$PANELS_TEST_STATE\")\" != unloaded ;;\n"
        "  kill) exit 1 ;;\n"
        "  bootout) printf 'unloaded\\n' > \"$PANELS_TEST_STATE\" ;;\n"
        "  bootstrap) printf 'running\\n' > \"$PANELS_TEST_STATE\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    environment = os.environ | {
        "PANELS_LAUNCHCTL": str(fake_launchctl),
        "PANELS_LAUNCHD_DOMAIN": "gui/501",
        "PANELS_TEST_CALLS": str(calls),
        "PANELS_TEST_STATE": str(state),
    }

    subprocess.run(
        ["sh", str(ASSET_ROOT / "service-control.sh"), "restart"],
        check=True,
        env=environment,
    )

    assert calls.read_text(encoding="utf-8").splitlines() == [
        "print gui/501/com.panels.live",
        "kill SIGTERM gui/501/com.panels.live",
        "print gui/501/com.panels.live",
        "bootout gui/501/com.panels.live",
        "bootstrap gui/501 "
        + str(Path.home() / "Library/LaunchAgents/com.panels.live.plist"),
    ]
    assert state.read_text(encoding="utf-8") == "running\n"


def test_linux_deploy_identity_owns_release_controls_and_live_is_read_only() -> None:
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
    assert "signed-in" in docs and "operator" in docs
    assert "does not require root access" in docs
