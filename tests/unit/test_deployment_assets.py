from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / "ops" / "panels-environments"
WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def test_panels_wrapper_uses_the_vps_users_current_app_and_preserves_arguments(
    tmp_path: Path,
) -> None:
    source_wrapper = ASSET_ROOT / "panels"
    assert source_wrapper.is_file()
    assert not source_wrapper.is_symlink()
    assert source_wrapper.stat().st_mode & 0o111 == 0o111
    assert source_wrapper.read_text(encoding="utf-8") == (
        "#!/bin/sh\n"
        "set -eu\n"
        "\n"
        'exec "$HOME/Deployments/Panels/current/app/bin/panels-launcher" "$@"\n'
    )

    command_directory = tmp_path / "command-bin"
    command_directory.mkdir()
    wrapper = command_directory / "panels"
    shutil.copy2(source_wrapper, wrapper)
    current_app = tmp_path / "Deployments" / "Panels" / "current" / "app"
    current_app.parent.mkdir(parents=True)
    for release_name in ("release-a", "release-b"):
        launcher = tmp_path / release_name / "bin" / "panels-launcher"
        launcher.parent.mkdir(parents=True)
        launcher.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' '{release_name}' \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)

    current_app.symlink_to(tmp_path / "release-a", target_is_directory=True)
    environment = {
        "HOME": str(tmp_path),
        "LANG": "C",
        "PATH": f"{command_directory}:/usr/bin:/bin",
    }
    first = subprocess.run(
        ["panels", "--help"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert first.stdout.splitlines() == ["release-a", "--help"]

    current_app.unlink()
    current_app.symlink_to(tmp_path / "release-b", target_is_directory=True)
    arguments = ["ticket", "show", "t_example", "--json"]
    second = subprocess.run(
        ["panels", *arguments],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert second.stdout.splitlines() == ["release-b", *arguments]


def test_linux_live_service_is_a_home_relative_user_unit() -> None:
    service = (ASSET_ROOT / "panels-live.service").read_text(encoding="utf-8")
    current = "%h/Deployments/Panels/current"
    assert f"WorkingDirectory={current}" in service
    assert f"ExecStart={current}/app/bin/panels-launcher serve" in service
    assert f"Environment=PLAN_DB_PATH={current}/data/planner.db" in service
    assert f"Environment=PLAN_LOGS_DIR={current}/logs" in service
    assert "Environment=PLAN_HERMES_HOME=%h/.hermes" in service
    assert "Environment=PLAN_PORT=8767" in service
    assert "WantedBy=default.target" in service
    assert "User=" not in service
    assert "/opt/panels" not in service
    assert "/var/lib/panels" not in service
    assert "/etc/panels" not in service


def test_macos_input_remains_a_supervised_user_launch_agent() -> None:
    plist_path = ASSET_ROOT / "panels-launchd.plist"
    plist = plist_path.read_text(encoding="utf-8")
    plist_values = plistlib.loads(plist_path.read_bytes())
    assert "com.panels.live" in plist
    assert 'root="$HOME/Deployments/Panels/current"' in plist
    assert "$root/app/bin/panels-launcher" in plist
    assert 'PLAN_DB_PATH="$root/data/planner.db"' in plist
    assert 'PLAN_HERMES_HOME="$HOME/.hermes"' in plist
    assert "<key>UserName</key>" not in plist
    assert plist_values["KeepAlive"] == {"SuccessfulExit": False}


def test_github_deployment_proves_and_builds_the_requested_exact_sha() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert "push:\n    branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "commit_sha:" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.commit_sha || github.sha" in workflow
    assert 'git -C "$PANELS_CANDIDATE_SOURCE" rev-parse HEAD)' in workflow
    assert "'^[0-9a-f]{40}$'" in workflow
    assert workflow.index("rev-parse HEAD") < workflow.index(
        "app-build"
    ) < workflow.index("app-deploy")
    assert "actions/checkout@" in workflow and "@v4" not in workflow
    assert workflow.count("actions/checkout@") == 1
    assert "ref: ${{ github.sha }}" in workflow
    assert 'git worktree add --detach "$PANELS_CANDIDATE_SOURCE"' in workflow
    assert "upload-artifact" not in workflow
    assert "download-artifact" not in workflow


def test_deploy_workflow_and_user_runner_share_one_real_runner_contract() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    unit = (ASSET_ROOT / "panels-deployment-runner.service").read_text(encoding="utf-8")
    labels = ("self-hosted", "linux", "production", "panels-deploy")
    assert workflow.count(f"runs-on: [{', '.join(labels)}]") == 1
    assert all(label in unit for label in labels)
    assert "WorkingDirectory=%h/Coding/Panels/.github-runner" in unit
    assert "ExecStart=%h/Coding/Panels/.github-runner/run.sh" in unit
    assert "ConditionPathExists=%h/Coding/Panels/.github-runner/.runner" in unit
    assert "User=" not in unit
    assert "${{ runner.temp }}" not in workflow
    assert "PANELS_CANDIDATE_APP=$RUNNER_TEMP/panels-candidate-" in workflow
    assert "PANELS_CANDIDATE_SOURCE=$RUNNER_TEMP/panels-source-" in workflow
    assert '>> "$GITHUB_ENV"' in workflow
    assert 'python3 -m venv "$PANELS_DEPLOY_VENV"' in workflow
    assert "--source-root \"$PANELS_CANDIDATE_SOURCE\"" in workflow
    assert "--requested-sha \"$PANELS_REQUESTED_SHA\"" in workflow
    assert "--candidate-app \"$PANELS_CANDIDATE_APP\"" in workflow
    assert "actions/setup-python@" not in workflow
    assert "actions/setup-node@" not in workflow
    assert "sys.version_info >= (3, 12)" in workflow
    assert "Node 22 is required" in workflow
    assert "./verify" not in workflow
    assert "npm ci --prefix web" not in workflow
    assert "npm ci --prefix agent_backends" not in workflow


def test_runner_setup_registers_the_labels_requested_by_the_workflow(
    tmp_path: Path,
) -> None:
    source = ASSET_ROOT / "configure-deployment-runner.sh"
    assert source.stat().st_mode & 0o111 == 0o111
    runner_root = tmp_path / "Coding" / "Panels" / ".github-runner"
    runner_root.mkdir(parents=True)
    calls = tmp_path / "calls"
    config = runner_root / "config.sh"
    config.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$PANELS_TEST_CALLS\"\n",
        encoding="utf-8",
    )
    config.chmod(0o755)

    subprocess.run(
        [str(source), "https://github.com/example/panels", "short-lived-token"],
        check=True,
        env={
            "HOME": str(tmp_path),
            "PANELS_TEST_CALLS": str(calls),
            "PATH": "/usr/bin:/bin",
        },
    )

    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--unattended",
        "--url",
        "https://github.com/example/panels",
        "--token",
        "short-lived-token",
        "--name",
        "panels-vps-deployment",
        "--labels",
        "production,panels-deploy",
        "--work",
        "_work",
        "--replace",
    ]


def test_production_workflow_uses_the_single_user_live_contract() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    current = "$HOME/Deployments/Panels/current"
    assert f'--current-root "{current}"' in workflow
    assert f'--source-db "{current}/data/planner.db"' in workflow
    assert f'--backup-dir "{current}/data/backups"' in workflow
    assert '--health-url "http://127.0.0.1:8767/api/health"' in workflow
    assert "--service-manager systemctl" in workflow
    assert "--service-name panels-live.service" in workflow
    assert "${{ vars." not in workflow
    assert "release-build" not in workflow
    assert "PANELS_RELEASE_ROOT" not in workflow
    assert "PANELS_CURRENT_POINTER" not in workflow


def test_scheduled_and_scripted_backups_use_the_same_deployed_launcher_contract() -> None:
    service = (ASSET_ROOT / "panels-db-backup.service").read_text(encoding="utf-8")
    pre_deploy = (ASSET_ROOT / "pre-deploy-backup.sh").read_text(encoding="utf-8")
    current = "%h/Deployments/Panels/current"
    assert (
        f"ExecStart={current}/app/bin/panels-launcher environment backup-current"
        in service
    )
    assert f"--source-db {current}/data/planner.db" in service
    assert f"--backup-dir {current}/data/backups" in service
    assert f"--current-app {current}/app" in service
    assert "Environment=PLAN_HERMES_HOME=%h/.hermes" in service
    assert '"$current_app/bin/panels-launcher" environment backup-current' in pre_deploy
    assert '--source-db "$PANELS_BACKUP_SOURCE_DB"' in pre_deploy
    assert '--backup-dir "$PANELS_BACKUP_DIRECTORY"' in pre_deploy
    assert '--current-app "$current_app"' in pre_deploy
    assert 'export PLAN_HERMES_HOME="$HOME/.hermes"' in pre_deploy
    assert "app-identity" not in pre_deploy
    assert "ENVIRONMENT_MANAGER" not in pre_deploy


def test_predeploy_backup_replaces_ambient_hermes_home_with_vps_home(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    launcher = current / "app" / "bin" / "panels-launcher"
    launcher.parent.mkdir(parents=True)
    calls = tmp_path / "calls"
    launcher.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$PLAN_HERMES_HOME\" \"$@\" > \"$PANELS_TEST_CALLS\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    vps_home = tmp_path / "vps"
    subprocess.run(
        ["sh", str(ASSET_ROOT / "pre-deploy-backup.sh")],
        check=True,
        env={
            "HOME": str(vps_home),
            "PANELS_BACKUP_DIRECTORY": str(current / "data" / "backups"),
            "PANELS_BACKUP_SOURCE_DB": str(current / "data" / "planner.db"),
            "PANELS_CURRENT_ROOT": str(current),
            "PANELS_HERMES_HOME": "/ambient/poison",
            "PANELS_TEST_CALLS": str(calls),
            "PLAN_HERMES_HOME": "/ambient/poison",
            "PATH": "/usr/bin:/bin",
        },
    )

    assert calls.read_text(encoding="utf-8").splitlines() == [
        str(vps_home / ".hermes"),
        "environment",
        "backup-current",
        "--source-db",
        str(current / "data" / "planner.db"),
        "--backup-dir",
        str(current / "data" / "backups"),
        "--current-app",
        str(current / "app"),
    ]


def test_service_control_keeps_launchctl_and_uses_systemd_user_manager(
    tmp_path: Path,
) -> None:
    control_path = ASSET_ROOT / "service-control.sh"
    control = control_path.read_text(encoding="utf-8")
    assert 'launchctl=${PANELS_LAUNCHCTL:-/bin/launchctl}' in control
    assert '"$launchctl" kickstart' in control
    assert '"$launchctl" bootout' in control
    assert '"$launchctl" bootstrap' in control
    assert '"$launchctl" kill SIGTERM "$target" >/dev/null 2>&1 || true' in control
    assert 'domain=${PANELS_LAUNCHD_DOMAIN:-"gui/$(id -u)"}' in control
    assert "exec systemctl --user \"$action\" panels-live.service" in control

    command_directory = tmp_path / "bin"
    command_directory.mkdir()
    calls = tmp_path / "calls"
    systemctl = command_directory / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$PANELS_TEST_CALLS\"\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    subprocess.run(
        ["sh", str(control_path), "restart"],
        check=True,
        env={
            "HOME": str(tmp_path),
            "PANELS_TEST_CALLS": str(calls),
            "PATH": f"{command_directory}:/usr/bin:/bin",
        },
    )
    assert calls.read_text(encoding="utf-8") == "--user restart panels-live.service\n"


def test_launchctl_restart_recovers_when_loaded_job_has_no_process(tmp_path: Path) -> None:
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


def test_multi_account_and_persistent_staging_assets_are_removed() -> None:
    assert not (ASSET_ROOT / "setup-accounts.sh").exists()
    assert not (ASSET_ROOT / "panels-environments.tmpfiles").exists()
    assert not (ASSET_ROOT / "panels-staging.service").exists()
    user_units = [
        ASSET_ROOT / "panels-live.service",
        ASSET_ROOT / "panels-deployment-runner.service",
        ASSET_ROOT / "panels-db-backup.service",
    ]
    assert all("User=" not in path.read_text(encoding="utf-8") for path in user_units)
