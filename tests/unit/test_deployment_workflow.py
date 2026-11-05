"""The publication workflow keeps its hosted rehearsal and evidence boundaries."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.deployment_rehearsal import deployment_paths

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml"


def environment() -> dict[str, str]:
    return {
        "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "HOME": "/home/vps",
        "RUNNER_TEMP": "/tmp/runner", "RUNNER_ENVIRONMENT": "github-hosted",
        "PANELS_REQUESTED_SHA": "a" * 40, "PANELS_BASELINE_SHA": "b" * 40,
    }


def test_rehearsal_isolates_every_persistent_path_and_preserves_production_defaults() -> None:
    production = deployment_paths(environment())
    assert production["PANELS_CURRENT_ROOT"] == "/home/vps/Deployments/Panels/current"
    assert production["PANELS_APP_HOME"] == "/home/vps"
    assert production["PANELS_SERVICE_NAME"] == "panels-live.service"
    assert production["PANELS_HEALTH_URL"] == "http://127.0.0.1:8767/api/health"
    rehearsal = deployment_paths(environment() | {"PANELS_REHEARSAL": "true"})
    root = Path(rehearsal["PANELS_TEMP_ROOT"])
    assert root.parent == Path("/home/vps/Deployments/Panels")
    for key in ("PANELS_CURRENT_ROOT", "PANELS_APP_HOME", "PANELS_REPORT_DIR",
                "PANELS_CANDIDATE_SOURCE", "PANELS_CODEX_TOOLS"):
        assert Path(rehearsal[key]).is_relative_to(root)
        assert rehearsal[key] != production[key]
    assert rehearsal["PANELS_SERVICE_NAME"] == "panels-rehearsal-123-1.service"


@pytest.mark.parametrize("override", [
    {"RUNNER_ENVIRONMENT": "self-hosted"},
    {"PANELS_BASELINE_SHA": ""},
    {"PANELS_BASELINE_SHA": "a" * 40},
])
def test_rehearsal_refuses_non_disposable_or_no_replacement_setup(
    override: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        deployment_paths(environment() | {"PANELS_REHEARSAL": "true"} | override)


def test_publication_is_hosted_and_retains_its_evidence() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    publication = workflow["jobs"]["build"]
    assert publication["runs-on"] == "ubuntu-24.04"
    assert publication["env"]["PANELS_REHEARSAL"] == "true"
    assert "53d64f0fdbda071a7887945a0d4ddc113694cf21" in (
        publication["env"]["PANELS_BASELINE_SHA"]
    )
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    steps = {step.get("name"): step for step in publication["steps"]}
    names = list(steps)
    assert names.index("Build exact candidate once") < names.index(
        "Seal immutable executable archive"
    )
    assert names.index("Seal immutable executable archive") < names.index(
        "Smoke exact retained archive"
    )
    install = "Install candidate with the update installer"
    assert names.index("Smoke exact retained archive") < names.index(
        "Stage exact archive beside the known-good app"
    ) < names.index(install) < names.index("Collect rehearsal proof")
    assert "deployment_rehearsal.py install" in steps[install]["run"]
    assert names.index("Collect rehearsal proof") < names.index("Check sealed archive is unchanged")
    assert "publication_checks.py verify-selection" in (
        next(step for step in workflow["jobs"]["publish"]["steps"]
             if step.get("name") == "Select complete publication checks")["run"]
    )
    assert steps["Retain exact executable archive"]["with"]["retention-days"] == 30
    assert next(step for step in workflow["jobs"]["publish"]["steps"]
                if step.get("name") == "Retain publication proof")["with"]["retention-days"] == 30


def test_publication_preserves_existing_isolated_suite_report_boundaries() -> None:
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["build"]
    shell = "python3 scripts/deployment_progress.py {0}"
    assert job["defaults"]["run"]["shell"] == shell
    steps = {step.get("name"): step for step in job["steps"] if "run" in step}
    suite_job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["suites"]
    suite_steps = {step.get("name"): step for step in suite_job["steps"] if "run" in step}
    for suite in ("unit", "integration", "E2E"):
        script = suite_steps[f"pytest {suite} at requested source"]["run"]
        assert script.index('"$GITHUB_WORKSPACE/scripts/deployment_codex.py" verify') < (
            script.index('.venv/bin/pytest "${selected[@]}"')
        )
    assert "ffmpeg" in steps["Bootstrap disposable hosted runner"]["run"]
    assert "76d832d3857551a029c4b39c23945eb47c16fe5b" in (
        steps["Provision offline Hermes packaging prerequisite"]["run"]
    )


def test_parallel_publication_binds_checked_build_and_native_mac_before_receipt() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    preparation, suites, publication = jobs["prepare"], jobs["suites"], jobs["publish"]
    native, build, checks = jobs["mac-runtime"], jobs["build"], jobs["checks"]
    assert suites["needs"] == build["needs"] == checks["needs"] == "prepare"
    assert suites["strategy"] == {"fail-fast": False,
                                  "matrix": {"suite": ["unit", "integration", "e2e", "browser"]}}
    assert publication["needs"] == ["prepare", "suites", "checks", "build", "mac-runtime"]
    assert "always()" in publication["if"]
    assert "inputs.operation != 'mac-build'" in publication["if"]
    for job in (preparation, suites, publication, native, build, checks):
        assert job["runs-on"] == ("macos-15" if job is native else "ubuntu-24.04")
        assert job["steps"][0]["with"]["ref"] == "${{ github.sha }}"
        assert not job.get("continue-on-error")
        assert all(not step.get("continue-on-error") for step in job["steps"])
    steps = {step.get("name"): step for step in publication["steps"]}
    names = list(steps)
    gate_name = "Collect checked suite evidence and require successful prerequisites"
    gate = steps[gate_name]
    assert "always()" in gate["if"]
    assert gate["env"]["PANELS_PREREQUISITES"] == (
        '{"prepare":"${{ needs.prepare.result }}","suites":"${{ needs.suites.result }}",'
        '"checks":"${{ needs.checks.result }}","build":"${{ needs.build.result }}"}'
    )
    assert "publication_checks.py collect" in gate["run"]
    assert names.index("Set publication paths") < names.index(gate_name)
    mac_gate = steps["Require successful native Mac publication"]
    assert mac_gate["env"]["PANELS_MAC_BUILD_RESULT"] == "${{ needs.mac-runtime.result }}"
    assert mac_gate["run"] == 'test "$PANELS_MAC_BUILD_RESULT" = success'
    assert names.index(gate_name) < names.index("Require successful native Mac publication")
    assert names.index("Require successful native Mac publication") < names.index(
        "Write immutable publication receipt"
    )
    receipt = steps["Write immutable publication receipt"]
    assert receipt["env"] == {
        "PANELS_ARTIFACT_ID": "${{ needs.build.outputs.app_artifact_id }}",
        "PANELS_MAC_ARTIFACT_ID": "${{ needs.mac-runtime.outputs.artifact_id }}",
        "PANELS_MAC_BUILD_RESULT": "${{ needs.mac-runtime.result }}",
    }
    assert receipt["run"] == "python3 scripts/update_publication.py receipt"
    assert "if" not in receipt
    assert sum("app-build" in step.get("run", "") for step in build["steps"]) == 1
    assert not any("app-build" in step.get("run", "") for step in publication["steps"])
    assert not any("app-build" in step.get("run", "") for step in suites["steps"])
    downloads = [step for step in publication["steps"]
                 if "actions/download-artifact@" in step.get("uses", "")]
    assert len(downloads) == 7
    assert len({step["with"]["path"] for step in downloads}) == 7
    assert steps["Download checked native Mac publication"]["with"] == {
        "artifact-ids": "${{ needs.mac-runtime.outputs.artifact_id }}",
        "path": "${{ env.PANELS_GATE_DIR }}/mac",
        "merge-multiple": True,
    }
    assert "!${{ env.PANELS_GATE_DIR }}/mac/panels-mac-runtime.tar.gz" in (
        steps["Retain publication proof"]["with"]["path"]
    )


def test_parallel_suite_setup_and_evidence_keep_the_existing_native_and_mount_boundaries() -> None:
    suites = yaml.safe_load(WORKFLOW.read_text())["jobs"]["suites"]
    steps = {step.get("name"): step for step in suites["steps"]}
    bootstrap = steps["Bootstrap disposable hosted runner"]["run"]
    assert "ffmpeg" in bootstrap and "bubblewrap" in bootstrap
    assert 'sudo systemctl start "user@$(id -u).service"' in bootstrap
    readiness = steps["Establish authenticated E2E user manager"]
    assert readiness["if"] == "${{ matrix.suite == 'e2e' }}"
    names = list(steps)
    assert names.index("Initialize suite reports") < names.index(
        "Check prepared source and selection"
    ) < names.index("Establish authenticated E2E user manager") < names.index(
        "Prepare requested-source check environment"
    )
    assert names.index("Establish authenticated E2E user manager") < names.index(
        "pytest E2E at requested source"
    )
    assert 'tee -a "$PANELS_REPORT_DIR/e2e.log"' in readiness["run"]
    hermes = steps["Provision offline Hermes integration prerequisite"]
    assert hermes["if"] == "${{ matrix.suite == 'integration' }}"
    assert "76d832d3857551a029c4b39c23945eb47c16fe5b" in hermes["run"]
    assert '"$hermes_source[acp]"' in hermes["run"]
    setup = steps["Prepare requested-source check environment"]["run"]
    for command in ("python3 -m venv .venv", "npm ci --prefix web",
                    "npm ci --prefix agent_backends", "npm --prefix web run build"):
        assert command in setup
    assert '"$GITHUB_WORKSPACE/scripts/deployment_codex.py" setup' in setup
    for name in ("pytest E2E at requested source", "Browser hosts at requested source"):
        command = steps[name]["run"]
        for mount in ('--bind "$PANELS_GATE_DIR/artifact-shadow" /home/vps/Deployments/Panels',
                      '--bind "$PANELS_CANDIDATE_SOURCE" "$PANELS_CANDIDATE_SOURCE"',
                      '--bind "$PANELS_REPORT_DIR" "$PANELS_REPORT_DIR"'):
            assert mount in command
        assert "PLAN_*|PANELS_REAL_*|HERMES_HOME|CODEX_HOME|CLAUDE_CONFIG_DIR" in command
        assert "deployment_check_report.py" in command
    assert '--ro-bind "$git_common_dir" "$git_common_dir"' in (
        steps["Browser hosts at requested source"]["run"]
    )
    upload = steps["Retain suite evidence and diagnostics"]
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["name"] == (
        "panels-publication-${{ matrix.suite }}-${{ github.run_attempt }}"
    )
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["include-hidden-files"] is True
