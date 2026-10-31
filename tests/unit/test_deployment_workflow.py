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
    from planner.environments.update_requests import FileUpdateRequestStore

    root = tmp_path / "current"
    (root / "data").mkdir(parents=True)
    sha = release().manifest.app_sha
    path = root / "data" / "deployment-lifecycle.json"
    store = DeploymentLifecycleStore(path)
    requests = FileUpdateRequestStore(root / "data" / "owner-update-request.json")
    request = OwnerUpdateRequest("first", release(), "now", "dispatched", "2026-10-30T00:00:00Z")
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
        requests.transition("first", frozenset({"dispatched"}), "cancelled")
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
        assert archived is not None and archived.state == "cancelled"
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


@pytest.mark.parametrize("target", [
    "root", "candidate", "baseline", "hermes", "interpreter", "stdlib",
])
def test_cleanup_refuses_execution_path_alias_before_any_service_or_file_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    import sys
    import sysconfig
    from types import SimpleNamespace

    from scripts import deployment_rehearsal as helper

    temporary = tmp_path / "temporary"
    temporary.mkdir()
    checkout = Path(helper.__file__).resolve().parents[1]
    alias = tmp_path / "execution-alias"
    protected = {
        "interpreter": Path(sys.executable).resolve().parent,
        "stdlib": Path(sysconfig.get_path("stdlib")).resolve(),
    }.get(target, checkout)
    alias.symlink_to(protected, target_is_directory=True)
    values = {
        "PANELS_TEMP_ROOT": str(temporary),
        "PANELS_CANDIDATE_SOURCE": str(temporary / "source"),
        "PANELS_REHEARSAL": "true",
    }
    if target in {"root", "interpreter", "stdlib"}:
        values["PANELS_TEMP_ROOT"] = str(alias)
    elif target == "candidate":
        values["PANELS_CANDIDATE_SOURCE"] = str(alias)
    elif target == "baseline":
        (temporary / "baseline-source").symlink_to(checkout, target_is_directory=True)
    else:
        values["PANELS_REHEARSAL_HERMES_ROOT"] = str(alias)
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cleanup performed an action before admission")

    # Replace helper dependencies, not the process-wide modules used by pytest teardown.
    monkeypatch.setattr(helper, "subprocess", SimpleNamespace(run=forbidden))
    monkeypatch.setattr(helper, "shutil", SimpleNamespace(rmtree=forbidden))
    try:
        with pytest.raises(ValueError, match="overlaps finalizer execution path"):
            helper.cleanup()
        assert temporary.is_dir()
    finally:
        alias.unlink()
        if (temporary / "baseline-source").is_symlink():
            (temporary / "baseline-source").unlink()


def test_rehearsal_cleanup_preserves_unretained_or_unfinished_outcome_evidence(
    tmp_path: Path,
) -> None:
    import json

    from scripts.deployment_rehearsal import validate_cleanup

    root = tmp_path / "rehearsal"
    root.mkdir()
    lifecycle = root / "lifecycle.json"
    values = {
        "PANELS_TEMP_ROOT": str(root), "PANELS_CANDIDATE_SOURCE": str(root / "source"),
        "PANELS_REHEARSAL": "true", "PANELS_LIFECYCLE_PATH": str(lifecycle),
        "PANELS_DEPLOYMENT_ID": "attempt", "PANELS_LIFECYCLE_START_OUTCOME": "success",
    }
    lifecycle.write_text(json.dumps({"phase": "app_healthy", "deployment_id": "attempt"}))
    with pytest.raises(ValueError, match="diagnostics are not retained"):
        validate_cleanup(values)
    values["PANELS_PROOF_RETENTION_OUTCOME"] = "success"
    with pytest.raises(ValueError, match="no terminal lifecycle outcome"):
        validate_cleanup(values)
    lifecycle.write_text(json.dumps({"phase": "succeeded", "deployment_id": "attempt"}))
    validate_cleanup(values)
    lifecycle.unlink()
    with pytest.raises(ValueError, match="evidence is unavailable"):
        validate_cleanup(values)
    # A suite or failed setup that never attempted installation has no outcome to finalize.
    values["PANELS_LIFECYCLE_START_OUTCOME"] = "skipped"
    validate_cleanup(values)


def test_lifecycle_finalizer_uses_isolated_checkout_source_and_reports_missing_source(
    tmp_path: Path,
) -> None:
    import json
    import shutil
    import subprocess
    import sys

    from planner.environments.deployment_lifecycle import DeploymentLifecycleStore

    store = DeploymentLifecycleStore(tmp_path / "lifecycle.json")
    sha = "a" * 40
    store.start("attempt", sha)
    store.transition("attempt", "verifying")
    store.transition("attempt", "app_healthy", serving_sha=sha)
    launcher = WORKFLOW.parents[2] / "scripts/deployment_finalize.py"
    command = [sys.executable, "-I", "-S", "-B", str(launcher), "lifecycle", "transition",
               "--path", str(store.path), "--deployment-id", "attempt", "--phase", "succeeded"]
    result = subprocess.run(command, capture_output=True, text=True, check=True, cwd=tmp_path)
    assert json.loads(result.stdout)["phase"] == "succeeded"
    # An empty checkout cannot silently use this test environment's installed planner.
    empty = tmp_path / "empty/scripts"
    empty.mkdir(parents=True)
    shutil.copyfile(launcher, empty / launcher.name)
    command[4] = str(empty / launcher.name)
    result = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


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
