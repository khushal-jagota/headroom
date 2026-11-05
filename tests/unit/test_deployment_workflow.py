"""Owner adoption finalization executes against disposable durable records."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml"


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
