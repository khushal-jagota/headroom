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
    opt_ins = {
        "tests.unit.test_conversation_claude_agent_sdk":
            "set PANELS_REAL_CLAUDE_TESTS=1 with claude installed to run this",
        "tests.unit.test_conversation_claude_real_cli":
            "set PANELS_REAL_CLAUDE_TESTS=1 with claude installed to run this",
        "tests.unit.test_conversation_codex_real_cli":
            "set PANELS_REAL_CODEX_TESTS=1 with codex installed to run this",
        "tests.unit.test_conversation_hermes_acp":
            "set PANELS_REAL_HERMES_TESTS=1 with hermes installed to run this",
        "tests.integration.test_orchestrator_worker_real_lifecycle":
            "set PANELS_REAL_LUNA_LIFECYCLE=1 to run real provider calls",
        "tests.integration.test_conversation_real_provider_permissions":
            "set PANELS_REAL_PERMISSION_TESTS=1 for billable real-provider permission exercises",
        "tests.integration.test_conversation_real_provider_steering":
            "set PANELS_REAL_SYSTEM_STEERING_TESTS=1 to run real provider calls",
    }
    paid = "".join(
        f'<testcase classname="{module}" name="paid"><skipped message="{reason}"/></testcase>'
        for module, reason in opt_ins.items()
    )
    executed = '<testcase classname="tests.unit.test_one" name="ok"/>'
    report.write_text(f"<testsuite>{executed}{paid}</testsuite>")
    check_pytest(report, ["tests/unit/test_one.py"])

    # An opt-in still supplies no default proof, even when every skip is recognized.
    report.write_text(f"<testsuite>{paid}</testsuite>")
    with pytest.raises(ValueError, match="supplied no default coverage"):
        check_pytest(report, ["tests/unit/test_conversation_claude_real_cli.py"])

    # Module and reason must both match. Prefix collisions and missing tools stay fatal.
    for module, reason in (
        ("tests.unit.test_skipped", opt_ins["tests.unit.test_conversation_codex_real_cli"]),
        ("tests.unit.test_conversation_codex_real_cli_extra",
         opt_ins["tests.unit.test_conversation_codex_real_cli"]),
        ("tests.unit.test_conversation_codex_real_cli", "a different skip reason"),
        ("tests.unit.test_conversation_codex_bindings", "the pinned Codex binary is not installed"),
    ):
        report.write_text(
            f'<testsuite>{executed}<testcase classname="{module}" name="required">'
            f'<skipped message="{reason}"/></testcase></testsuite>'
        )
        with pytest.raises(ValueError, match="unexpected required skip"):
            check_pytest(report, ["tests/unit/test_one.py"])

    # Recognition never excuses an actual provider-test failure.
    report.write_text(
        f'<testsuite>{executed}<testcase '
        'classname="tests.unit.test_conversation_codex_real_cli" name="paid">'
        '<failure message="provider failed"/></testcase></testsuite>'
    )
    with pytest.raises(ValueError, match="failed test"):
        check_pytest(report, ["tests/unit/test_one.py"])


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
