"""Workflow routing and the real mount boundary, without another browser host."""
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
    for key in ("PANELS_CURRENT_ROOT", "PANELS_APP_HOME", "PANELS_LIFECYCLE_PATH",
                "PANELS_REPORT_DIR", "PANELS_CANDIDATE_SOURCE", "PANELS_CODEX_TOOLS"):
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


def test_publication_is_hosted_and_owner_adoption_is_the_only_production_path() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    publication = workflow["jobs"]["deploy"]
    adoption = workflow["jobs"]["adopt"]
    assert publication["runs-on"] == "ubuntu-24.04"
    assert publication["env"]["PANELS_REHEARSAL"] == "true"
    assert "2c0e05cefc82de100bfc576c1a8f3eda7a931f2d" in (
        publication["env"]["PANELS_BASELINE_SHA"]
    )
    assert "workflow_dispatch" in adoption["if"]
    assert "inputs.operation == 'adopt'" in adoption["if"]
    assert "!inputs.rehearsal" in adoption["if"]
    assert "github.ref == 'refs/heads/main'" in adoption["if"]
    assert adoption["runs-on"] == ["self-hosted", "linux", "production", "panels-deploy"]
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    steps = {step.get("name"): step for step in publication["steps"]}
    names = list(steps)
    assert names.index("Build exact candidate once") < names.index(
        "Seal immutable executable archive"
    )
    assert names.index("Seal immutable executable archive") < names.index(
        "Smoke exact retained archive"
    )
    assert names.index("Smoke exact retained archive") < names.index("Deploy exact app")
    assert names.index("Collect rehearsal proof") < names.index(
        "Write immutable publication receipt"
    )
    assert "publication_checks.py verify-selection" in (
        steps["Select complete publication checks"]["run"]
    )
    for name in ("Retain exact executable archive", "Retain publication proof"):
        assert steps[name]["with"]["retention-days"] == 30
    commands = "\n".join(step.get("run", "") for step in adoption["steps"])
    assert "update_runner apply" in commands
    assert "systemd-run --user --wait --collect --pipe" in commands
    assert '--unit="panels-adoption-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in commands
    assert "app-build" not in commands and "pytest" not in commands
    for key in ("REQUEST_ID", "PUBLICATION_RUN_ID", "ARTIFACT_ID", "PROOF_ARTIFACT_ID",
                "APP_SHA", "ARTIFACT_DIGEST", "ARCHIVE_SHA256"):
        assert f'"$UPDATE_{key}"' in commands


def test_publication_preserves_existing_isolated_suite_report_boundaries() -> None:
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]
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
    assert steps["Finalize deployment lifecycle"]["shell"] == "bash"
    # Skip only an unattempted start. Partial start failures must retain CLI diagnostics.
    assert steps["Start deployment lifecycle"]["id"] == "lifecycle_start"
    assert steps["Finalize deployment lifecycle"]["if"] == (
        "${{ always() && steps.lifecycle_start.outcome != 'skipped' "
        "&& steps.lifecycle_start.outcome != '' }}"
    )
    assert steps["Remove rehearsal temporary state"]["shell"] == shell.replace(
        " {0}", " --cleanup {0}"
    )
    assert "ffmpeg" in steps["Bootstrap disposable hosted runner"]["run"]
    assert "76d832d3857551a029c4b39c23945eb47c16fe5b" in (
        steps["Provision offline Hermes packaging prerequisite"]["run"]
    )


def test_parallel_publication_requires_all_suites_before_any_candidate_work() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    preparation, suites, publication = jobs["prepare"], jobs["suites"], jobs["deploy"]
    assert suites["needs"] == "prepare"
    assert suites["strategy"] == {"fail-fast": False,
                                  "matrix": {"suite": ["unit", "integration", "e2e", "browser"]}}
    assert publication["needs"] == ["prepare", "suites"]
    assert "always()" in publication["if"]
    for job in (preparation, suites, publication):
        assert job["runs-on"] == "ubuntu-24.04"
        assert job["steps"][0]["with"]["ref"] == "${{ github.sha }}"
        assert not job.get("continue-on-error")
        assert all(not step.get("continue-on-error") for step in job["steps"])
    steps = {step.get("name"): step for step in publication["steps"]}
    names = list(steps)
    gate_name = "Collect checked suite evidence and require successful prerequisites"
    gate = steps[gate_name]
    assert "always()" in gate["if"]
    assert gate["env"]["PANELS_PREREQUISITES"] == (
        '{"prepare":"${{ needs.prepare.result }}","suites":"${{ needs.suites.result }}"}'
    )
    assert "publication_checks.py collect" in gate["run"]
    assert names.index("Set deployment paths") < names.index(gate_name)
    assert names.index(gate_name) < names.index("Provision offline Hermes packaging prerequisite")
    assert names.index(gate_name) < names.index("Start known-good rehearsal release")
    assert names.index("Validate collected suite reports at requested source") < (
        names.index("Build exact candidate once")
    )
    for name in ("Provision offline Hermes packaging prerequisite", "Build exact candidate once",
                 "Seal immutable executable archive", "Retain exact executable archive",
                 "Write immutable publication receipt"):
        assert "if" not in steps[name]
    assert sum("app-build" in step.get("run", "") for step in publication["steps"]) == 1
    assert not any("app-build" in step.get("run", "") for step in suites["steps"])
    downloads = [step for step in publication["steps"]
                 if "actions/download-artifact@" in step.get("uses", "")]
    assert len(downloads) == 5
    assert len({step["with"]["path"] for step in downloads}) == 5
    assert all("github.run_attempt" in step["with"]["name"] for step in downloads)


def test_parallel_suite_setup_and_evidence_keep_the_existing_native_and_mount_boundaries() -> None:
    suites = yaml.safe_load(WORKFLOW.read_text())["jobs"]["suites"]
    steps = {step.get("name"): step for step in suites["steps"]}
    bootstrap = steps["Bootstrap disposable hosted runner"]["run"]
    assert "ffmpeg" in bootstrap and "bubblewrap" in bootstrap
    assert 'sudo systemctl start "user@$(id -u).service"' in bootstrap
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


def test_post_health_failure_reports_installed_app_without_undoing_local_commit(
    tmp_path: Path,
) -> None:
    """The real finalizer CLI must not turn local commit into workflow success."""
    import os
    import subprocess
    import sys

    from planner.environments.deployment_lifecycle import DeploymentLifecycleStore

    sha = "a" * 40
    path = tmp_path / "deployment-lifecycle.json"
    store = DeploymentLifecycleStore(path)
    store.start("123-1", sha, expected_deployment_id=None)
    store.transition("123-1", "verifying")
    store.transition("123-1", "app_healthy", serving_sha=sha)
    # Replace only the external health observation. Execute the actual lifecycle CLI.
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = tools / "python3"
    executable.write_text(
        '#!/bin/sh\nif [ "$1" = - ]; then cat >/dev/null; '
        'printf "%s\\n" "$FINALIZER_SERVING_SHA"; '
        f'else exec "{sys.executable}" "$@"; fi\n'
    )
    executable.chmod(0o755)
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["adopt"]
    step = next(s for s in job["steps"] if s.get("name") == "Finalize owner adoption lifecycle")
    assert step["if"] == "${{ always() }}"
    assert job["steps"][-1] is step
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env=os.environ | {
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "FINALIZER_SERVING_SHA": sha,
            "PANELS_PRIOR_JOB_STATUS": "failure",
            "PANELS_REQUESTED_SHA": sha,
            "PANELS_LIFECYCLE_PATH": str(path),
            "PANELS_DEPLOYMENT_ID": "123-1",
            "GITHUB_WORKSPACE": str(WORKFLOW.parents[2]),
        },
        text=True, capture_output=True, check=True,
    )
    assert "post_health_check_failed" in result.stdout
    recorded = store.read().lifecycle
    assert recorded is not None and recorded.phase == "failed"
    assert recorded.serving_sha == sha
    assert "installed" in (recorded.detail or "")
    # A healthy endpoint before commit cannot open candidate work through finalization.
    store.start("123-2", sha, expected_deployment_id="123-1")
    store.transition("123-2", "verifying")
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env=os.environ | {
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "FINALIZER_SERVING_SHA": sha,
            "PANELS_PRIOR_JOB_STATUS": "failure",
            "PANELS_REQUESTED_SHA": sha,
            "PANELS_LIFECYCLE_PATH": str(path),
            "PANELS_DEPLOYMENT_ID": "123-2",
            "GITHUB_WORKSPACE": str(WORKFLOW.parents[2]),
        },
        text=True, capture_output=True, check=True,
    )
    recorded = store.read().lifecycle
    assert recorded is not None and recorded.phase == "failed"
    assert recorded.serving_sha is None
