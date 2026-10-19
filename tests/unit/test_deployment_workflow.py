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


def test_workflow_routes_rehearsal_away_from_production_and_checks_pin_before_suites() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = workflow["jobs"]["deploy"]
    assert job["if"] == "${{ inputs.rehearsal || github.ref == 'refs/heads/main' }}"
    assert job["runs-on"] == (
        "${{ inputs.rehearsal && 'ubuntu-24.04' || "
        "fromJSON('[\"self-hosted\", \"linux\", \"production\", \"panels-deploy\"]') }}"
    )
    assert "inputs.rehearsal && format('deploy-rehearsal-" in workflow["concurrency"]["group"]
    steps = {step.get("name"): step for step in job["steps"]}
    names = list(steps)
    assert names.index("Start deployment lifecycle") < names.index("Prepare deployment runtime")
    assert steps["Prepare deployment runtime"]["if"] == "${{ !inputs.rehearsal }}"
    assert steps["Prepare rehearsal runtime"]["if"] == "${{ inputs.rehearsal }}"
    assert steps["Prepare rehearsal runtime"]["run"] == steps["Prepare deployment runtime"]["run"]
    assert names.index("Prepare rehearsal runtime") < names.index(
        "Start known-good rehearsal release"
    )
    for suite in ("unit", "integration", "E2E"):
        script = steps[f"pytest {suite} at requested source"]["run"]
        assert script.index('"$GITHUB_WORKSPACE/scripts/deployment_codex.py" verify') < (
            script.index('.venv/bin/pytest "${selected[@]}"')
        )
    deploy = steps["Deploy exact app"]["run"]
    for value in ("PANELS_CURRENT_ROOT", "PANELS_HEALTH_URL", "PANELS_SERVICE_NAME",
                  "PANELS_LIFECYCLE_PATH", "PANELS_APP_HOME"):
        assert value in deploy
    assert "inputs.rehearsal" in steps["Retain rehearsal proof"]["if"]
    production_cleanup = "Remove production deployment temporary state"
    rehearsal_cleanup = "Remove rehearsal temporary state"
    assert steps[production_cleanup]["if"] == "${{ always() && !inputs.rehearsal }}"
    assert steps[rehearsal_cleanup]["if"] == "${{ always() && inputs.rehearsal }}"
    assert steps[production_cleanup]["run"] == steps[rehearsal_cleanup]["run"]
    assert names.index(production_cleanup) < names.index("Finalize deployment lifecycle")
    assert names.index("Finalize deployment lifecycle") < names.index("Collect rehearsal proof")
    assert names.index("Collect rehearsal proof") < names.index("Retain rehearsal proof")
    assert names.index("Retain rehearsal proof") < names.index(rehearsal_cleanup)
    hermes = steps["Provision offline Hermes packaging prerequisite"]
    assert hermes["if"] == "${{ inputs.rehearsal }}"
    assert "76d832d3857551a029c4b39c23945eb47c16fe5b" in hermes["run"]
    assert '"$hermes_source[acp]"' in hermes["run"]
    assert "ffmpeg" in steps["Bootstrap disposable hosted runner"]["run"]
