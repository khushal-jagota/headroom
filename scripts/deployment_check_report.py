"""Reject empty collection and skipped required proof in direct check reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree

# Only these exact paid-provider module/reason pairs can skip default coverage.
# Missing local tools and other required skips still fail the gate.
PROVIDER_SKIPS = {
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
    "tests.integration.test_conversation_real_credential_requests":
        "set PANELS_REAL_CREDENTIAL_REQUEST_TESTS=1 for billable native Sol high calls",
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
    provider_opt_ins = 0
    unavailable_macos_proofs = 0
    for case in cases:
        name = case.get("classname", "")
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"failed test in report: {name}")
        skip = case.find("skipped")
        if skip is None:
            executed += 1
            continue
        reason = skip.get("message", "")
        if (
            sys.platform == "linux"
            and name == "tests.unit.test_skill_guard_macos"
            and case.get("name")
            == "test_macos_directory_descriptor_resolves_private_and_protected_paths"
            and reason == "macOS F_GETPATH descriptor proof"
        ):
            # F_GETPATH is Mac-only. This exact Linux skip supplies no executed proof.
            unavailable_macos_proofs += 1
            print(f"Mac proof unavailable on Linux: {name}.{case.get('name')}: {reason}")
            continue
        if not any(
            (name == module or name.startswith(module + ".")) and reason == expected
            for module, expected in PROVIDER_SKIPS.items()
        ):
            raise ValueError(f"unexpected required skip: {name}: {reason}")
        provider_opt_ins += 1
        print(f"Provider opt-in excluded from default coverage: {name}: {reason}")
    if not executed:
        raise ValueError("selected pytest suite supplied no default coverage")
    print(f"Required pytest proof: {executed} executed, {provider_opt_ins} provider opt-ins, "
          f"{unavailable_macos_proofs} unavailable Mac proofs")


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


def check_browser(report: Path, selected: list[str], source_root: Path,
                  revision: str, invocation_id: str) -> None:
    """Require every requested-source catalog case and typed companion proof."""
    catalog = json.loads((source_root / "web/tests/browser/catalog.json").read_text())
    if catalog.get("schema") != 1 or not isinstance(catalog.get("entries"), list):
        raise ValueError("unsupported browser catalog")
    paths = [entry["path"] for entry in catalog["entries"]]
    entry_ids = [entry["id"] for entry in catalog["entries"]]
    identities = [case for entry in catalog["entries"] for case in entry["cases"]]
    if (not paths or len(set(paths)) != len(paths)
            or len(set(entry_ids)) != len(entry_ids) or any(not value for value in entry_ids)
            or len(set(identities)) != len(identities)
            or any(not entry["cases"] for entry in catalog["entries"])):
        raise ValueError("empty or duplicate browser catalog")
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(paths):
        raise ValueError("invalid browser selection")
    expected = {(entry["id"], case) for entry in catalog["entries"]
                if entry["path"] in selected for case in entry["cases"]}
    value = json.loads(report.read_text())
    if (value.get("schema") != 1 or value.get("outcome") != "passed"
            or value.get("revision") != revision
            or value.get("invocationId") != invocation_id
            or value.get("selected") != selected):
        raise ValueError("browser receipt selection, revision, or invocation mismatch")
    cases = value["cases"]
    observed = [(case["entry"], case["id"]) for case in cases]
    if (not cases or len(set(observed)) != len(observed) or set(observed) != expected
            or any(case.get("status") != "passed" for case in cases)):
        raise ValueError("browser proof is missing, duplicate, empty, failed, or skipped")
    required_companions = {file for entry in catalog["entries"]
                           if entry["path"] in selected for file in entry.get("companions", [])}
    if required_companions:
        companion = value["companion"]
        if (not isinstance(companion, dict) or companion.get("schema") != 1
                or companion.get("invocationId") != invocation_id
                or companion.get("revision") != revision
                or companion.get("outcome") != "passed"
                or companion.get("compiler") != "passed"):
            raise ValueError("missing or invalid typed browser companion")
        runtime = companion["runtime"]
        for required_file in required_companions:
            matches = [result for result in runtime if result["path"] == required_file]
            if len(matches) != 1 or not matches[0]["cases"]:
                raise ValueError("browser companion runtime file is absent or duplicated")
        companion_identities = [(result["path"], case["name"])
                      for result in runtime for case in result["cases"]]
        if (not companion_identities
                or len(set(companion_identities)) != len(companion_identities)
                or any(not result["cases"] for result in runtime)
                or any(case["status"] != "passed"
                       for result in runtime for case in result["cases"])):
            raise ValueError("browser companion runtime is absent, empty, failed, or skipped")
    print(f"Required browser proof: {len(cases)} independent cases executed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("pytest", "vitest", "browser"))
    parser.add_argument("report", type=Path)
    parser.add_argument("selected", nargs="+")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--invocation-id")
    args = parser.parse_args()
    if args.kind == "pytest":
        check_pytest(args.report, args.selected)
    elif args.kind == "vitest":
        check_vitest(args.report, args.selected)
    else:
        if not args.source_root or not args.revision or not args.invocation_id:
            parser.error("browser proof requires source root, revision, and invocation ID")
        check_browser(args.report, args.selected, args.source_root,
                      args.revision, args.invocation_id)


if __name__ == "__main__":
    main()
