"""Owner adoption finalization executes against disposable durable records."""
from __future__ import annotations

import os
import subprocess
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
    publication = workflow["jobs"]["build"]
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
    assert names.index("Collect rehearsal proof") < names.index("Check sealed archive is unchanged")
    assert "publication_checks.py verify-selection" in (
        next(step for step in workflow["jobs"]["publish"]["steps"]
             if step.get("name") == "Select complete publication checks")["run"]
    )
    assert steps["Retain exact executable archive"]["with"]["retention-days"] == 30
    assert next(step for step in workflow["jobs"]["publish"]["steps"]
                if step.get("name") == "Retain publication proof")["with"]["retention-days"] == 30
    commands = "\n".join(step.get("run", "") for step in adoption["steps"])
    assert 'update_runner "${operation_arguments[@]}"' in commands
    assert "systemd-run --user --wait --collect --pipe" in commands
    assert '--unit="panels-adoption-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in commands
    assert "app-build" not in commands and "pytest" not in commands
    # The runner reads pinned publication identifiers from the request receipt.
    for key in ("REQUEST_ID", "APP_SHA"):
        assert f'"$UPDATE_{key}"' in commands


@pytest.mark.parametrize("operation", ["adopt", "recover-legacy"])
def test_legacy_recovery_reuses_serialized_owner_adoption(
    tmp_path: Path, operation: str,
) -> None:
    """Exercise the actual shell: recovery must stay isolated and retain every pin."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "recover-legacy" in workflow["on"]["workflow_dispatch"]["inputs"]["operation"]["options"]
    assert "inputs.operation == 'recover-legacy'" in workflow["concurrency"]["group"]
    jobs = workflow["jobs"]
    for name in ("prepare", "mac-runtime", "publish"):
        assert "inputs.operation != 'recover-legacy'" in jobs[name]["if"]
    assert "inputs.operation == 'recover-legacy'" in jobs["adopt"]["if"]
    step = next(s for s in jobs["adopt"]["steps"] if s.get("id") == "owner_apply")
    command = tmp_path / "systemd-run"
    recorded = tmp_path / "arguments"
    command.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$RECORDED_ARGUMENTS"\n')
    command.chmod(0o755)
    variables = {
        "UPDATE_OPERATION": operation, "UPDATE_FAILED_DEPLOYMENT_ID": "failed-attempt",
        "UPDATE_PRIOR_SHA": "a" * 40, "UPDATE_REQUEST_ID": "request",
        "UPDATE_PUBLICATION_RUN_ID": "1", "UPDATE_ARTIFACT_ID": "2",
        "UPDATE_PROOF_ARTIFACT_ID": "3", "UPDATE_APP_SHA": "b" * 40,
        "UPDATE_ARTIFACT_DIGEST": "c" * 64, "UPDATE_ARCHIVE_SHA256": "d" * 64,
        "GITHUB_RUN_ID": "4", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKSPACE": str(tmp_path),
        "PANELS_DEPLOY_VENV": str(tmp_path / "venv"), "PANELS_DEPLOYMENT_ID": "4-1",
        "RECORDED_ARGUMENTS": str(recorded), "PATH": str(tmp_path) + os.pathsep + os.defpath,
    }
    subprocess.run(["/bin/bash", "-e", "-c", step["run"]], env=variables, check=True)
    arguments = recorded.read_text().splitlines()
    assert arguments[:4] == ["--user", "--wait", "--collect", "--pipe"]
    assert arguments[arguments.index("planner.environments.update_runner") + 1] == (
        "recover-legacy" if operation == "recover-legacy" else "apply"
    )
    assert arguments[arguments.index("--request-id") + 1] == "request"
    assert arguments[arguments.index("--app-sha") + 1] == "b" * 40
    if operation == "recover-legacy":
        for flag, value in (("--failed-deployment-id", "failed-attempt"),
                            ("--prior-sha", "a" * 40), ("--deployment-id", "4-1")):
            assert arguments[arguments.index(flag) + 1] == value
    else:
        assert "--failed-deployment-id" not in arguments


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


@pytest.mark.parametrize("case", [
    "later_check", "uncertain", "cancelled", "replacement", "same_sha_stale",
    "health_failure", "recovery_failure", "success", "replacement_no_lifecycle",
    "missing_receipt", "installed_uncertain", "apply_failure",
])
def test_adoption_finalizer_reports_own_evidence_without_service_actions(
    tmp_path: Path, case: str,
) -> None:
    """Execute the workflow finalizer against disposable records, never provisioning."""
    import json
    import os
    import subprocess
    import sys
    from dataclasses import replace

    from tests.unit.test_owner_updates import release

    from planner.environments.deployment_lifecycle import DeploymentLifecycleStore
    from planner.environments.update_contracts import OwnerUpdateRequest
    from planner.environments.update_requests import CANCELLED_DETAIL, FileUpdateRequestStore

    root = tmp_path / "current"
    (root / "data").mkdir(parents=True)
    sha = release().manifest.app_sha
    path = root / "data" / "deployment-lifecycle.json"
    store = DeploymentLifecycleStore(path)
    requests = FileUpdateRequestStore(root / "data" / "owner-update-request.json")
    request = OwnerUpdateRequest("first", release(), "now", "requested", "2026-10-30T00:00:00Z")
    requests.create(request)
    if case != "replacement_no_lifecycle":
        store.start("123-1", sha, expected_deployment_id=None)
    if case in {"later_check", "success", "installed_uncertain"}:
        store.transition("123-1", "verifying")
        store.transition("123-1", "app_healthy", serving_sha=sha)
    elif case in {"health_failure", "recovery_failure"}:
        store.transition("123-1", "failed", code=(
            "candidate_unhealthy" if case == "health_failure" else "rollback_recovery_failed"
        ), detail="Observed health or recovery failure.")
    elif case in {"cancelled", "replacement", "same_sha_stale",
                  "replacement_no_lifecycle", "missing_receipt"}:
        requests.transition("first", frozenset({"requested"}), "failed", detail=CANCELLED_DETAIL)
        if case != "cancelled":
            requests.create(replace(request, request_id="replacement"))
        if case == "same_sha_stale":
            store.start("123-2", sha, expected_deployment_id="123-1")
    if case == "missing_receipt":
        for receipt in (root / "data" / "cancelled-update-requests").glob("*.json"):
            receipt.unlink()
    before_lifecycle = path.read_bytes() if path.exists() else None
    before_request = requests.path.read_bytes()
    # The existing external-health shim emits a supplied observation. No HTTP,
    # service control, installation, or skill provisioning command can run here.
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
    cleanup = next(s for s in job["steps"] if s.get("name") == "Remove adoption temporary state")
    assert job["steps"].index(step) < job["steps"].index(cleanup)
    # Exercise the harmful reorder too: cleanup removes the actual temporary
    # environment, while the finalizer keeps its independent host/checkout runtime.
    temporary = tmp_path / "disposable"
    (temporary / "venv/bin").mkdir(parents=True)
    (temporary / "venv/bin/python").symlink_to(sys.executable)
    cleanup_env = os.environ | {
        "PANELS_TEMP_ROOT": str(temporary),
        "PANELS_CANDIDATE_SOURCE": str(temporary / "source"),
        "PANELS_REHEARSAL": "false",
    }
    subprocess.run(["bash", "-euo", "pipefail", "-c", cleanup["run"]],
                   env=cleanup_env, capture_output=True, text=True, check=True)
    assert not temporary.exists()
    apply_failed = case in {
        "apply_failure", "health_failure", "recovery_failure", "cancelled",
        "replacement", "same_sha_stale", "replacement_no_lifecycle", "missing_receipt",
    }
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env=os.environ | {
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "FINALIZER_SERVING_SHA": sha,
            "PANELS_CURRENT_ROOT": str(root),
            "PANELS_DEPLOY_VENV": str(temporary / "venv"),
            "GITHUB_WORKSPACE": str(WORKFLOW.parents[2]),
            "PANELS_DEPLOYMENT_ID": "123-1", "UPDATE_REQUEST_ID": "first",
            "UPDATE_APP_SHA": sha,
            "UPDATE_APPLY_OUTCOME": "failure" if apply_failed else "success",
            "UPDATE_PROVISION_OUTCOME": ("success" if case == "success" else
                                         "skipped" if apply_failed or case == "installed_uncertain"
                                         else "failure"),
            "UPDATE_PUBLISHED_ROUTE_OUTCOME": "success" if case == "success" else "skipped",
        }, text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout)
    recorded = store.read().lifecycle
    assert requests.path.read_bytes() == before_request
    if case == "replacement_no_lifecycle":
        assert recorded is None and not path.exists()
        assert report["status"] == "superseded"
        assert "Update canceled before installation" in report["detail"]
        archived = requests.read_for_reporting("first")
        assert archived is not None and archived.state == "failed"
        assert archived.detail == CANCELLED_DETAIL
        return
    assert recorded is not None
    if case == "missing_receipt":
        assert report["status"] == "superseded"
        assert path.read_bytes() == before_lifecycle
        assert "uncertain" in report["detail"]
        assert "canceled" not in report["detail"]
    elif case == "installed_uncertain":
        assert recorded.phase == "unknown" and recorded.serving_sha == sha
        assert "installed" in (recorded.detail or "")
        from planner.environments.update_admission import matching_commit
        assert matching_commit(store, sha)
    elif case in {"replacement", "same_sha_stale"}:
        assert report["status"] == "superseded"
        assert path.read_bytes() == before_lifecycle
        assert "Update canceled before installation" in report["detail"]
    elif case in {"health_failure", "recovery_failure"}:
        assert report["status"] == "preserved"
        assert path.read_bytes() == before_lifecycle
        assert recorded.code == (
            "candidate_unhealthy" if case == "health_failure" else "rollback_recovery_failed"
        )
        assert store.project(sha).state == "problem"
        assert store.project(sha).outcome == "failed"
    elif case == "cancelled":
        assert recorded.phase == "cancelled"
        assert recorded.detail == (
            "Update canceled before installation. The previous version stayed active."
        )
        assert store.project(sha).state == "idle"
        assert store.project(sha).outcome == "cancelled"
    elif case in {"uncertain", "apply_failure"}:
        assert recorded.phase == "unknown"
        assert recorded.serving_sha is None
        assert "uncertain" in (recorded.detail or "")
        assert store.project(sha).state == "unknown"
        assert recorded.code == "admission_evidence_uncertain"
        assert "health" not in (recorded.detail or "")
        assert "rollback" not in (recorded.detail or "")
    elif case == "later_check":
        assert recorded.phase == "failed" and recorded.serving_sha == sha
        assert recorded.code == "workflow_check_failed"
        assert "agent skill provisioning" in (recorded.detail or "")
        assert "installed" in (recorded.detail or "")
    else:
        assert recorded.phase == "succeeded" and recorded.serving_sha == sha


def test_adoption_reporting_write_failure_is_observable_without_lifecycle_mutation(
    tmp_path: Path,
) -> None:
    import json
    import subprocess
    import sys

    root = tmp_path / "current"
    data = root / "data"
    data.mkdir(parents=True)
    # The lock cannot be opened as a regular file. The finalizer must not report success.
    (data / "owner-update-request.json.lock").mkdir()
    launcher = WORKFLOW.parents[2] / "scripts/deployment_finalize.py"
    result = subprocess.run([
        sys.executable, "-I", "-S", "-B", str(launcher), "adoption", "--current-root", str(root),
        "--request-id", "request", "--deployment-id", "attempt", "--app-sha", "a" * 40,
        "--apply-outcome", "success", "--provision-outcome", "success",
        "--published-route-outcome", "success", "--serving-sha", "a" * 40,
    ], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "uncertain"
    assert not (data / "deployment-lifecycle.json").exists()
