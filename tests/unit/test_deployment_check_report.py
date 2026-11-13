"""The release gate excludes only registered provider opt-in reasons."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

import pytest
from scripts.deployment_check_report import check_pytest


def test_cli_qualification_skips_require_exact_registered_reasons(tmp_path: Path) -> None:
    suite = ElementTree.Element("testsuite")
    ElementTree.SubElement(suite, "testcase", classname="tests.integration.test_required")
    module = "tests.integration.test_conversation_cli_compatibility"
    for provider in ("CODEX", "CLAUDE"):
        case = ElementTree.SubElement(suite, "testcase", classname=module)
        ElementTree.SubElement(
            case, "skipped",
            message=f"set PANELS_QUALIFY_{provider} to an absolute CLI executable path",
        )
    report = tmp_path / "integration.xml"
    tree = ElementTree.ElementTree(suite)
    tree.write(report)
    check_pytest(report, ["tests/integration/test_conversation_cli_compatibility.py"])

    skip = suite.findall("testcase")[1].find("skipped")
    assert skip is not None
    skip.set("message", "set PANELS_QUALIFY_CODEX to an absolute CLI executable path extra")
    tree.write(report)
    with pytest.raises(ValueError, match="unexpected required skip"):
        check_pytest(report, [])

    skip.set("message", "set PANELS_QUALIFY_CODEX to an absolute CLI executable path")
    suite.findall("testcase")[1].set("classname", "tests.integration.test_unlisted")
    tree.write(report)
    with pytest.raises(ValueError, match="unexpected required skip"):
        check_pytest(report, [])
