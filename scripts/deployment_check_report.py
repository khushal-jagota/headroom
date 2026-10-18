"""Reject empty collection and skipped required proof in direct check reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree

# These paid-provider opt-ins do not supply default deployment coverage.
PROVIDER_SKIPS = {
    "tests.integration.test_orchestrator_worker_real_lifecycle":
        "set PANELS_REAL_LUNA_LIFECYCLE=1 to run real provider calls",
    "tests.integration.test_conversation_real_provider_permissions":
        "set PANELS_REAL_PERMISSION_TESTS=1 for billable real-provider permission exercises",
    "tests.integration.test_conversation_real_provider_steering":
        "set PANELS_REAL_SYSTEM_STEERING_TESTS=1 to run real provider calls",
}


def check_pytest(report: Path, selected: list[str]) -> None:
    cases = list(ElementTree.parse(report).iter("testcase"))
    if not cases:
        raise ValueError("selected pytest suite collected zero tests")
    for path in selected:
        module = path.split("::", 1)[0].removesuffix(".py").replace("/", ".")
        if not any(
            case.get("classname", "") == module
            or case.get("classname", "").startswith(module + ".")
            for case in cases
        ):
            raise ValueError(f"selected file collected no tests: {path}")
    executed = 0
    for case in cases:
        name = case.get("classname", "")
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"failed test in report: {name}")
        skip = case.find("skipped")
        if skip is None:
            executed += 1
            continue
        reason = skip.get("message", "")
        if not any(
            (name == module or name.startswith(module + ".")) and reason == expected
            for module, expected in PROVIDER_SKIPS.items()
        ):
            raise ValueError(f"unexpected required skip: {name}: {reason}")
        print(f"Provider opt-in excluded from default coverage: {name}: {reason}")
    if not executed:
        raise ValueError("selected pytest suite supplied no default coverage")
    print(f"Required pytest proof: {executed} executed, {len(cases) - executed} provider opt-ins")


def check_vitest(report: Path, selected: list[str]) -> None:
    value = json.loads(report.read_text())
    results = value["testResults"]
    for path in selected:
        matches = [result for result in results
                   if result["name"].endswith("/" + path.removeprefix("web/"))]
        if not matches:
            raise ValueError(f"selected frontend host collected no tests: {path}")
        for result in matches:
            cases = result["assertionResults"]
            if not cases or any(case["status"] != "passed" for case in cases):
                raise ValueError(f"frontend host is empty, failed, or skipped: {path}")
    assertions = [case for result in results for case in result["assertionResults"]]
    if not assertions or any(case["status"] != "passed" for case in assertions):
        raise ValueError("frontend proof is empty, failed, or skipped")
    if not value.get("success"):
        raise ValueError("frontend report did not succeed")
    print(f"Required frontend proof: {len(assertions)} executed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("pytest", "vitest"))
    parser.add_argument("report", type=Path)
    parser.add_argument("selected", nargs="+")
    args = parser.parse_args()
    if args.kind == "pytest":
        check_pytest(args.report, args.selected)
    else:
        check_vitest(args.report, args.selected)


if __name__ == "__main__":
    main()
