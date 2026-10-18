"""Prove deployment cannot credit missing collection or skipped required cases."""

from pathlib import Path

import pytest
from scripts.deployment_check_report import check_pytest, check_vitest


def test_pytest_report_requires_each_selected_module_and_executed_proof(tmp_path: Path) -> None:
    report = tmp_path / "pytest.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.unit.test_other" name="ok"/></testsuite>'
    )
    with pytest.raises(ValueError, match="selected file collected no tests"):
        check_pytest(report, ["tests/unit/test_one.py"])
    report.write_text(
        '<testsuite><testcase classname="tests.unit.test_one_extra" name="ok"/></testsuite>'
    )
    with pytest.raises(ValueError, match="selected file collected no tests"):
        check_pytest(report, ["tests/unit/test_one.py"])
    report.write_text('<testsuite/>')
    with pytest.raises(ValueError, match="zero tests"):
        check_pytest(report, ["tests/unit/test_one.py"])
    report.write_text(
        '<testsuite><testcase classname="tests.unit.test_one" name="ok"/></testsuite>'
    )
    check_pytest(report, ["tests/unit/test_one.py"])


def test_pytest_provider_opt_in_cannot_excuse_required_skip(tmp_path: Path) -> None:
    report = tmp_path / "pytest.xml"
    reason = "set PANELS_REAL_LUNA_LIFECYCLE=1 to run real provider calls"
    report.write_text(
        '<testsuite><testcase classname="tests.unit.test_one" name="ok"/>'
        '<testcase classname="tests.integration.test_orchestrator_worker_real_lifecycle" '
        f'name="paid"><skipped message="{reason}"/></testcase></testsuite>'
    )
    check_pytest(report, ["tests/unit/test_one.py"])
    report.write_text(report.read_text().replace("tests.unit.test_one", "tests.unit.test_skipped")
                      .replace('name="ok"/>', f'name="ok"><skipped message="{reason}"/>'
                               '</testcase>'))
    with pytest.raises(ValueError, match="unexpected required skip"):
        check_pytest(report, ["tests/unit/test_skipped.py"])


def test_frontend_requires_passed_assertions_in_every_selected_host(tmp_path: Path) -> None:
    import json

    report = tmp_path / "vitest.json"
    second_assertions: list[dict[str, str]] = []
    value = {"success": True, "testResults": [
        {"name": "/source/web/tests/one.test.ts", "assertionResults": [{"status": "passed"}]},
        {"name": "/source/web/tests/two.test.ts", "assertionResults": second_assertions},
    ]}
    report.write_text(json.dumps(value))
    check_vitest(report, ["web/tests/one.test.ts"])
    with pytest.raises(ValueError, match="empty, failed, or skipped"):
        check_vitest(report, ["web/tests/two.test.ts"])
    second_assertions.append({"status": "pending"})
    report.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="empty, failed, or skipped"):
        check_vitest(report, ["web/tests/two.test.ts"])
