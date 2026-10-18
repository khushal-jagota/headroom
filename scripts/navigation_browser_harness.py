"""Real HTTP/SSE + TanStack/Svelte browser proof on a wholly synthetic runtime.

The rendered host is a harness comparator, never the original production UI baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from measure_navigation_delivery import distribution, isolate, seed
from playwright.async_api import async_playwright

from planner.conversation.storage import ConversationStore
from planner.core.db import connect
from planner.navigation import data as navigation
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.proof import capture_command_commits, response_fence


async def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.runtime.resolve()
    root.mkdir(parents=True, exist_ok=False)
    isolate(root)  # Strip every inherited PLAN_* before the first database open.
    path = root / "planner.db"
    seed(path)
    runtime = DeliveryRuntime(path)
    lock = threading.Lock()
    fanout_admitted = threading.Event()
    source_samples: list[dict[str, Any]] = []
    requests: dict[str, dict[str, int]] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        ownership = ConversationStore(str(path)).claim_runtime()
        try:
            with contextlib.closing(connect(str(path))) as conn:
                conn.execute("BEGIN IMMEDIATE")
                navigation.reset_epoch(conn)
                conn.commit()
            await runtime.start()
            yield
        finally:
            await runtime.close()
            ownership.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(runtime.router)

    @app.get("/api/harness/fanout-admitted")
    def fanout_status() -> dict[str, bool]:
        return {"admitted": fanout_admitted.is_set()}

    @app.post("/api/harness/change")
    def change(body: dict[str, Any]) -> dict[str, Any]:
        admitted = time.perf_counter_ns()
        with lock, capture_command_commits(str(path)) as capture:
            with contextlib.closing(connect(str(path))) as conn:
                conn.execute("BEGIN IMMEDIATE")
                tag = body["tag"]
                scenario = body.get("scenario", "owner")
                if scenario == "fanout":
                    fanout_admitted.set()
                if scenario == "reset":
                    navigation.reset_epoch(conn)
                elif scenario == "delete":
                    conn.execute("DELETE FROM projects WHERE id='measure_delete'")
                elif scenario == "no_op":
                    pass
                elif scenario in ("fanout", "large"):
                    text = tag
                    conn.execute("UPDATE tickets SET title=? || id", (text,))
                    if scenario == "large":
                        conn.execute(
                            "UPDATE projects SET name=? WHERE id='project_other'", ("x" * 600_000,)
                        )
                else:
                    conn.execute(
                        "UPDATE tickets SET title=? WHERE id IN "
                        "('t_measure_0000','t_measure_0001')",
                        (tag,),
                    )
                conn.commit()
                committed = time.perf_counter_ns()
                fence = response_fence(conn, capture)
                sample = {
                    "tag": tag,
                    "scenario": scenario,
                    "admitted_ns": admitted,
                    "commit_return_ns": committed,
                    "revision": fence["revision"],
                    "fence": fence,
                }
                source_samples.append(sample)
                return sample

    app.mount("/", StaticFiles(directory=args.static_dir), name="host")

    class MeasuredApp:
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            key = scope["method"] + " " + scope["path"]
            counter = requests.setdefault(key, {"requests": 0, "body_bytes": 0})
            counter["requests"] += 1

            async def measured_send(message: Any) -> None:
                if message["type"] == "http.response.body":
                    counter["body_bytes"] += len(message.get("body", b""))
                await send(message)

            await app(scope, receive, measured_send)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(MeasuredApp(), log_level="error", lifespan="on", timeout_graceful_shutdown=2)
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(45):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("server exited before startup")
                await asyncio.sleep(0.01)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            page.set_default_timeout(10000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            url = f"http://127.0.0.1:{port}/{args.index}"
            await page.goto(url)
            try:
                await page.wait_for_function("window.navigationHarness?.ready()")
                result = await exercise(page, requests)
                await page.screenshot(path=str(args.output.parent / "navigation-browser.png"))
            except Exception:
                print(
                    await page.evaluate(
                        "({html:document.body.innerHTML, "
                        "state:window.navigationHarness?.state?.(), "
                        "marks:window.navigationHarness?.renderMarks})"
                    ),
                    flush=True,
                )
                failure = await page.evaluate(
                    "({state:window.navigationHarness?.state?.(), "
                    "marks:window.navigationHarness?.renderMarks, html:document.body.innerHTML})"
                )
                args.output.with_suffix(".failure.json").write_text(
                    json.dumps(
                        {
                            "browser": failure,
                            "requests": requests,
                            "source_samples": source_samples,
                        },
                        indent=2,
                    )
                )
                raise
            finally:
                await browser.close()
            assert not errors, errors
            result["browser_errors"] = errors
        result.update(
            {
                "requests": requests,
                "source_samples": source_samples,
                "runtime": str(root),
                "database": str(path),
                "method": "Real lease/startup; HTTP snapshot/SSE; TanStack collection; "
                "Svelte selectors and DOM; server monotonic admission/commit to "
                "Python-observed browser DOM. Observation polling adds latency.",
                "limitations": [
                    "Synthetic harness comparator, not original browser baseline",
                    "No production UI parity or final speedup claim",
                    "Browser-reload trials retain the server/runtime, process and OS caches",
                    "Small samples do not establish sustained capacity",
                    "Transferred bytes are ASGI response body bytes, not network framing",
                ],
            }
        )
        return result
    finally:
        server.should_exit = True
        await serving
        listener.close()


async def exercise(page: Any, requests: dict[str, dict[str, int]]) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    checks: list[str] = []
    all_marks: list[dict[str, Any]] = []
    clocks: list[dict[str, float | int]] = []
    visibility_results: list[dict[str, Any]] = []

    async def calibrate() -> dict[str, float | int]:
        before = time.perf_counter_ns()
        browser_ms = await page.evaluate("performance.now()")
        after = time.perf_counter_ns()
        clock = {
            "offset_ns": (before + after) / 2 - browser_ms * 1e6,
            "uncertainty_ns": (after - before) / 2,
        }
        clocks.append(clock)
        return clock

    clock = await calibrate()
    costs: list[dict[str, Any]] = []

    def counters() -> dict[str, dict[str, int]]:
        return {key: value.copy() for key, value in requests.items()}

    async def change(tag: str, scenario: str = "owner", wait: bool = True) -> dict[str, Any]:
        before = counters()
        offered = time.perf_counter_ns()
        if scenario == "background":
            origin = "/".join(page.url.split("/")[:3])
            response = await page.request.post(
                origin + "/api/harness/change", data={"tag": tag, "scenario": scenario}
            )
            assert response.ok
            sample = await response.json()
        else:
            sample = await page.evaluate(
                "([tag,scenario]) => window.navigationHarness.change(tag,scenario)", [tag, scenario]
            )
        sample["offered_ns"] = offered
        if wait:
            await page.wait_for_function(
                "expected => { const node=document.querySelector('[data-revision]'); "
                "return node?.dataset.epoch === expected.epoch && "
                "BigInt(node.dataset.revision) >= BigInt(expected.revision); }",
                arg=sample["fence"],
            )
            sample["render_observed_ns"] = time.perf_counter_ns()
            marks = await page.evaluate("window.navigationHarness.renderMarks")
            relevant = [
                mark
                for mark in marks
                if mark["epoch"] == sample["fence"]["epoch"]
                and int(mark["revision"]) >= int(sample["revision"])
            ]
            if relevant and scenario != "no_op":
                sample["render_mark_ns"] = relevant[0]["rendered_ms"] * 1e6 + clock["offset_ns"]
                sample["clock_uncertainty_ns"] = clock["uncertainty_ns"]
        sample["cost_before"] = before
        sample["cost_after"] = counters()
        if wait and scenario in ("owner", "background") and tag.startswith("warm-"):
            origin = "/".join(page.url.split("/")[:3])
            refreshed_at = time.perf_counter_ns()
            refresh = await page.request.get(origin + "/api/navigation/snapshot")
            assert refresh.ok
            sample["snapshot_transfer_comparator"] = {
                "response_body_bytes": len(await refresh.body()),
                "requests": 1,
                "request_to_complete_body_ms": (time.perf_counter_ns() - refreshed_at) / 1e6,
                "label": "Backend snapshot refresh only, not original browser baseline",
            }
        samples.append(sample)
        return cast(dict[str, Any], sample)

    # The parent selects the latest publication while the child keeps its previous DOM.
    await page.evaluate("window.navigationHarness.holdRender(true)")
    delayed = await change("delayed-dom", wait=False)
    await page.wait_for_function(
        "revision => window.navigationHarness.publishedRevision() === revision",
        arg=delayed["revision"],
    )
    await page.wait_for_timeout(80)
    assert await page.evaluate("window.navigationHarness.waitStatus()") == "pending"
    assert await page.locator("[data-left]").inner_text() != "delayed-dom"
    await page.evaluate("window.navigationHarness.holdRender(false)")
    await page.wait_for_function("window.navigationHarness.waitStatus() === 'included'")
    assert await page.locator("[data-left]").inner_text() == "delayed-dom"
    assert await page.locator("[data-right]").inner_text() == "delayed-dom"
    checks.append("Command wait remains pending until actual related DOM records update")

    await page.evaluate("window.navigationHarness.holdPublication(true)")
    delayed = await change("delayed-publication", wait=False)
    await page.wait_for_timeout(80)
    assert await page.evaluate("window.navigationHarness.waitStatus()") == "pending"
    assert await page.locator("[data-left]").inner_text() == "delayed-dom"
    await page.evaluate("window.navigationHarness.holdPublication(false)")
    await page.wait_for_function("window.navigationHarness.waitStatus() === 'included'")
    assert await page.locator("[data-left]").inner_text() == "delayed-publication"
    checks.append("Delayed collection publication cannot satisfy visibility")

    await page.locator("[data-draft]").fill("local draft survives")
    await page.locator("[data-choice]").select_option("manual")
    identity = await page.evaluate("window.navigationHarness.ownerIdentity")
    route_streams_before = counters()["GET /api/navigation/events"]["requests"]
    for route in ("#/a", "#/b", "#/a"):
        old_consumer = await page.locator("[data-consumer]").get_attribute("data-consumer")
        await page.evaluate("route => location.hash = route", route)
        await page.wait_for_function(
            "old => document.querySelector('[data-consumer]').dataset.consumer !== old",
            arg=old_consumer,
        )
        await change("route-" + route)
    assert await page.evaluate("window.navigationHarness.ownerIdentity") == identity
    assert counters()["GET /api/navigation/events"]["requests"] == route_streams_before
    assert await page.locator("[data-draft]").input_value() == "local draft survives"
    assert await page.locator("[data-choice]").input_value() == "manual"
    checks.append("One owner survives route/pane changes and local draft/manual choice")

    await page.evaluate("window.navigationHarness.holdRender(true)")
    await change("unmount-pending", wait=False)
    await page.wait_for_timeout(30)
    await page.evaluate("location.hash = '#/removed-observer'")
    await page.wait_for_function("window.navigationHarness.waitStatus() === 'unknown'")
    await page.evaluate("window.navigationHarness.holdRender(false)")
    checks.append("Unmount of required rendered consumer releases pending wait as unknown")

    for i in range(10):
        await change(f"warm-{i}", "background" if i % 2 else "owner")
    before_burst = counters()
    burst_start = time.perf_counter_ns()
    tasks = []
    for i in range(20):
        await asyncio.sleep(max(0, (burst_start + i * 10_000_000 - time.perf_counter_ns()) / 1e9))
        tasks.append(asyncio.create_task(change(f"burst-{i}", "burst", wait=False)))
    burst = await asyncio.gather(*tasks)
    highest = max(burst, key=lambda s: int(s["revision"]))
    await page.wait_for_function(
        "revision => document.querySelector('[data-revision]').dataset.revision === revision",
        arg=highest["revision"],
    )
    burst_end = time.perf_counter_ns()
    burst_final_backlog = int(highest["revision"]) - int(
        await page.locator("[data-revision]").get_attribute("data-revision")
    )
    burst_marks = await page.evaluate("window.navigationHarness.renderMarks")
    for index, sample in enumerate(burst):
        sample["actual_task_start_ns"] = sample["offered_ns"]
        sample["offered_ns"] = burst_start + index * 10_000_000
        sample["burst_final_observed_ns"] = burst_end
        sample["coherent_render_marks_at_or_after_revision"] = [
            mark for mark in burst_marks if int(mark["revision"]) >= int(sample["revision"])
        ]
        relevant = sample["coherent_render_marks_at_or_after_revision"]
        if relevant:
            sample["render_mark_ns"] = relevant[0]["rendered_ms"] * 1e6 + clock["offset_ns"]
            sample["clock_uncertainty_ns"] = clock["uncertainty_ns"]
    for sample in burst:
        completed = [
            int(mark["revision"])
            for mark in burst_marks
            if mark["epoch"] == sample["fence"]["epoch"]
            and mark["rendered_ms"] * 1e6 + clock["offset_ns"] <= sample["commit_return_ns"]
        ]
        sample["pending_revisions_at_commit"] = int(sample["revision"]) - max(completed, default=0)
    costs.append({"scenario": "burst", "before": before_burst, "after": counters()})
    await page.evaluate("window.navigationHarness.disconnect()")
    reconnected = await change("disconnected", "reconnect", wait=False)
    await page.evaluate("window.navigationHarness.reconnect()")
    await page.wait_for_function(
        "document.querySelector('[data-left]').textContent === 'disconnected'"
    )
    reconnected["render_observed_ns"] = time.perf_counter_ns()
    reconnect_marks = await page.evaluate("window.navigationHarness.renderMarks")
    relevant = [
        mark
        for mark in reconnect_marks
        if mark["epoch"] == reconnected["fence"]["epoch"]
        and int(mark["revision"]) >= int(reconnected["revision"])
    ]
    assert relevant
    reconnected["render_mark_ns"] = relevant[0]["rendered_ms"] * 1e6 + clock["offset_ns"]
    reconnected["clock_uncertainty_ns"] = clock["uncertainty_ns"]
    checks.append("Reconnect resumes from published cursor and reaches coherent DOM")
    await change("noop", "no_op")
    await page.wait_for_function("window.navigationHarness.waitStatus() === 'no_op'")
    checks.append("Certified no-op remains distinct from a relevant change")
    await change("reset", "reset")
    before_large = counters()
    await change("large-", "large")
    after_large = counters()
    assert (
        after_large["GET /api/navigation/snapshot"]["requests"]
        > before_large["GET /api/navigation/snapshot"]["requests"]
    )
    costs.append({"scenario": "large", "before": before_large, "after": after_large})
    assert (await page.locator("[data-left]").inner_text()).startswith("large-")
    fanout = asyncio.create_task(change("fanout-", "fanout"))
    origin = "/".join(page.url.split("/")[:3])
    async with asyncio.timeout(5):
        while not (await (await page.request.get(origin + "/api/harness/fanout-admitted")).json())[
            "admitted"
        ]:
            await asyncio.sleep(0.001)
    foreground = asyncio.create_task(change("foreground", "foreground"))
    fanout_sample, foreground_sample = await asyncio.gather(fanout, foreground)
    assert (
        fanout_sample["admitted_ns"]
        < foreground_sample["admitted_ns"]
        < fanout_sample["commit_return_ns"]
    )
    await page.evaluate("window.navigationHarness.disconnect()")
    await change("deleted", "delete", wait=False)
    assert await page.evaluate("window.navigationHarness.hasDeletedProject()") is True
    await change("delete-reset", "reset", wait=False)
    await page.evaluate("window.navigationHarness.reconnect()")
    await page.wait_for_function("window.navigationHarness.hasDeletedProject() === false")
    assert await page.evaluate("window.navigationHarness.hasDeletedProject()") is False
    checks.append(
        "Epoch reset and oversized batch recover through full snapshot. "
        "Replacement removes absent record"
    )
    # Capture each page before reload destroys its DOM marks.
    all_marks.extend(await page.evaluate("window.navigationHarness.renderMarks"))
    visibility_results.extend(await page.evaluate("window.navigationHarness.state().waitResults"))
    for i in range(3):
        assert await page.evaluate("window.navigationHarness.coherenceErrors") == []
        await page.reload()
        await page.wait_for_function("window.navigationHarness?.ready()")
        clock = await calibrate()
        await change(f"browser-reload-{i}", "browser-reload")
        all_marks.extend(await page.evaluate("window.navigationHarness.renderMarks"))
        visibility_results.extend(
            await page.evaluate("window.navigationHarness.state().waitResults")
        )
    assert await page.evaluate("window.navigationHarness.coherenceErrors") == []
    marks = await page.evaluate("window.navigationHarness.renderMarks")
    observed = [s for s in samples if "render_observed_ns" in s]
    marked = [s for s in samples if "render_mark_ns" in s]
    for sample in samples:
        sample["cost_delta"] = {
            key: {
                metric: value - sample["cost_before"].get(key, {}).get(metric, 0)
                for metric, value in counter.items()
            }
            for key, counter in sample["cost_after"].items()
        }
    return {
        "checks": checks,
        "browser_memory": await page.evaluate(
            "performance.memory ? ({usedJSHeapSize:performance.memory.usedJSHeapSize,"
            "totalJSHeapSize:performance.memory.totalJSHeapSize,"
            "jsHeapSizeLimit:performance.memory.jsHeapSizeLimit}) : ({available:false})"
        ),
        "samples": samples,
        "final_render_marks": marks,
        "all_render_marks": all_marks,
        "workload_costs": costs,
        "burst_render_marks": burst_marks,
        "clock_calibrations": clocks,
        "visibility_results": visibility_results,
        "dom_mark_latency_ms": {
            scenario: {
                "offered_to_render": distribution(
                    [
                        (s["render_mark_ns"] - s["offered_ns"]) / 1e6
                        for s in marked
                        if s["scenario"] == scenario
                    ]
                ),
                "admission_to_render": distribution(
                    [
                        (s["render_mark_ns"] - s["admitted_ns"]) / 1e6
                        for s in marked
                        if s["scenario"] == scenario
                    ]
                ),
                "commit_to_render": distribution(
                    [
                        (s["render_mark_ns"] - s["commit_return_ns"]) / 1e6
                        for s in marked
                        if s["scenario"] == scenario
                    ]
                ),
            }
            for scenario in sorted({s["scenario"] for s in marked})
        },
        "background_method": "Background commits use an independent HTTP client. Owner commits "
        "use browser fetch and a command visibility wait. Both use the same isolated writer",
        "latency_ms": {
            scenario: {
                "offered_to_observed_dom": distribution(
                    [
                        (s["render_observed_ns"] - s["offered_ns"]) / 1e6
                        for s in observed
                        if s["scenario"] == scenario
                    ]
                ),
                "admission_to_observed_dom": distribution(
                    [
                        (s["render_observed_ns"] - s["admitted_ns"]) / 1e6
                        for s in observed
                        if s["scenario"] == scenario
                    ]
                ),
                "commit_to_observed_dom": distribution(
                    [
                        (s["render_observed_ns"] - s["commit_return_ns"]) / 1e6
                        for s in observed
                        if s["scenario"] == scenario
                    ]
                ),
            }
            for scenario in sorted({s["scenario"] for s in observed})
        },
        "burst": {
            "offered_count": 20,
            "offered_rate_per_second": 100,
            "achieved_to_last_dom_per_second": 20 / ((burst_end - burst_start) / 1e9),
            "final_backlog": max(0, burst_final_backlog),
            "max_pending_revisions_at_commit": max(s["pending_revisions_at_commit"] for s in burst),
        },
        "failures": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "checks": result["checks"], "failures": 0}))


if __name__ == "__main__":
    main()
