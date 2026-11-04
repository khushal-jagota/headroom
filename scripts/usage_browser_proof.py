"""Connected proof of the actual Usage route and local API, without fixture usage."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def tokens(value: int) -> str:
    for divisor, suffix in [(10**12, "T"), (10**9, "B"), (10**6, "M"), (10**3, "K")]:
        if value >= divisor:
            scaled = value / divisor
            digits = 0 if scaled >= 100 else 1 if scaled >= 10 else 2
            text = f"{scaled:.{digits}f}"
            if "." in text and set(text.split(".")[1]) == {"0"}:
                text = text.split(".")[0]
            return text + suffix
    return f"{value:,}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    identity = json.loads(args.identity.read_text())
    base = f"http://127.0.0.1:{identity['port']}"
    args.output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    requests: list[dict[str, str]] = []
    receipts: list[dict[str, object]] = []
    started = time.perf_counter()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for size, width, height in [("desktop", 1440, 1050), ("compact", 390, 844)]:
            for tab in ["tokens", "cost", "limits"]:
                context = browser.new_context(
                    viewport={"width": width, "height": height}, reduced_motion="reduce"
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))

                def gate(route: object) -> None:
                    request = route.request
                    path = urlsplit(request.url).path
                    if "/api/" in path:
                        requests.append({"method": request.method, "path": path})
                    if (
                        path == "/api/conversation/usage/refresh"
                        or (
                            path.startswith("/api/conversation/backends")
                            and (
                                request.method != "GET"
                                or "refresh" in request.url
                                or "sign-in" in path
                            )
                        )
                        or (path.startswith("/api/updates") and request.method != "GET")
                    ):
                        errors.append(f"Unexpected action was blocked: {request.method} {path}")
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", gate)
                endpoint = "/limits" if tab == "limits" else "/summary"
                with page.expect_response(
                    lambda response, endpoint=endpoint: (
                        urlsplit(response.url).path == "/api/conversation/usage" + endpoint
                    ),
                    timeout=60000,
                ) as response:
                    page.goto(base + "/#/backends/" + tab)
                result = response.value.json()
                assert response.value.status == 200
                if tab == "limits":
                    windows = result["codex"]["windows"]
                    assert windows, "Real Codex allowance must exist"
                    expected = max(0, 100 - windows[0]["usedPercent"])
                    expectedText = f"{expected:g}%"
                    expect(page.locator("[data-usage-limits]")).to_contain_text(expectedText)
                    details = page.locator("[data-usage-limits] details").first
                    details.locator("summary").click()
                    if windows[0].get("resetsAt"):
                        expect(details).to_contain_text(windows[0]["resetsAt"])
                    value = {"codex": result["codex"], "claude": result.get("claude")}
                else:
                    count = sum(
                        sum(
                            bucket["totals"][key]
                            for key in [
                                "uncachedInputTokens",
                                "cachedInputTokens",
                                "cacheCreationTokens",
                                "outputTokens",
                            ]
                        )
                        for bucket in result["buckets"]
                    )
                    cost = sum(bucket["costUsd"] for bucket in result["buckets"])
                    assert count > 0 and cost > 0
                    expectedText = tokens(count) if tab == "tokens" else f"${cost:,.2f}*"
                    expect(page.locator("[data-usage-total]")).to_have_text(expectedText)
                    value = {
                        "tokens": count,
                        "costUsd": cost,
                        "records": sum(bucket["records"] for bucket in result["buckets"]),
                        "window": {
                            key: result[key] for key in ["sinceDay", "untilDay", "timeZone"]
                        },
                    }
                screenshot = f"{size}-{tab}.png"
                page.screenshot(path=str(args.output / screenshot), full_page=False)
                receipts.append(
                    {
                        "size": size,
                        "tab": tab,
                        "rendered": expectedText,
                        "actualApiValues": value,
                        "screenshot": screenshot,
                    }
                )
                context.close()
        browser.close()
    assert not errors, errors
    assert not (args.identity.parent / "provider-attempt.txt").exists()
    result = {
        "durationMs": (time.perf_counter() - started) * 1000,
        "actualUsageData": True,
        "fixtureUsage": False,
        "providerFactories": "deny",
        "nativeOrProviderActions": 0,
        "requests": requests,
        "pageErrors": errors,
        "views": receipts,
    }
    (args.output / "browser-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {"durationMs": result["durationMs"], "views": len(receipts), "pageErrors": errors}
        )
    )


if __name__ == "__main__":
    main()
