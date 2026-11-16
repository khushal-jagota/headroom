import json
from pathlib import Path

import pytest
from tests.compare.runner import run_list


def test_comparison(comparison_list: Path, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    right_module = request.config.getoption("--compare-right-module")
    if right_module is None:
        candidate = json.loads(comparison_list.read_text()).get("candidate")
        right_module = (
            "tests.compare.ts_launcher" if candidate == "typescript" else "tests.compare.host"
        )
    report = run_list(comparison_list, tmp_path, right_module)
    report_directory = request.config.getoption("--compare-report-dir")
    if report_directory:
        destination = Path(report_directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{comparison_list.stem}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "rules"}))
    assert not report["differences"], json.dumps(report["differences"], indent=2)
    if comparison_list.stem == "unit-15.real-core-reruns.unit-11.files":
        observed = report["captures"]
        # Same-route token history also requires these transitions in the candidate.
        assert observed["catalog_0"]["revision"] == observed["catalog_1"]["revision"]
        assert observed["catalog_1"]["revision"] != observed["catalog_2"]["revision"]
        assert observed["catalog_6"]["revision"] != observed["goal_after_refresh"]["revision"]
