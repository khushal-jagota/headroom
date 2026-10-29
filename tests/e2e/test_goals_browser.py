"""Saved goal edits and conversations cross the browser, API, store and change stream.

The shared scripted-backend server proves the actual first-message ownership path without
starting a provider. Unit tests cannot prove mounted queries receive external goal edits.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, ApiHelper, JsonObject, ServerHandle
from tests.support.server_fixtures import signal_server as signal_server


def test_formula_expectation_projection_and_correction_reach_mounted_chart(
    server: ServerHandle,
    api: ApiHelper,
    cli: Callable[..., JsonObject],
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Prove API/CLI projections cross Svelte, React and live resource invalidation.

    Pure projection and renderer tests cannot prove that a formula-only dependency's
    manual correction reaches a mounted saved page. Reuse the ordinary server and
    browser host for one scenario; no source, script or provider is necessary.
    """
    project = api.direct_post(server, "/api/projects", {"name": "Formula browser proof"})

    def manual_measure(name: str) -> JsonObject:
        return api.direct_post(
            server,
            "/api/measures",
            {
                "project_id": project["id"],
                "name": name,
                "unit": "items",
                "origin": "manual",
                "kind": "reading",
                "timezone": "UTC",
            },
        )

    def replace(measure: JsonObject, day: str, value: float) -> None:
        state = api.get(server, f"/api/measures/{measure['id']}/manual-state?date={day}")
        api.direct_post(
            server,
            f"/api/measures/{measure['id']}/manual-writes",
            {
                "request_id": f"{measure['id']}:{day}:{state['write_revision']}",
                "date": day,
                "basis": "date",
                "operation": "replace",
                "value": value,
                "expected_definition_revision": state["definition_revision"],
                "expected_write_revision": state["write_revision"],
            },
        )

    actual, baseline = manual_measure("Progress"), manual_measure("Opening baseline")
    replace(baseline, "2026-07-01", 100.125)
    for day in ("2026-07-02", "2026-07-03", "2026-07-04"):
        replace(actual, day, 101.125)
    fixed_baseline = {"op": "reference_at", "measure_id": baseline["id"], "date": "2026-07-01"}
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Follow the phased expectation",
            "kind": "quantitative",
            "deadline": "2026-07-08",
            "primary_chart": {
                "type": "chart",
                "id": "formula-progress",
                "kind": "line",
                "title": "Progress against expectation",
                "measure_id": actual["id"],
                "format": {"decimals": 2},
                "target": 999,
                "expectation": {
                    "op": "if_before",
                    "date": "2026-07-03",
                    "before": {
                        "op": "reference",
                        "measure_id": baseline["id"],
                        "day_offset": 0,
                    },
                    "otherwise": {
                        "op": "add",
                        "left": fixed_baseline,
                        "right": {
                            "op": "if_before",
                            "date": "2026-07-05",
                            "before": {
                                "op": "multiply",
                                "left": {"op": "constant", "value": 0.25},
                                "right": {"op": "elapsed_days", "start": "2026-07-01"},
                            },
                            "otherwise": {"op": "constant", "value": 100},
                        },
                    },
                },
            },
            "page": {
                "kind": "headroom.page",
                "version": 1,
                "title": "Saved approach",
                "summary": "Follow the dated expectation.",
                "blocks": [],
            },
        },
    )
    path = f"/api/goals/{goal['id']}"
    chart_id = goal["primary_chart"]["id"]
    read_path = path + "/readings?first_date=2026-07-01&last_date=2026-07-08"
    readings = api.get(server, read_path)
    assert (
        cli(
            server,
            "goal",
            "read",
            goal["id"],
            "--first-date",
            "2026-07-01",
            "--last-date",
            "2026-07-08",
        )
        == readings
    )
    projection = readings["charts"][chart_id]
    points = {point["date"]: point for point in projection["points"]}
    assert projection["summary_date"] == "2026-07-04"
    assert (
        points["2026-07-03"]["expected"]["value"],
        points["2026-07-03"]["difference"],
        points["2026-07-03"]["meets_expectation"],
    ) == (100.625, 0.5, True)
    assert (
        points["2026-07-04"]["expected"]["value"],
        points["2026-07-04"]["difference"],
        points["2026-07-04"]["meets_expectation"],
    ) == (100.875, 0.25, True)
    assert points["2026-07-02"]["expected"]["value"] is None
    assert points["2026-07-02"]["meets_expectation"] is None
    assert points["2026-07-05"]["break_before"] is True
    assert points["2026-07-08"]["expected"]["value"] == 200.125
    assert points["2026-07-08"]["actual"]["value"] is None
    chart_dates = [
        point["date"] for point in api.get(server, path + "/readings")["charts"][chart_id]["points"]
    ]

    page = open_page(
        context_factory(), server, f"#/workspace/goal/{goal['id']}", "[data-goal-page]"
    )
    page.emulate_media(reduced_motion="reduce")
    chart = page.locator(f'[data-chart-id="{chart_id}"]')
    headline = chart.locator(".metric-value[data-current]")
    plot = chart.get_by_role("slider")
    expected_tip = chart.locator('[data-chart-tooltip] [data-tooltip-series="target"]')
    expect(headline).to_have_attribute("data-current", "101.125", timeout=WAIT_MS)
    chart.get_by_role("button", name="All time", exact=True).click(timeout=WAIT_MS)
    expect(headline).to_have_attribute("data-current", "101.125", timeout=WAIT_MS)
    # The production SVG must keep both the missing date and the phase jump open.
    expect(chart.locator('[data-line="target"]')).to_have_attribute(
        "d", re.compile(r"M.*M.*M"), timeout=WAIT_MS
    )
    plot.focus(timeout=WAIT_MS)
    plot.press("End", timeout=WAIT_MS)
    expect(plot).to_have_attribute(
        "aria-valuetext", re.compile("2026-07-08.*Expected 200.13"), timeout=WAIT_MS
    )
    expect(expected_tip).to_have_text("Expected200.13", timeout=WAIT_MS)
    expect(headline).to_have_attribute("data-current", "missing", timeout=WAIT_MS)
    for _ in range(6):
        plot.press("ArrowLeft", timeout=WAIT_MS)
    expect(plot).to_have_attribute(
        "aria-valuetext", re.compile("2026-07-02.*Expected No reading"), timeout=WAIT_MS
    )
    expect(chart.locator(".comparison")).to_have_text(
        "Expected reading unavailable", timeout=WAIT_MS
    )
    assert chart.locator(".comparison").evaluate("node => node.style.color") == "var(--text-muted)"
    expect(chart.locator('[data-dot="target"]')).to_have_css("display", "none", timeout=WAIT_MS)
    plot.press("ArrowRight", timeout=WAIT_MS)
    expect(plot).to_have_attribute(
        "aria-valuetext", re.compile("2026-07-03.*Expected 100.63"), timeout=WAIT_MS
    )
    expect(expected_tip).to_have_text("Expected100.63", timeout=WAIT_MS)
    # Pointer input resolves the same real date as keyboard input.
    bounds = plot.bounding_box()
    assert bounds is not None
    today_position = chart_dates.index("2026-07-04") / (len(chart_dates) - 1)
    page.mouse.move(
        bounds["x"] + bounds["width"] * today_position, bounds["y"] + bounds["height"] / 2
    )
    expect(plot).to_have_attribute(
        "aria-valuetext", re.compile("2026-07-04.*Expected 100.88"), timeout=WAIT_MS
    )
    expect(expected_tip).to_have_text("Expected100.88", timeout=WAIT_MS)

    # The baseline exists only in the formula: its write must invalidate this page.
    page.locator("[data-goal-page]").evaluate("node => node.dataset.formulaProof = 'retained'")
    saved_page = api.get(server, path)
    replace(baseline, "2026-07-01", 102.125)
    expect(expected_tip).to_have_text("Expected102.88", timeout=WAIT_MS)
    expect(chart.locator(".comparison")).to_have_text("1.75 below target", timeout=WAIT_MS)
    corrected = api.get(server, read_path)["charts"][chart_id]["points"][3]
    assert (
        corrected["expected"]["value"],
        corrected["difference"],
        corrected["meets_expectation"],
    ) == (102.875, -1.75, False)
    assert api.get(server, path)["page"] == saved_page["page"]
    assert api.get(server, path)["revision"] == saved_page["revision"]
    expect(page.locator("[data-goal-page]")).to_have_attribute(
        "data-formula-proof", "retained", timeout=WAIT_MS
    )
    page.mouse.move(0, 0)
    plot.press("Escape", timeout=WAIT_MS)
    expect(headline).to_have_attribute("data-current", "101.125", timeout=WAIT_MS)

    if evidence := os.environ.get("PANELS_FORMULA_EVIDENCE_DIR"):
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(folder / "formula-desktop.png"), full_page=True, timeout=WAIT_MS)
        page.set_viewport_size({"width": 390, "height": 844})
        expect(headline).to_have_attribute("data-current", "101.125", timeout=WAIT_MS)
        page.screenshot(path=str(folder / "formula-phone.png"), full_page=True, timeout=WAIT_MS)


def test_goal_page_persistence_external_refresh_and_owned_conversation(
    signal_server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    server = signal_server
    project = api.direct_post(server, "/api/projects", {"name": "Goal browser proof"})
    source = api.direct_post(
        server,
        "/api/measure-sources",
        {
            "name": "Collected reading",
            "project_id": project["id"],
            "account": "browser-proof",
            "responsible": {"kind": "chief", "id": "chief"},
        },
    )
    measure = api.direct_post(
        server,
        "/api/measures",
        {
            "project_id": project["id"],
            "name": "Draft words",
            "unit": "words",
            "origin": "taken",
            "kind": "reading",
            "timezone": "UTC",
            "source_id": source["id"],
        },
    )

    def collect(value: int) -> str:
        current_source = api.get(server, f"/api/measure-sources/{source['id']}")
        configured = api.direct_put(
            server,
            f"/api/measure-sources/{source['id']}/script",
            {
                "expected_revision": current_source["source_revision"],
                "text": "output=context['required_outputs'][0]\n"
                f"record(output['measure_id'], output['required_date'], {value})",
            },
        )
        run = api.direct_post(
            server,
            f"/api/measure-sources/{source['id']}/runs",
            {
                "mode": "collection",
                "expected_script_revision": configured["active_script"]["revision"],
            },
        )
        return str(run["id"])

    first_run = collect(42)
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Publish a field guide",
            "kind": "binary",
            "page": {
                "kind": "headroom.page",
                "version": 1,
                "title": "Approach",
                "summary": "",
                "blocks": [
                    {
                        "type": "checklist",
                        "id": "approach",
                        "title": "Approach checklist",
                        "items": [
                            {"id": "outline", "label": "Write the outline", "state": "open"},
                            {"id": "review", "label": "Review the draft", "state": "open"},
                        ],
                    },
                    {
                        "type": "table",
                        "id": "notes",
                        "columns": ["Task", "Note"],
                        "rows": [["Outline", "First"], ["Review", "Second"]],
                    },
                    {
                        "type": "chart",
                        "id": "reading",
                        "kind": "number",
                        "placement": "supporting",
                        "title": "Draft words",
                        "measure_id": measure["id"],
                    },
                    {
                        "type": "checklist",
                        "id": "supporting-tasks",
                        "title": "Supporting tasks",
                        "items": [{"id": "notes", "label": "Keep source notes", "state": "open"}],
                    },
                ],
            },
        },
    )
    path = f"/api/goals/{goal['id']}"
    page = open_page(
        context_factory(), server, f"#/workspace/goal/{goal['id']}", "[data-goal-page]"
    )
    expect(page.get_by_role("heading", name="Publish a field guide")).to_be_visible(timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as saved:
        page.get_by_role("checkbox", name="Write the outline", exact=True).click(timeout=WAIT_MS)
    assert saved.value.ok
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"
    page.reload()
    page.wait_for_selector("[data-goal-page]", timeout=WAIT_MS)
    # Completed steps move into the saved disclosure instead of disappearing on reload.
    page.locator("[data-group-toggle]").click(timeout=WAIT_MS)
    expect(page.get_by_role("checkbox", name="Write the outline", exact=True)).to_be_checked(
        timeout=WAIT_MS
    )
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ):
        page.get_by_role("button", name="Mark done", exact=True).click(timeout=WAIT_MS)
    current = api.get(server, path)
    assert current["completed"] is True and current["active"] is True
    assert any(
        row["id"] == goal["id"] for row in api.get(server, "/api/goals?active=true")["goals"]
    )
    # Promotion crosses the same revision-checked writer as ordinary checklist edits.
    before_selection = current["page"]
    page.get_by_role("button", name="Edit goal", exact=True).click(timeout=WAIT_MS)
    page.get_by_label("Completion display", exact=True).select_option(label="Approach checklist")
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as selected:
        page.get_by_role("button", name="Save", exact=True).click(timeout=WAIT_MS)
    assert selected.value.ok
    current = api.get(server, path)
    assert current["primary_checklist_block_id"] == "approach"
    assert current["page"] == before_selection and current["completed"] is False
    primary = page.get_by_role("region", name="Completion checklist", exact=True)
    expect(primary.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
    )
    expect(page.get_by_role("button", name="Mark done", exact=True)).to_have_count(0)
    expect(page.get_by_role("checkbox", name="Review the draft", exact=True)).to_have_count(1)
    # An ordinary checklist still saves without contributing to effective completion.
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as ordinary:
        page.get_by_role("checkbox", name="Keep source notes", exact=True).click(timeout=WAIT_MS)
    assert ordinary.value.ok
    current = api.get(server, path)
    assert current["page"]["blocks"][3]["items"][0]["state"] == "done"
    assert current["completed"] is False
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as completed:
        primary.get_by_role("checkbox", name="Review the draft", exact=True).click(timeout=WAIT_MS)
    assert completed.value.ok
    assert api.get(server, path)["completed"] is True
    page.reload()
    expect(primary.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "2 of 2 done", timeout=WAIT_MS
    )
    primary.locator("[data-group-toggle]").click(timeout=WAIT_MS)
    expect(primary.get_by_role("checkbox", name="Review the draft", exact=True)).to_be_checked(
        timeout=WAIT_MS
    )
    # The approved page keeps the independent active state behind Edit goal.
    page.get_by_role("button", name="Edit goal", exact=True).click(timeout=WAIT_MS)
    page.get_by_label("State", exact=True).select_option(label="Inactive")
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ):
        page.get_by_role("button", name="Save", exact=True).click(timeout=WAIT_MS)
    expect(page.locator("[data-goal-page] .goal-state")).to_have_text("Inactive", timeout=WAIT_MS)
    current = api.get(server, path)
    assert current["completed"] is True and current["active"] is False
    updated_page = current["page"]
    updated_page["blocks"][0]["items"][1]["label"] = "Review the updated draft"
    updated_page["blocks"][0]["items"][1]["state"] = "open"
    response = httpx.patch(
        server.base + path,
        headers={"X-Plan-Actor": "goal", "X-Plan-Goal-ID": goal["id"]},
        json={
            "expected_revision": current["revision"],
            "page": updated_page,
            "active": True,
        },
        timeout=10,
    )
    assert response.is_success, response.text
    expect(primary.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
    )
    assert api.get(server, path)["completed"] is False
    expect(page.locator("[data-goal-page] .goal-state")).to_have_text("Active", timeout=WAIT_MS)
    expect(page.get_by_role("checkbox", name="Review the updated draft", exact=True)).to_be_visible(
        timeout=WAIT_MS
    )
    # The list response supplies the same saved item counts to cards and the table.
    page.get_by_role("link", name="Chief of Staff", exact=True).click(timeout=WAIT_MS)
    card = page.locator(f'[data-goal-card="{goal["id"]}"]')
    expect(card.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
    )
    page.get_by_role("link", name="All goals", exact=True).click(timeout=WAIT_MS)
    row = page.get_by_role("row").filter(
        has=page.get_by_role("link", name="Publish a field guide", exact=True)
    )
    expect(row.get_by_role("cell").nth(1)).to_have_text("1 done", timeout=WAIT_MS)
    expect(row.get_by_role("cell").nth(2)).to_have_text("2 items", timeout=WAIT_MS)
    row.get_by_role("link", name="Publish a field guide", exact=True).click(timeout=WAIT_MS)
    reading = page.locator('[data-chart-id="reading"] [data-current]')
    expect(reading).to_have_attribute("data-current", "42", timeout=WAIT_MS)
    assert api.get(server, f"/api/measure-source-runs/{first_run}")["run"]["status"] == "succeeded"
    collect(52)
    expect(reading).to_have_attribute("data-current", "52", timeout=WAIT_MS)
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"

    # Authored text is read-only on a goal page. The goal agent still writes the document,
    # and the page shows the written words without an editor of its own.
    assert page.locator("[data-goal-page] [contenteditable]").count() == 0
    current = api.get(server, path)
    reordered = current["page"]
    reordered["blocks"][1]["rows"].reverse()
    with page.expect_response(
        lambda reply: (
            urlparse(reply.url).path == "/api/resource-body"
            and parse_qs(urlparse(reply.url).query).get("key") == [path]
        ),
        timeout=WAIT_MS,
    ):
        remote = httpx.patch(
            server.base + path,
            headers={"X-Plan-Actor": "goal", "X-Plan-Goal-ID": goal["id"]},
            json={
                "expected_revision": current["revision"],
                "title": "Agent title",
                "page": reordered,
            },
            timeout=10,
        )
        assert remote.is_success, remote.text
    expect(page.locator("[data-goal-page] h1")).to_have_text("Agent title", timeout=WAIT_MS)
    expect(page.locator('[data-block="table"] tbody tr').first).to_contain_text(
        "Second", timeout=WAIT_MS
    )
    assert api.get(server, path)["page"]["blocks"][1]["rows"] == [
        ["Review", "Second"],
        ["Outline", "First"],
    ]

    # The rail must subscribe to another active goal, even while this one is selected.
    other = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Another active goal",
            "kind": "binary",
        },
    )
    other_message = api.direct_post(
        server,
        "/api/messages/send",
        {
            "target": {"kind": "goal", "id": other["id"]},
            "message": "Work on the other goal",
        },
    )
    other_id = other_message["conversation_id"]
    rail_goal = page.locator(f'[data-goal-id="{other["id"]}"]')
    expect(rail_goal.locator('[data-conversation-signal="working"]')).to_have_count(
        1, timeout=WAIT_MS
    )
    api.direct_post(
        server,
        f"/api/test/connected/{other_id}/message",
        {
            "kind": "goal",
            "id": other["id"],
            "text": "A question from the other goal",
        },
    )
    expect(rail_goal.locator('[data-conversation-signal="message"]')).to_have_count(
        1, timeout=WAIT_MS
    )
    api.direct_post(server, f"/api/test/signals/{other_id}/finish", {})
    # Opening the page did not start a model. Its first browser message must do so.
    assert api.get(server, path)["conversation_id"] is None
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    type_instead = page.get_by_role("button", name="Type instead", exact=True)
    if type_instead.is_visible():
        type_instead.click(timeout=WAIT_MS)
    page.locator("[data-conversation-input]").fill("Help with this goal", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path + "/conversation/send"), timeout=WAIT_MS
    ) as delivered:
        page.locator("[data-conversation-input]").press("Enter", timeout=WAIT_MS)
    assert delivered.value.ok, delivered.value.text()
    conversation_id = delivered.value.json()["conversation_id"]
    wire = api.get(server, f"/api/test/signals/{conversation_id}/delivered")["texts"][-1]
    assert "You are the agent for goal " + goal["id"] in wire
    assert "Help with this goal" in wire
    api.direct_post(server, f"/api/test/signals/{conversation_id}/finish", {})
    page.get_by_role("button", name="Conversation options", exact=True).click(timeout=WAIT_MS)
    page.locator("[data-conversation-new-arm]").click(timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path + "/conversation/reset"), timeout=WAIT_MS
    ) as reset:
        page.locator("[data-conversation-new-confirm]").click(timeout=WAIT_MS)
    assert reset.value.ok
    history = api.get(server, f"/api/conversation-history/goal/{goal['id']}")
    assert [entry["conversation_id"] for entry in history["entries"]] == [conversation_id]
    assert api.get(server, path)["conversation_id"] is None
    page.reload()
    page.wait_for_selector("[data-goal-page]", timeout=WAIT_MS)
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"


def test_goal_files_open_from_catalog_and_historical_conversation(
    signal_server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Connect goal catalog and Markdown links to served bytes in the workspace frame.

    The existing server and browser fixtures prove this connection without another host.
    Pure address tests cannot prove the mounted goal page intercepts both link sources.
    """
    server = signal_server
    project = api.direct_post(server, "/api/projects", {"name": "Goal file browser proof"})
    goal = api.direct_post(
        server,
        "/api/goals",
        {"project_id": project["id"], "title": "Keep the field guide", "kind": "binary"},
    )
    goal_path = f"/api/goals/{goal['id']}"
    file_path = f"/files/goals/{goal['id']}/artifacts/field%20guide.md"
    api.direct_put(
        server, file_path, {"content": "# Durable field guide\n\nSaved supporting material."}
    )
    page = open_page(
        context_factory(), server, f"#/workspace/goal/{goal['id']}", "[data-goal-page]"
    )
    page.locator("[data-artifacts-band] summary").click(timeout=WAIT_MS)
    page.locator('[data-artifact-folder="artifacts"]').click(timeout=WAIT_MS)
    page.locator('[data-artifact-chip="artifacts/field guide.md"]').click(timeout=WAIT_MS)
    artifact = page.locator("[data-ticket-artifact]")
    expect(artifact.get_by_role("heading", name="Durable field guide")).to_be_visible(
        timeout=WAIT_MS
    )
    assert f"/workspace/goal/{goal['id']}?" in page.url
    assert parse_qs(urlparse(page.url).fragment.split("?", 1)[1])["source"] == ["goal"]
    page.go_back()
    expect(artifact).to_have_count(0, timeout=WAIT_MS)
    expect(page.get_by_role("heading", name="Keep the field guide")).to_be_visible(timeout=WAIT_MS)

    sent = api.direct_post(
        server,
        "/api/messages/send",
        {"target": {"kind": "goal", "id": goal["id"]}, "message": "Keep this guide"},
    )
    conversation_id = sent["conversation_id"]
    api.direct_post(
        server,
        f"/api/test/connected/{conversation_id}/message",
        {"kind": "goal", "id": goal["id"], "text": f"[Field guide]({file_path})"},
    )
    api.direct_post(server, f"/api/test/signals/{conversation_id}/finish", {})
    api.direct_post(server, goal_path + "/conversation/reset", {})
    page.goto(server.base + f"/#/workspace/goal/{goal['id']}?conversation={conversation_id}")
    conversation_link = page.get_by_role("link", name="Open field guide.md", exact=True)
    conversation_link.filter(visible=True).first.click(timeout=WAIT_MS)
    expect(artifact.get_by_role("heading", name="Durable field guide")).to_be_visible(
        timeout=WAIT_MS
    )
    opened_url = page.url
    opened_params = parse_qs(urlparse(opened_url).fragment.split("?", 1)[1])
    assert opened_params["conversation"] == [conversation_id]
    page.reload()
    expect(artifact.get_by_role("heading", name="Durable field guide")).to_be_visible(
        timeout=WAIT_MS
    )
    assert page.url == opened_url
    page.locator("[data-ticket-artifact-close]").click(timeout=WAIT_MS)
    expect(artifact).to_have_count(0, timeout=WAIT_MS)
    closed_params = parse_qs(urlparse(page.url).fragment.split("?", 1)[1])
    assert closed_params == {"conversation": [conversation_id]}
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    expect(conversation_link.filter(visible=True).first).to_be_visible(timeout=WAIT_MS)
