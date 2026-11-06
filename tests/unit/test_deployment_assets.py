from __future__ import annotations

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


