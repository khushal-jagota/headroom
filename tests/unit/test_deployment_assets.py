from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / "ops" / "panels-environments"
WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def test_linux_setup_installs_bare_panels_wrapper_contract() -> None:
    wrapper_path = ASSET_ROOT / "panels"
    wrapper = wrapper_path.read_text(encoding="utf-8")
    setup = (ASSET_ROOT / "setup-accounts.sh").read_text(encoding="utf-8")

    assert wrapper_path.is_file()
    assert not wrapper_path.is_symlink()
    assert wrapper_path.stat().st_mode & 0o111 == 0o111
    assert wrapper == (
        '#!/bin/sh\n'
        'set -eu\n'
        '\n'
        'exec /opt/panels/current/bin/panels-launcher "$@"\n'
    )
    assert 'asset_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)' in setup
    assert (
        'install -m 0755 -o root -g root "$asset_directory/panels" '
        "/usr/local/bin/panels"
    ) in setup
    assert "ln " not in setup
    assert ".venv/bin/panels" not in setup


def test_bare_panels_wrapper_follows_current_and_preserves_arguments(
    tmp_path: Path,
) -> None:
    canonical_target = "/opt/panels/current/bin/panels-launcher"
    source_wrapper = (ASSET_ROOT / "panels").read_text(encoding="utf-8")
    assert source_wrapper.count(canonical_target) == 1

    command_directory = tmp_path / "command-bin"
    command_directory.mkdir()
    wrapper_path = command_directory / "panels"
    simulated_current = tmp_path / "current"
    wrapper_path.write_text(
        source_wrapper.replace(canonical_target, f"{simulated_current}/bin/panels-launcher"),
        encoding="utf-8",
    )
    shutil.copymode(ASSET_ROOT / "panels", wrapper_path)

    for release_name in ("release-a", "release-b"):
        launcher = tmp_path / release_name / "bin" / "panels-launcher"
        launcher.parent.mkdir(parents=True)
        launcher.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' '{release_name}' \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)

    simulated_current.symlink_to(tmp_path / "release-a", target_is_directory=True)
    outside_checkout = tmp_path / "agent-home"
    outside_checkout.mkdir()
    agent_environment = {
        "HOME": str(outside_checkout),
        "LANG": "C",
        "PATH": f"{command_directory}:/usr/bin:/bin",
    }

    help_result = subprocess.run(
        ["panels", "--help"],
        cwd=outside_checkout,
        env=agent_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert help_result.stdout.splitlines() == ["release-a", "--help"]

    simulated_current.unlink()
    simulated_current.symlink_to(tmp_path / "release-b", target_is_directory=True)
    read_only_arguments = ["ticket", "show", "t_example", "--json"]
    read_only_result = subprocess.run(
        ["panels", *read_only_arguments],
        cwd=outside_checkout,
        env=agent_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert read_only_result.stdout.splitlines() == ["release-b", *read_only_arguments]


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
    assert 'USER="$(id -un)"' in plist
    assert 'LOGNAME="$(id -un)"' in plist
    assert "<key>UserName</key>" not in plist
    assert "Library/Application Support/Panels" in plist
    assert "RunAtLoad" in plist and "KeepAlive" in plist
    assert plist_values["KeepAlive"] == {"SuccessfulExit": False}


def test_github_deployment_checks_out_and_proves_exact_sha_before_deploy() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert "ref: ${{ github.sha }}" in workflow
    assert 'git rev-parse HEAD)" = "${{ github.sha }}' in workflow
    assert "PANELS_RELEASE_ROOT/${{ github.sha }}" in workflow
    assert "release-build" in workflow
    assert "upload-artifact" not in workflow
    assert "download-artifact" not in workflow
    assert "jobs:\n  deploy:" in workflow
    assert workflow.index("git rev-parse HEAD") < workflow.index(
        "release-build"
    ) < workflow.index("environment deploy")
    assert "actions/checkout@" in workflow and "@v4" not in workflow


def test_github_does_not_have_a_standalone_verify_workflow() -> None:
    assert not (WORKFLOW_ROOT / "verify.yml").exists()


def test_github_deploy_does_not_run_source_verification_dependencies() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert "./verify" not in workflow
    assert "playwright install" not in workflow
    assert "npm ci --prefix web" not in workflow
    assert "npm ci --prefix agent_backends" not in workflow


def test_github_deploy_uses_the_self_hosted_runner_toolchain() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert "actions/setup-python@" not in workflow
    assert "actions/setup-node@" not in workflow
    assert "sys.version_info >= (3, 12)" in workflow
    assert 'process.versions.node.split(".")[0]' in workflow
    assert "Node 22 is required" in workflow
    assert workflow.index("sys.version_info") < workflow.index("python3 -m venv .venv")
    assert workflow.index("process.versions.node") < workflow.index("python3 -m venv .venv")


def test_deploy_workflow_preserves_one_runner_and_exact_release_path() -> None:
    workflow = (WORKFLOW_ROOT / "deploy.yml").read_text(encoding="utf-8")
    assert workflow.count("runs-on: [self-hosted, production]") == 1
    assert 'release/${{ github.sha }}' not in workflow
    assert '"$PANELS_RELEASE_ROOT/${{ github.sha }}"' in workflow
    assert "--candidate \"$PANELS_RELEASE_ROOT/${{ github.sha }}\"" in workflow
    assert "python3 -m venv .venv" in workflow
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
