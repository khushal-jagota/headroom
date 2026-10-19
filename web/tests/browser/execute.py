"""Execute independent browser cases with one Chromium process and fresh contexts."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# Existing browser evidence helpers live in tests.e2e.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def main() -> None:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    receipt_path = Path(sys.argv[2])
    results = []
    timing = {"navigationSeconds": 0.0, "screenshotSeconds": 0.0,
              "fixedWaitSeconds": 0.0, "fixedWaitRequestedSeconds": 0.0,
              "navigations": 0, "screenshots": 0, "fixedWaits": 0}
    # Measure the same browser calls as the original rail probe. No wait changes.
    for method_name, seconds_key, count_key in (
        ("goto", "navigationSeconds", "navigations"),
        ("screenshot", "screenshotSeconds", "screenshots"),
        ("wait_for_timeout", "fixedWaitSeconds", "fixedWaits"),
    ):
        original = getattr(Page, method_name)

        def measured(self, *args, _original=original, _seconds=seconds_key,
                     _count=count_key, **kwargs):
            if _count == "fixedWaits":
                timing["fixedWaitRequestedSeconds"] += args[0] / 1000
            start = time.monotonic()
            try:
                return _original(self, *args, **kwargs)
            finally:
                timing[_seconds] += time.monotonic() - start
                timing[_count] += 1

        setattr(Page, method_name, measured)
    with sync_playwright() as playwright:
        start = time.monotonic()
        browser = playwright.chromium.launch(headless=True)
        launch_seconds = time.monotonic() - start
        try:
            for case in manifest["cases"]:
                before = dict(timing)
                start = time.monotonic()
                result = {"id": case["id"], "entry": case["entry"], "status": "failed"}
                try:
                    spec = importlib.util.spec_from_file_location(case["id"], case["python"])
                    if spec is None or spec.loader is None:
                        raise ValueError("Browser case has no loader")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.run(browser, case["urls"])
                    result["status"] = "passed"
                except Exception:
                    result["error"] = traceback.format_exc()
                    print(result["error"], file=sys.stderr)
                finally:
                    # new_page also creates a context. Explicit case contexts remain real.
                    for context in browser.contexts:
                        try:
                            context.close()
                        except Exception:
                            result["status"] = "failed"
                            result["error"] = traceback.format_exc()
                            print(result["error"], file=sys.stderr)
                    result["seconds"] = time.monotonic() - start
                    result["timing"] = {key: value - before[key] for key, value in timing.items()}
                    attributed = sum(result["timing"][key] for key in (
                        "navigationSeconds", "screenshotSeconds", "fixedWaitSeconds"))
                    result["timing"]["unattributedSeconds"] = result["seconds"] - attributed
                    results.append(result)
                    receipt_path.write_text(json.dumps(
                        {"cases": results, "browserStartupSeconds": launch_seconds}, indent=2))
                    print(f'{case["entry"]}/{case["id"]}: {result["status"]} '
                          f'({result["seconds"]:.2f}s)', flush=True)
        finally:
            browser.close()
    if not results or any(result["status"] != "passed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
