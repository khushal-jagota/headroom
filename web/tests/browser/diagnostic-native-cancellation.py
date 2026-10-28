"""Run premise controls directly, without the complete-case catalog protocol."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

EXPECTED_FAILURE = {
    "late-wheel": "SETUP_LATEST_WHEEL",
    "late-acceptance": "SETUP_LATEST_ACCEPTANCE",
    "broken-wheel": "BEHAVIOR_WHEEL_CANCELLATION",
    "broken-supersession": "BEHAVIOR_SEND_SUPERSESSION",
}


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    report_path = Path(sys.argv[2])
    spec = importlib.util.spec_from_file_location("native_cancellation_case", manifest["python"])
    assert spec is not None and spec.loader is not None
    case = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(case)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for selected in manifest["controls"]:
                control = selected["control"]
                evidence = Path(manifest["evidence"]) / control
                evidence.mkdir(parents=True, exist_ok=True)
                os.environ["PANELS_E2E_FAILURE_DIR"] = str(evidence)
                start = time.monotonic()
                failure = None
                result = {"control": control, "status": "failed"}
                try:
                    try:
                        case.run_retirement(browser, selected["urls"], control=control)
                    except AssertionError as error:
                        failure = str(error)
                    expected = EXPECTED_FAILURE.get(control)
                    if expected is None:
                        assert failure is None, f"Unexpected assertion: {failure}"
                    else:
                        assert failure is not None, f"Control did not reject: {expected}"
                        assert expected in failure, (
                            f"Wrong rejection, expected {expected}: {failure}"
                        )
                        assert "_NEW_ONLY" not in failure, (
                            f"Original behavioral assertions missed the mutation: {failure}"
                        )
                    proof_path = evidence / "reply-space-retirement-proof.json"
                    proof = json.loads(proof_path.read_text())
                    assert proof["nativeControl"]["control"] == control, "Missing diagnostic proof"
                    native = proof["nativeControl"]
                    if control in {"broken-wheel", "late-wheel"}:
                        wheel = next(
                            event for event in native["motion"] if event["kind"] == "wheel"
                        )
                        assert wheel["trusted"] is True, (
                            "Control did not deliver trusted wheel input"
                        )
                        assert wheel["valid"] is (control == "broken-wheel"), wheel
                    if control in {"broken-supersession", "late-acceptance"}:
                        acceptance = next(
                            event for event in native["events"] if event["kind"] == "acceptance"
                        )
                        assert acceptance["valid"] is (control == "broken-supersession"), acceptance
                    result.update(
                        status="passed",
                        expectedFailure=expected,
                        rejection=failure,
                        proof=str(proof_path),
                    )
                except Exception:
                    result["error"] = traceback.format_exc()
                    print(result["error"], file=sys.stderr)
                finally:
                    for context in browser.contexts:
                        context.close()
                    result["seconds"] = round(time.monotonic() - start, 3)
                    results.append(result)
                    report_path.write_text(
                        json.dumps(
                            {"diagnostic": "native-cancellation", "controls": results}, indent=2
                        )
                        + "\n"
                    )
                    print(
                        f"Diagnostic {control}: {result['status']} ({result['seconds']:.2f}s)",
                        flush=True,
                    )
                if result["status"] != "passed":
                    break
        finally:
            browser.close()
    if any(result["status"] != "passed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
