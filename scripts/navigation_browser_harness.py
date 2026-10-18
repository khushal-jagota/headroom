"""Measure the actual built App through its actual application lifespan.

Author/review this runner before use. Each invocation launches a fresh sanitized
child with the selected tree's interpreter. No provider or replacement UI is used.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from measure_navigation_delivery import (
    EVIDENCE_SCHEMA,
    assert_isolated,
    build_application,
    distribution,
    isolated_environment,
    rss_bytes,
)

# Returned as a private remote object, never installed on window or application state.
DOM_OBSERVER = r"""({ids}) => {
 const roots=[document.querySelector('.approved-rail'),
  document.querySelector('[data-orchestrator-children]')];
 if(roots.some(root=>!root)) throw Error('Actual workspace roots are absent');
 const marks=[];
 const read=()=>({at:performance.now(),connected:roots.every(root=>root.isConnected),
  rows:ids.map(id=>({id,rail:document.querySelector(`[data-ticket-id="${id}"]`)?.textContent?.trim()??null,
   children:document.querySelector(`[data-sprint-ticket-id="${id}"]`)?.textContent?.trim()??null}))});
 const observer=new MutationObserver(()=>marks.push(read()));
 for(const root of roots) observer.observe(root,
  {subtree:true,childList:true,characterData:true,attributes:true});
 return {read:()=>read(),marks:()=>marks.slice(),stop:()=>observer.disconnect()};
}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--runtime-parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tickets", type=int, default=500)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--cold-contexts", type=int, default=3)
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--browser-cache", type=Path)
    parser.add_argument(
        "--diagnostic-only",
        action="store_true",
        help="Separate instrumented cold CPU and bounded forced-GC heap attribution",
    )
    parser.add_argument(
        "--heap-snapshots",
        action="store_true",
        help="Write diagnostic before/after CDP heap snapshots",
    )
    parser.add_argument("--isolated-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.tickets < 3 or args.trials < 1 or args.cold_contexts < 1:
        parser.error("at least three Tickets and one warm/cold trial are required")
    return args


async def diagnose(
    browser: Any, args: argparse.Namespace, url: str, seed: dict[str, Any], source: Path
) -> dict[str, Any]:
    """Use the actual App, with external CDP instrumentation only.

    This mode does not enter the matched timing path. Forced GC and CPU sampling
    perturb execution and are explicitly diagnostic rather than production costs.
    """
    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")
        await cdp.send("Performance.enable")
        await cdp.send("HeapProfiler.enable")
        await cdp.send("Profiler.enable")
        await cdp.send("Profiler.setSamplingInterval", {"interval": 500})
        network: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        for event in ("requestWillBeSent", "responseReceived", "loadingFinished"):
            cdp.on(
                "Network." + event,
                lambda data, name=event: network.append({"event": name, "data": data}),
            )
        cdp.on("Tracing.dataCollected", lambda data: trace.extend(data["value"]))
        trace_done = asyncio.Event()
        cdp.on("Tracing.tracingComplete", lambda _data: trace_done.set())
        await cdp.send(
            "Tracing.start",
            {
                "categories": (
                    "devtools.timeline,v8,disabled-by-default-v8.compile,blink.user_timing"
                ),
                "options": "record-as-much-as-possible",
            },
        )
        await cdp.send("Profiler.start")
        started = time.perf_counter_ns()
        await page.goto(url + "#/workspace/item/" + seed["items"][0])
        await page.locator(f'[data-sprint-ticket-id="{seed["tickets"][0]}"]').wait_for(
            state="attached"
        )
        usable = time.perf_counter_ns()
        await page.wait_for_timeout(600)
        cpu = await cdp.send("Profiler.stop")
        await cdp.send("Tracing.end")
        await asyncio.wait_for(trace_done.wait(), 10)
        args.output.with_suffix(".cpu.json").write_text(json.dumps(cpu) + "\n")
        args.output.with_suffix(".cdp-trace.json").write_text(
            json.dumps({"traceEvents": trace}) + "\n"
        )
        resources = await page.evaluate("""() => ({
            navigation:performance.getEntriesByType('navigation').map(x=>x.toJSON()),
            resources:performance.getEntriesByType('resource').map(x=>x.toJSON())})""")
        initial_metrics = await cdp.send("Performance.getMetrics")
        bodies = []
        for network_event in network:
            if network_event["event"] != "responseReceived":
                continue
            data = network_event["data"]
            path = urlparse(data["response"]["url"]).path
            if path not in {"/api/navigation/snapshot", "/api/bootstrap"}:
                continue
            try:
                body = await cdp.send("Network.getResponseBody", {"requestId": data["requestId"]})
                raw = (
                    base64.b64decode(body["body"])
                    if body["base64Encoded"]
                    else body["body"].encode()
                )
                parsed = json.loads(raw)
                args.output.with_suffix(
                    ".initial-" + path.rsplit("/", 1)[-1] + ".json"
                ).write_bytes(raw)
                bodies.append(
                    {
                        "path": path,
                        "body_bytes": len(raw),
                        "top_level_keys": list(parsed),
                        "response": data["response"],
                    }
                )
            except Exception as error:
                bodies.append({"path": path, "unavailable": str(error)})

        async def heap_point(label: str, snapshot: bool = False) -> dict[str, Any]:
            await page.wait_for_timeout(600)
            quiet = await cdp.send("Performance.getMetrics")
            await cdp.send("HeapProfiler.collectGarbage")
            await page.wait_for_timeout(100)
            forced = await cdp.send("Performance.getMetrics")
            usage = await cdp.send("Runtime.getHeapUsage")
            if snapshot and args.heap_snapshots:
                chunks: list[str] = []

                def take_chunk(data: dict[str, Any]) -> None:
                    chunks.append(data["chunk"])

                cdp.on("HeapProfiler.addHeapSnapshotChunk", take_chunk)
                await cdp.send("HeapProfiler.takeHeapSnapshot", {"reportProgress": False})
                cdp.remove_listener("HeapProfiler.addHeapSnapshotChunk", take_chunk)
                args.output.with_suffix(f".{label}.heapsnapshot").write_text("".join(chunks))
            return {
                "label": label,
                "quiet_metrics": quiet,
                "forced_gc_metrics": forced,
                "heap_usage_after_gc": usage,
                "dom": await cdp.send("Memory.getDOMCounters"),
            }

        heap = [await heap_point("before", True)]
        workloads = []
        for batch in range(3):
            batch_start = time.perf_counter_ns()
            for index in range(20):
                # Constant row count and equal-length values bound retained state.
                tag = f"heap-cycle-{batch:02d}-{index:02d}"
                response = await page.request.post(
                    url + "api/harness/change", data={"scenario": "background", "tag": tag}
                )
                assert response.ok, await response.text()
                await page.wait_for_function(
                    '([id,tag])=>document.querySelector(`[data-ticket-id="${id}"]`)'
                    "?.textContent.includes(tag) && "
                    'document.querySelector(`[data-sprint-ticket-id="${id}"]`)'
                    "?.textContent.includes(tag)",
                    arg=[seed["tickets"][0], tag],
                )
            workloads.append(
                {
                    "batch": batch,
                    "writes": 20,
                    "started_ns": batch_start,
                    "ended_ns": time.perf_counter_ns(),
                }
            )
            heap.append(await heap_point(f"batch-{batch}", batch == 2))
        return {
            "diagnostic_only": True,
            "mode": args.mode,
            "source": str(source),
            "revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=source, text=True
            ).strip(),
            "source_dirty_state": subprocess.check_output(
                ["git", "status", "--short"], cwd=source, text=True
            ),
            "built_asset_hashes": {
                str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (source / "web/dist").rglob("*")
                if path.is_file()
            },
            "runner_hashes": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (
                    Path(__file__),
                    Path(__file__).with_name("measure_navigation_delivery.py"),
                )
            },
            "cold_started_ns": started,
            "cold_usable_ns": usable,
            "cold_metrics": initial_metrics,
            "resource_timings": resources,
            "initial_bodies": bodies,
            "network": network,
            "heap": heap,
            "workloads": workloads,
            "limits": [
                "Instrumented diagnostic only; CPU sampling/tracing and forced GC perturb costs.",
                "Fresh browser/process and synthetic runtime, warm OS/disk cache.",
                "Quiet points wait 600ms. Forced GC diagnoses retention, not production heap.",
                "Three 20-write batches bound observations; they cannot exclude slow leaks.",
            ],
        }
    finally:
        await context.close()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    root = args.runtime.resolve()
    source = args.source_root.resolve()
    isolated_paths = assert_isolated(root, source)  # First application import in clean child.
    # The selected interpreter owns planner; the source root owns its existing
    # fixtures. Script execution otherwise puts only scripts/ on sys.path.
    sys.path.insert(0, str(source))
    import uvicorn
    from playwright.async_api import async_playwright

    db = importlib.import_module("planner.core.db")
    requests: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    writer_lock = threading.Lock()
    app, fixture = build_application(root, source, args.mode, args.tickets)

    @app.post("/api/harness/change")
    def source_change(body: dict[str, Any]) -> dict[str, Any]:
        # Background writes deliberately use the retained source connection. They
        # change ordinary source facts, never Signal/public authority records.
        started = time.perf_counter_ns()
        ids = fixture["seed"]["tickets"]
        scenario, tag = body["scenario"], body["tag"]
        with writer_lock, contextlib.closing(db.connect(str(fixture["path"]))) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if scenario in {"fanout", "large"}:
                conn.execute("UPDATE tickets SET title=?", (tag,))
            else:
                conn.execute("UPDATE tickets SET title=? WHERE id IN (?,?)", (tag, *ids[:2]))
            conn.commit()
            committed = time.perf_counter_ns()  # First operation after actual commit returns.
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='navigation_meta'"
            ).fetchone()
            continuity = (
                None
                if row is None
                else tuple(conn.execute("SELECT epoch,revision FROM navigation_meta").fetchone())
            )
        result = {
            "scenario": scenario,
            "tag": tag,
            "started_ns": started,
            "commit_return_ns": committed,
            "continuity": continuity,
        }
        writes.append(result)
        return result

    @app.post("/api/harness/signal/{index}/{action}")
    async def signal(index: int, action: str) -> dict[str, Any]:
        runtime = app.state.conversation
        ticket_id = fixture["seed"]["tickets"][index]
        contracts = importlib.import_module("planner.conversation.contracts")
        content = importlib.import_module("planner.conversation.message_content")
        tickets = importlib.import_module("planner.tickets.data")
        principals = importlib.import_module("planner.core.contracts")
        with contextlib.closing(db.connect(str(fixture["path"]))) as conn:
            ticket = tickets.read_ticket(conn, ticket_id)
        conversation_id = ticket.conversation_id
        if action == "start":
            if conversation_id is None:
                conversation_id = f"measured-active-conversation-{index}"
                await runtime.system.start_conversation(
                    contracts.ConversationStartRequest(
                        conversation_id=conversation_id,
                        backend_key=contracts.ConversationBackendKey(ticket.employee_backend),
                        model=ticket.employee_launch_model,
                        workspace_folder=root / "workspace",
                    )
                )
                with contextlib.closing(db.connect(str(fixture["path"]))) as conn:
                    tickets.write_ticket_conversation_start(
                        conn,
                        ticket_id,
                        conversation_id=conversation_id,
                        backend=ticket.employee_backend,
                        model=ticket.employee_launch_model,
                        reasoning_effort=ticket.employee_launch_reasoning_effort,
                        now=5000,
                    )
            await runtime.system.send(
                conversation_id,
                content.text_message_content("Measured turn"),
                sender_label="owner",
                reply_requested=False,
            )
        elif action == "finish":
            await fixture["children"][conversation_id].finish()
        elif action == "message":
            await runtime.system.record_message_to_owner(
                conversation_id,
                content.text_message_content("Measured explicit unread message"),
                sender_label="Synthetic worker",
                sender=principals.Principal(principals.PrincipalKind.ticket, ticket_id),
                recipient=principals.OWNER_PRINCIPAL,
            )
        elif action == "invalidate" and args.mode == "candidate":
            runtime.store.signal_coordinator.invalidate("publication_failed")
        else:
            raise ValueError("unsupported controlled action")
        await runtime.system.wait_until_quiescent()
        return {"conversation_id": conversation_id, "action": action}

    class MeasuredApp:
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            item = {
                "method": scope["method"],
                "path": scope["path"],
                "started_ns": time.perf_counter_ns(),
                "body_bytes": 0,
                "chunks": [],
                "status": None,
            }
            requests.append(item)

            async def measured_send(message: Any) -> None:
                if message["type"] == "http.response.start":
                    item["status"] = message["status"]
                elif message["type"] == "http.response.body":
                    size = len(message.get("body", b""))
                    item["body_bytes"] += size
                    item["chunks"].append({"at_ns": time.perf_counter_ns(), "bytes": size})
                await send(message)

            await app(scope, receive, measured_send)

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))  # Reserve a surveyed available port atomically.
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            MeasuredApp(),
            log_level="error",
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    network: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    browser: Any = None
    context: Any = None
    try:
        async with asyncio.timeout(60):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("actual application failed before startup")
                await asyncio.sleep(0.01)
        seed = fixture["seed"]
        ids = seed["tickets"]
        url = f"http://127.0.0.1:{port}/"
        async with async_playwright() as playwright:
            launch: dict[str, Any] = {"headless": True}
            if args.browser_executable:
                launch["executable_path"] = str(args.browser_executable)
            browser = await playwright.chromium.launch(**launch)
            if args.diagnostic_only:
                diagnostic = await diagnose(browser, args, url, seed, source)
                diagnostic["isolated_paths"] = isolated_paths
                return diagnostic
            cold: list[dict[str, Any]] = []
            for index in range(args.cold_contexts):
                cold_context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                cold_page = await cold_context.new_page()
                cdp = await cold_context.new_cdp_session(cold_page)
                await cdp.send("Network.enable")
                for event in (
                    "requestWillBeSent",
                    "responseReceived",
                    "dataReceived",
                    "loadingFinished",
                    "loadingFailed",
                    "eventSourceMessageReceived",
                ):
                    cdp.on(
                        "Network." + event,
                        lambda data, name=event, phase=f"cold-{index}": network.append(
                            {
                                "phase": phase,
                                "event": name,
                                "observed_ns": time.perf_counter_ns(),
                                "data": data,
                            }
                        ),
                    )
                started = time.perf_counter_ns()
                await cold_page.goto(url + "#/workspace/item/" + seed["items"][0])
                await cold_page.locator(
                    "[data-sprint-item-workspace] .sprint-workspace-title"
                ).wait_for(state="visible")
                await cold_page.locator(f'[data-sprint-ticket-id="{ids[0]}"]').wait_for(
                    state="attached"
                )
                cold.append(
                    {
                        "started_ns": started,
                        "usable_ns": time.perf_counter_ns(),
                        "label": "fresh browser context, warm backend/process/OS cache",
                    }
                )
                await cold_context.close()
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            cdp = await context.new_cdp_session(page)
            await cdp.send("Network.enable")
            await cdp.send("Performance.enable")
            for event in (
                "requestWillBeSent",
                "responseReceived",
                "dataReceived",
                "loadingFinished",
                "loadingFailed",
                "eventSourceMessageReceived",
            ):
                cdp.on(
                    "Network." + event,
                    lambda data, name=event: network.append(
                        {
                            "phase": "warm",
                            "event": name,
                            "observed_ns": time.perf_counter_ns(),
                            "data": data,
                        }
                    ),
                )
            await page.goto(url + "#/workspace/item/" + seed["items"][0])
            await page.locator(f'[data-sprint-ticket-id="{ids[0]}"]').wait_for(state="attached")
            await page.locator("details[data-workspace-group]").evaluate_all(
                "nodes => nodes.forEach(node => { node.open=true; })"
            )
            observer = await page.evaluate_handle(DOM_OBSERVER, {"ids": ids[:2]})
            clocks = []
            for _index in range(5):
                before = time.perf_counter_ns()
                mark = await page.evaluate("performance.now()")
                after = time.perf_counter_ns()
                clocks.append(
                    {
                        "offset_ns": (before + after) / 2 - mark * 1e6,
                        "uncertainty_ns": (after - before) / 2,
                    }
                )
            clock = min(clocks, key=lambda item: item["uncertainty_ns"])

            async def change(
                tag: str, scenario: str = "background", wait: bool = True
            ) -> dict[str, Any]:
                response = await page.request.post(
                    url + "api/harness/change", data={"scenario": scenario, "tag": tag}
                )
                assert response.ok, await response.text()
                sample = await response.json()
                if wait:
                    await page.wait_for_function(
                        "([ids,tag]) => ids.every(id => "
                        '[`[data-ticket-id="${id}"]`,`[data-sprint-ticket-id="${id}"]`].every('
                        "selector=>document.querySelector(selector)?.textContent?.includes(tag)))",
                        arg=[ids[:2], tag],
                    )
                    sample["dom_observed_ns"] = time.perf_counter_ns()
                    marks = await observer.evaluate("observer => observer.marks()")
                    matched = [
                        row
                        for row in marks
                        if row["connected"]
                        and all(
                            tag in (entry["rail"] or "") and tag in (entry["children"] or "")
                            for entry in row["rows"]
                        )
                    ]
                    if matched:
                        sample["dom_mark_ns"] = matched[0]["at"] * 1e6 + clock["offset_ns"]
                        sample["clock_uncertainty_ns"] = clock["uncertainty_ns"]
                samples.append(sample)
                return cast(dict[str, Any], sample)

            old_cursor = None
            if args.mode == "candidate":
                snapshot_response = await page.request.get(url + "api/navigation/snapshot")
                assert snapshot_response.ok, await snapshot_response.text()
                old_cursor = (await snapshot_response.json())["cursor"]
            paired_window_started_ns = time.perf_counter_ns()
            for index in range(args.trials):
                await change(f"paired-warm-{index:03d}")
            ui_started = time.perf_counter_ns()
            item_path = "/api/items/" + seed["items"][0]
            async with page.expect_response(
                lambda response: (
                    urlparse(response.url).path == item_path and response.request.method == "PATCH"
                )
            ) as pending_ui_response:
                editor = page.locator(".sprint-workspace-title [contenteditable=true]")
                await editor.fill("Measured Alpha edited in actual App")
                await editor.press("Enter")
            ui_response = await pending_ui_response.value
            assert ui_response.ok, await ui_response.text()
            await page.wait_for_function(
                "(id)=>document.querySelector("
                '`[data-sprint-item="${id}"] .item-title`)?.textContent === '
                '"Measured Alpha edited in actual App" && document.querySelector('
                '".sprint-workspace-title [contenteditable=true]")?.textContent === '
                '"Measured Alpha edited in actual App"',
                arg=seed["items"][0],
            )
            ui_fence = ui_response.headers.get("panels-navigation-fence")
            if args.mode == "candidate":
                assert ui_fence is not None
            samples.append(
                {
                    "scenario": "owner-ui",
                    "action_started_ns": ui_started,
                    "dom_observed_ns": time.perf_counter_ns(),
                    "navigation_fence": ui_fence,
                    "note": "Actual InlineEdit Enter/blur command to connected pane+rail DOM; "
                    "not a precise owner commit timestamp or private visibility result.",
                }
            )
            for index in range(args.trials):
                owner_tag = f"paired-owner-edit-{index:03d}"
                owner_started = time.perf_counter_ns()
                owner_response = await page.request.patch(
                    url + "api/tickets/" + ids[0], data={"title": owner_tag}
                )
                assert owner_response.ok, await owner_response.text()
                await page.wait_for_function(
                    '([id,tag])=>document.querySelector(`[data-ticket-id="${id}"]`)'
                    "?.textContent.includes(tag) && "
                    'document.querySelector(`[data-sprint-ticket-id="${id}"]`)'
                    "?.textContent.includes(tag)",
                    arg=[ids[0], owner_tag],
                )
                samples.append(
                    {
                        "scenario": "owner-api",
                        "tag": owner_tag,
                        "request_started_ns": owner_started,
                        "dom_observed_ns": time.perf_counter_ns(),
                        "note": (
                            "Request-to-DOM interval, not an instrumented owner commit timestamp."
                        ),
                        "navigation_fence": owner_response.headers.get("panels-navigation-fence"),
                    }
                )
            # One source transaction changes every row. The observer retains the
            # actual mounted roots; no harness spans stand in for application DOM.
            await change("paired-fanout", "fanout")
            await change("paired-large-" + '界🙂"\\\n\t' * 19 + "終", "large")
            burst_started_ns = time.perf_counter_ns()
            burst = [
                asyncio.create_task(change(f"paired-burst-{i:03d}", wait=False)) for i in range(5)
            ]
            burst_samples = await asyncio.gather(*burst)
            latest = max(burst_samples, key=lambda row: row["commit_return_ns"])
            await page.wait_for_function(
                "([ids,tag])=>ids.every(id=>"
                '[`[data-ticket-id="${id}"]`,`[data-sprint-ticket-id="${id}"]`].every('
                "selector=>document.querySelector(selector)?.textContent?.includes(tag)))",
                arg=[ids[:2], latest["tag"]],
            )
            burst_converged_ns = time.perf_counter_ns()
            burst_dom = await observer.evaluate("observer=>observer.read()")
            assert burst_dom["connected"]
            assert all(
                latest["tag"] in (row["rail"] or "") and latest["tag"] in (row["children"] or "")
                for row in burst_dom["rows"]
            )
            samples.append(
                {
                    "scenario": "burst-convergence",
                    "writes": len(burst_samples),
                    "burst_started_ns": burst_started_ns,
                    "final_commit_return_ns": latest["commit_return_ns"],
                    "converged_ns": burst_converged_ns,
                    "latest_tag": latest["tag"],
                    "connected_dom": burst_dom,
                    "achieved_commits_per_second": len(burst_samples)
                    * 1e9
                    / (latest["commit_return_ns"] - burst_started_ns),
                }
            )
            import httpx

            await context.set_offline(True)
            reconnect_started = time.perf_counter_ns()
            async with httpx.AsyncClient(trust_env=False) as external_writer:
                response = await external_writer.post(
                    url + "api/harness/change",
                    json={"scenario": "background", "tag": "paired-offline-update"},
                )
                response.raise_for_status()
                reconnect_write = response.json()
            online_started = time.perf_counter_ns()
            await context.set_offline(False)
            await page.wait_for_function(
                '(id)=>document.querySelector(`[data-ticket-id="${id}"]`)'
                '?.textContent.includes("paired-offline-update") && '
                'document.querySelector(`[data-sprint-ticket-id="${id}"]`)'
                '?.textContent.includes("paired-offline-update")',
                arg=ids[0],
            )
            samples.append(
                {
                    **reconnect_write,
                    "scenario": "reconnect",
                    "offline_started_ns": reconnect_started,
                    "online_started_ns": online_started,
                    "dom_observed_ns": time.perf_counter_ns(),
                }
            )
            manual = page.locator('select[aria-label="Worker state"]')
            await manual.select_option("active")
            await page.locator("details[data-workspace-group]").first.evaluate(
                "node=>{node.open=false}"
            )
            await change("manual-choice-update", wait=False)
            await page.wait_for_function(
                '(tag)=>document.querySelector(".approved-rail")?.textContent.includes(tag)',
                arg="manual-choice-update",
            )
            assert not await page.locator("details[data-workspace-group]").first.evaluate(
                "node=>node.open"
            )
            await page.locator("details[data-workspace-group]").first.evaluate(
                "node=>{node.open=true}"
            )
            assert (await observer.evaluate("observer=>observer.read()"))["connected"]
            await observer.evaluate("observer=>observer.stop()")
            paired_window_ended_ns = time.perf_counter_ns()
            paired_memory = {
                "browser": await cdp.send("Performance.getMetrics"),
                "python_app_runner_rss_bytes": rss_bytes(os.getpid()),
            }
            browser_cdp = await browser.new_browser_cdp_session()
            paired_processes = await browser_cdp.send("SystemInfo.getProcessInfo")
            for process in paired_processes["processInfo"]:
                process["rss_bytes"] = rss_bytes(process["id"])
            paired_memory["processes"] = paired_processes
            # Exercise actual route/draft state without a product debug accessor.
            await page.evaluate(
                "(hash)=>{location.hash=hash}",
                "#/workspace/item/" + seed["items"][0] + "/" + ids[0],
            )
            await page.locator('[data-screen="ticket"]').wait_for()
            draft = page.locator("[data-conversation-composer] textarea").first
            draft_check: dict[str, Any] = {
                "supported": False,
                "reason": "No editable actual composer exists for this fixture configuration.",
            }
            if await draft.count() and await draft.is_enabled():
                await draft.fill("Measured local draft")
                response = await page.request.patch(
                    url + "api/tickets/" + ids[1], data={"title": "draft-preservation-update"}
                )
                assert response.ok, await response.text()
                await page.wait_for_function(
                    "(id)=>document.querySelector(`.approved-rail "
                    '[data-ticket-id="${id}"]`)?.textContent.includes("draft-preservation-update")',
                    arg=ids[1],
                )
                assert await draft.input_value() == "Measured local draft"
                draft_check = {"supported": True, "preserved_after_background_commit": True}
            await page.evaluate(
                "(hash)=>{location.hash=hash}", "#/workspace/item/" + seed["items"][0]
            )
            await page.locator("[data-orchestrator-children]").wait_for()
            # Actual runtime facts originate at the controlled backend wire.
            signal_response = await page.request.post(url + "api/harness/signal/0/start")
            assert signal_response.ok, await signal_response.text()
            signal_identity = (await signal_response.json())["conversation_id"]
            await page.locator(
                f'[data-conversation-id="{signal_identity}"] [data-conversation-signal="working"]'
            ).first.wait_for()
            second_start = await page.request.post(url + "api/harness/signal/1/start")
            assert second_start.ok, await second_start.text()
            unread_identity = (await second_start.json())["conversation_id"]
            response = await page.request.post(url + "api/harness/signal/1/finish")
            assert response.ok, await response.text()
            response = await page.request.post(url + "api/harness/signal/1/message")
            assert response.ok, await response.text()
            await page.locator(
                f'[data-conversation-id="{unread_identity}"] [data-conversation-signal="message"]'
            ).first.wait_for()
            degraded = None
            stale_cursor_response = None
            if args.mode == "candidate":
                response = await page.request.post(url + "api/harness/signal/0/invalidate")
                assert response.ok, await response.text()
                await page.wait_for_function(
                    "(id)=>!document.querySelector("
                    '`[data-conversation-id="${id}"] [data-conversation-signal="working"]`)',
                    arg=signal_identity,
                )
                await page.locator(
                    f'[data-conversation-id="{unread_identity}"] '
                    '[data-conversation-signal="message"]'
                ).first.wait_for()
                degraded_response = await page.request.get(url + "api/navigation/snapshot")
                degraded = await degraded_response.json()
                assert degraded.get("mode") == "degraded"
            # Stop the actual application lifetime and reconnect the same page to
            # a fresh actual owner. The private binding is not reached or replaced.
            restart_started = time.perf_counter_ns()
            server.should_exit = True
            await serving
            listener.close()
            app, fixture = build_application(root, source, args.mode, args.tickets)
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            listener.setblocking(False)
            server = uvicorn.Server(
                uvicorn.Config(
                    MeasuredApp(),
                    log_level="error",
                    access_log=False,
                    lifespan="on",
                    timeout_graceful_shutdown=2,
                )
            )
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            async with asyncio.timeout(60):
                while not server.started:
                    if serving.done():
                        await serving
                        raise RuntimeError("actual application failed during restart")
                    await asyncio.sleep(0.01)
            if args.mode == "candidate":
                async with httpx.AsyncClient(trust_env=False) as check:
                    response = await check.get(url + "api/navigation/snapshot")
                    response.raise_for_status()
                    recovered_snapshot = response.json()
                    assert recovered_snapshot["mode"] == "current"
                    stale_cursor_response = await check.get(
                        url + "api/navigation/events", params={"cursor": old_cursor}, timeout=10
                    )
                    stale_cursor_response.raise_for_status()
                    assert "event: reset" in stale_cursor_response.text
                new_epoch = recovered_snapshot["snapshot"]["epoch"]

                def browser_requested_current_stream() -> bool:
                    for event in network:
                        if event["event"] != "requestWillBeSent":
                            continue
                        request_url = event["data"]["request"]["url"]
                        parsed = urlparse(request_url)
                        if parsed.path != "/api/navigation/events":
                            continue
                        cursor = parse_qs(parsed.query).get("cursor", [""])[0]
                        try:
                            value = json.loads(
                                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                            )
                        except (ValueError, UnicodeError):
                            continue
                        if isinstance(value, dict) and value.get("epoch") == new_epoch:
                            return True
                    return False

                async with asyncio.timeout(30):
                    while not browser_requested_current_stream():
                        await asyncio.sleep(0.01)
                await page.wait_for_function(
                    "(id)=>!document.querySelector("
                    '`[data-conversation-id="${id}"] [data-conversation-signal="working"]`)',
                    arg=signal_identity,
                )
            await page.locator(
                f'[data-conversation-id="{unread_identity}"] [data-conversation-signal="message"]'
            ).first.wait_for()
            restart_observed = time.perf_counter_ns()
            # Move and delete an unrelated third row through canonical owner
            # commands, and check both actually connected parent panes.
            moved_id = ids[-1]
            parent_response = await page.request.patch(
                url + "api/tickets/" + moved_id,
                data={"orchestrator": {"kind": "sprint_item", "id": seed["items"][1]}},
            )
            assert parent_response.ok, await parent_response.text()
            await page.locator(f'[data-sprint-ticket-id="{moved_id}"]').wait_for(state="detached")
            await page.evaluate(
                "hash=>{location.hash=hash}", "#/workspace/item/" + seed["items"][1]
            )
            await page.locator(f'[data-sprint-ticket-id="{moved_id}"]').wait_for(state="attached")
            deletion = await page.request.delete(url + "api/tickets/" + moved_id)
            assert deletion.ok, await deletion.text()
            await page.locator(f'[data-sprint-ticket-id="{moved_id}"]').wait_for(state="detached")
            await page.locator(f'[data-ticket-id="{moved_id}"]').wait_for(state="detached")
            await page.evaluate(
                "hash=>{location.hash=hash}", "#/workspace/item/" + seed["items"][0]
            )
            await page.locator(f'[data-sprint-ticket-id="{ids[0]}"]').wait_for(state="attached")
            samples.append({"scenario": "parent-move-and-deletion", "connected_panes": True})
            displayed = await page.locator("[data-sprint-ticket-id]").evaluate_all(
                "nodes=>nodes.map(node=>({id:node.dataset.sprintTicketId,"
                'title:node.querySelector(".ticket-row-title")?.textContent}))'
            )
            import sqlite3

            with sqlite3.connect(fixture["path"].as_uri() + "?mode=ro", uri=True) as read_source:
                canonical = dict(read_source.execute("SELECT id,title FROM tickets"))
            assert len(displayed) == len(ids) - 1
            assert all(canonical[row["id"]] == row["title"] for row in displayed)
            memory = await cdp.send("Performance.getMetrics")
            process_memory: Any = None
            try:
                browser_cdp = await browser.new_browser_cdp_session()
                process_memory = await browser_cdp.send("SystemInfo.getProcessInfo")
                for process in process_memory["processInfo"]:
                    process["rss_bytes"] = rss_bytes(process["id"])
            except Exception as error:
                process_memory = {"unavailable": type(error).__name__}
            await page.screenshot(path=str(args.output.with_suffix(".png")), full_page=True)
            assert not errors, errors
            result = {
                "cold_contexts": cold,
                "paired_cost_window_ns": [paired_window_started_ns, paired_window_ended_ns],
                "draft_check": draft_check,
                "paired_memory": paired_memory,
                "final_canonical_dom_equality": {"rows": len(displayed), "equal": True},
                "clocks": clocks,
                "samples": samples,
                "degraded": degraded,
                "browser_errors": errors,
                "browser_performance_metrics": memory,
                "process_memory": process_memory,
                "python_app_runner_rss_bytes": rss_bytes(os.getpid()),
                "final_connected_dom": await page.locator(".board-screen").inner_text(),
                "stale_cursor_reset": (
                    None if stale_cursor_response is None else stale_cursor_response.text
                ),
                "restart": {
                    "started_ns": restart_started,
                    "dom_observed_ns": restart_observed,
                    "kind": "actual application restart, same Python process and browser page",
                },
                "signal_conversations": {
                    "working": signal_identity,
                    "durable_unread": unread_identity,
                },
            }
            await context.tracing.stop(path=str(args.output.with_suffix(".trace.zip")))
            await context.close()
            context = None
            await browser.close()
            browser = None
        return {
            "evidence_schema_version": 1,
            **result,
            "source": str(source),
            "mode": args.mode,
            "revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=source, text=True
            ).strip(),
            "source_dirty_state": subprocess.check_output(
                ["git", "status", "--short"], cwd=source, text=True
            ),
            "source_hashes": {
                str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for directory in (source / "src", source / "web/src")
                for path in directory.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            },
            "dependency_hashes": {
                str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (source / "requirements.txt", source / "web/package-lock.json")
            },
            "runner_hashes": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (
                    Path(__file__),
                    Path(__file__).with_name("measure_navigation_delivery.py"),
                )
            },
            "built_asset_hashes": {
                str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (source / "web/dist").rglob("*")
                if path.is_file()
            },
            "isolated_paths": isolated_paths,
            "seed": seed,
            "requests": requests,
            "network": network,
            "writes": writes,
            "commit_to_dom_ms": distribution(
                [
                    (sample["dom_mark_ns"] - sample["commit_return_ns"]) / 1e6
                    for sample in samples
                    if "dom_mark_ns" in sample
                ]
            ),
            "limits": [
                (
                    "Actual production DOM and application collection path, no "
                    "private collection timestamp."
                ),
                "Fresh browser contexts retain backend/process/OS caches; no OS-cold claim.",
                (
                    "Harness writer lock correlates commit/revision; "
                    "write time includes admission wait."
                ),
                (
                    "CDP dataReceived retains ongoing SSE encoded chunks; "
                    "loadingFinished also includes completed requests. "
                    "Do not add overlapping counters."
                ),
                (
                    "ASGI body bytes exclude HTTP/TCP framing; Python RSS includes "
                    "runner instrumentation."
                ),
                (
                    "Active Signal uses real runtime with controlled backend "
                    "child/probe adapters, not a real provider."
                ),
                (
                    "No in-process recovery claim: actual application does not "
                    "expose its lifetime-owned binding for test-driven reinstall."
                ),
                (
                    "No frozen/delayed private collection or visibility-wait "
                    "settlement claim; existing focused hosts cover those boundaries."
                ),
            ],
        }
    finally:
        if context is not None:
            with contextlib.suppress(Exception):
                await context.tracing.stop(path=str(args.output.with_suffix(".failure.trace.zip")))
                await context.close()
        if browser is not None:
            await browser.close()
        server.should_exit = True
        await serving
        listener.close()
        args.output.with_suffix(".raw.json").write_text(
            json.dumps(
                {"network": network, "requests": requests, "writes": writes, "samples": samples},
                indent=2,
            )
            + "\n"
        )


def main() -> None:
    args = parse_args()
    args.source_root = args.source_root.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.isolated_child:
        args.runtime_parent.mkdir(parents=True, exist_ok=True)
        if args.mode == "baseline":
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=args.source_root, text=True
            ).strip()
            if revision != "f0977a512ba9a6bb1736e15ee9760aa39989a0fc":
                raise ValueError("Baseline source differs from the selected immutable source")
        root = Path(tempfile.mkdtemp(prefix="actual-app-", dir=args.runtime_parent)).resolve()
        env = isolated_environment(root, args.source_root, os.environ)
        if args.browser_cache:
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(args.browser_cache.resolve())
        elif not args.browser_executable:
            raise ValueError(
                "Explicit --browser-cache or --browser-executable is required under synthetic HOME"
            )
        command = [
            str(args.source_root / ".venv/bin/python"),
            str(Path(__file__).resolve()),
            *sys.argv[1:],
            "--isolated-child",
            "--runtime",
            str(root),
        ]
        completed = subprocess.run(
            command,
            cwd=args.source_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        args.output.with_suffix(".log").write_text(completed.stdout)
        if completed.returncode:
            raise SystemExit(completed.returncode)
        return
    result = asyncio.run(run(args))
    from jsonschema import validate  # type: ignore[import-untyped]

    if not args.diagnostic_only:
        validate(result, EVIDENCE_SCHEMA)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
