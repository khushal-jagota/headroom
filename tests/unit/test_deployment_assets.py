from __future__ import annotations

import os
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
        'exec "$HOME/Deployments/Panels/current/app/bin/panels" "$@"\n'
    )

    command_directory = tmp_path / "command-bin"
    command_directory.mkdir()
    wrapper = command_directory / "panels"
    shutil.copy2(source_wrapper, wrapper)
    current_app = tmp_path / "Deployments" / "Panels" / "current" / "app"
    current_app.parent.mkdir(parents=True)
    for release_name in ("release-a", "release-b"):
        cli = tmp_path / release_name / "bin" / "panels"
        cli.parent.mkdir(parents=True)
        cli.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' '{release_name}' \"$@\"\n",
            encoding="utf-8",
        )
        cli.chmod(0o755)

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


def test_runner_setup_registers_the_labels_requested_by_the_workflow(
    tmp_path: Path,
) -> None:
    source = ASSET_ROOT / "configure-deployment-runner.sh"
    assert source.stat().st_mode & 0o111 == 0o111
    runner_root = tmp_path / "Deployments" / "Panels" / "deployment-runner"
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
    # A machine with systemctl and no launchctl — the VPS. The script chooses launchctl
    # whenever it can find one, so the only way to ask it for the systemd branch is a PATH
    # with no launchctl on it; leaving the system directories in would hand a macOS host
    # its real /bin/launchctl and run this against the actual service manager.
    subprocess.run(
        ["/bin/sh", str(control_path), "restart"],
        check=True,
        env={
            "HOME": str(tmp_path),
            "PANELS_TEST_CALLS": str(calls),
            "PATH": str(command_directory),
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
