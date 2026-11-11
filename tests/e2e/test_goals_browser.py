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
from playwright.sync_api import BrowserContext, Locator, Page, expect
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
            "completion_rule": {
                "actual": {"op": "reference", "measure_id": actual["id"], "day_offset": 0},
                "target": {"op": "constant", "value": 999},
                "direction": "atleast",
                "first_date": "2026-07-01",
            },
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
    # Keep keyboard inspection independent of pointer exits during focus scroll.
    page.mouse.move(0, 0)
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
    # Another mounted holder must not dismiss Chief's goal previews.
    api.direct_post(
        server, "/api/managers", {"title": "Other holder", "project_id": project["id"]}
    )
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

    def collect(value: int, limitation: str | None = None) -> str:
        current_source = api.get(server, f"/api/measure-sources/{source['id']}")
        configured = api.direct_put(
            server,
            f"/api/measure-sources/{source['id']}/script",
            {
                "expected_revision": current_source["source_revision"],
                "text": "output=context['required_outputs'][0]\n"
                f"record(output['measure_id'], output['required_date'], {value}, "
                f"limitation={limitation!r})",
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

    def accepted_manual(name: str, kind: str, *, previous_date: bool = False) -> JsonObject:
        body: JsonObject = {
            "project_id": project["id"],
            "name": name,
            "unit": "items",
            "origin": "manual",
            "kind": kind,
            "timezone": "UTC",
        }
        if kind == "period_count":
            body["period"] = "day"
        manual = api.direct_post(server, "/api/measures", body)
        current_state = api.get(server, f"/api/measures/{manual['id']}/manual-state")
        day = str(current_state["current_date"])
        if previous_date:
            from datetime import date, timedelta

            day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        api.direct_post(
            server,
            f"/api/measures/{manual['id']}/manual-writes",
            {
                "request_id": name,
                "date": day,
                "basis": "date",
                "operation": "replace",
                "value": 12,
                "expected_definition_revision": current_state["definition_revision"],
                "expected_write_revision": current_state["write_revision"],
            },
        )
        return manual

    manual_period = accepted_manual("Period fallback", "period_count", previous_date=True)
    manual_snapshot = accepted_manual("Snapshot daily control", "reading")
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Publish a field guide",
            "kind": "binary",
            "primary_checklist_block_id": None,
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
                        "items": [
                            {"id": "source-notes", "label": "Keep source notes", "state": "open"}
                        ],
                    },
                    {
                        "type": "chart",
                        "id": "coverage-gauge",
                        "kind": "gauge",
                        "placement": "supporting",
                        "title": "Collected gauge",
                        "measure_id": measure["id"],
                        "target": 40,
                    },
                    {
                        "type": "chart",
                        "id": "manual-period-cumulative",
                        "kind": "gauge",
                        "placement": "supporting",
                        "title": "Manual period fallback",
                        "measure_id": manual_period["id"],
                        "target": 12,
                        "manual": {"model": "cumulative_target", "increments": [1]},
                    },
                    {
                        "type": "chart",
                        "id": "manual-snapshot-daily",
                        "kind": "bar",
                        "placement": "supporting",
                        "title": "Manual snapshot quota control",
                        "measure_id": manual_snapshot["id"],
                        "target": 12,
                        "manual": {"model": "daily_quota", "increments": [1]},
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
    # Holder configuration crosses the API and change stream into the mounted page.
    before_selection = current["page"]
    selected = httpx.patch(
        server.base + path,
        headers={"X-Plan-Actor": "chief"},
        json={
            "primary_checklist_block_id": "approach",
        },
        timeout=10,
    )
    assert selected.is_success, selected.text
    current = api.get(server, path)
    assert current["primary_checklist_block_id"] == "approach"
    assert current["page"] == before_selection and current["completed"] is False
    primary = page.get_by_role("region", name="Completion checklist", exact=True)
    expect(primary.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
    )
    expect(page.get_by_role("button", name="Mark done", exact=True)).to_have_count(0)
    expect(page.get_by_role("checkbox", name="Review the draft", exact=True)).to_have_count(1)
    # The controlled browser fixture cannot prove that a date edit crosses the real
    # writer, saved page JSON, query invalidation, and a subsequent reload. Reuse
    # this selected-checklist scenario rather than another connected server/host.
    def persist_item_deadline(value: str | None) -> None:
        before = api.get(server, path)
        with page.expect_response(
            lambda response: response.url.endswith(path) and response.request.method == "PATCH",
            timeout=WAIT_MS,
        ) as date_saved:
            field = primary.get_by_label("Deadline for Review the draft", exact=True)
            field.fill(value or "", timeout=WAIT_MS)
            field.press("Enter", timeout=WAIT_MS)
        assert date_saved.value.ok
        body = date_saved.value.request.post_data_json
        assert isinstance(body, dict)
        assert "expected_revision" not in body
        accepted = api.get(server, path)
        expected_page = before["page"]
        expected_page["blocks"][0]["items"][1]["deadline"] = value
        assert accepted["page"] == expected_page
        assert accepted["revision"] == before["revision"] + 1
        for key in ("completed", "active", "primary_checklist_block_id", "deadline"):
            assert accepted[key] == before[key], key
        page.reload()
        expect(primary.get_by_label("Deadline for Review the draft", exact=True)).to_have_value(
            value or "", timeout=WAIT_MS
        )
        expect(primary.get_by_role("progressbar")).to_have_attribute(
            "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
        )

    persist_item_deadline("2031-10-30")
    persist_item_deadline("2031-11-02")
    persist_item_deadline(None)
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
    # The native State select saves the independent active state immediately.
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as inactive:
        page.get_by_label("State", exact=True).select_option(label="Inactive")
    assert inactive.value.ok
    expect(page.get_by_label("State", exact=True)).to_have_value("false", timeout=WAIT_MS)
    current = api.get(server, path)
    assert current["completed"] is True and current["active"] is False
    updated_page = current["page"]
    updated_page["blocks"][0]["items"][1]["label"] = "Review the updated draft"
    updated_page["blocks"][0]["items"][1]["state"] = "open"
    response = httpx.patch(
        server.base + path,
        headers={"X-Plan-Actor": "chief"},
        json={
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
    expect(page.get_by_label("State", exact=True)).to_have_value("true", timeout=WAIT_MS)
    expect(
        primary.get_by_role("list", name="Open items").get_by_role(
            "checkbox", name="Review the updated draft", exact=True
        )
    ).to_be_visible(timeout=WAIT_MS)
    # The list response supplies the same saved item counts to cards and the table.
    page.locator("button[data-chief-destination]").click(timeout=WAIT_MS)
    card = page.locator(f'[data-goal-card="{goal["id"]}"]')
    expect(card.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
    )
    # The newly interactive overview must use the real goal writer.
    # Reuse this connected scenario: component tests cannot prove persisted API
    # acceptance, effective completion, or reload reads from a card-originated edit.
    overview_revision = api.get(server, path)["revision"]
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as overview_saved:
        card.get_by_role("checkbox", name="Review the updated draft", exact=True).click(
            timeout=WAIT_MS
        )
    assert overview_saved.value.ok
    overview_body = overview_saved.value.request.post_data_json
    assert isinstance(overview_body, dict)
    assert "expected_revision" not in overview_body
    overview_accepted = api.get(server, path)
    assert overview_accepted["revision"] == overview_revision + 1
    assert overview_accepted["page"]["blocks"][0]["items"][1]["state"] == "done"
    assert overview_accepted["completed"] is True
    page.reload()
    expect(card.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "2 of 2 done", timeout=WAIT_MS
    )
    card.get_by_role("button", name="2 completed", exact=True).click(timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as overview_reopened:
        card.get_by_role("checkbox", name="Review the updated draft", exact=True).click(
            timeout=WAIT_MS
        )
    assert overview_reopened.value.ok
    assert api.get(server, path)["completed"] is False
    expect(card.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "1 of 2 done", timeout=WAIT_MS
    )
    page.locator('.rail-utilities a[data-screen="goals"]').click(timeout=WAIT_MS)
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
    # A same-value source correction must refresh its factual details without
    # suppressing the mounted gauge's meaningful comparison or animated state.
    gauge_card = page.locator('[data-chart-id="coverage-gauge"]')
    gauge = gauge_card.locator("figure[aria-label]")
    gauge.scroll_into_view_if_needed(timeout=WAIT_MS)
    expect(gauge).to_have_class(re.compile(r".*success.*"), timeout=WAIT_MS)
    expect(gauge.locator('[role="meter"]')).to_have_attribute(
        "aria-valuetext", re.compile("On target"), timeout=WAIT_MS
    )
    # Wait for the actual animated count to settle before a same-value correction.
    expect(gauge.locator('span[class*="status"]')).to_have_text("On target", timeout=WAIT_MS)
    collect(52, "The retained query covers only part of this reading.")
    details = gauge_card.locator(".observation-details")
    details.locator("summary").click(timeout=WAIT_MS)
    expect(details).to_contain_text(
        "The retained query covers only part of this reading.", timeout=WAIT_MS
    )
    expect(gauge_card.locator(".comparison")).to_have_count(0)
    expect(gauge).to_have_class(re.compile(r".*success.*"), timeout=WAIT_MS)
    expect(gauge.locator('[role="meter"]')).to_have_attribute(
        "aria-valuetext", re.compile("On target"), timeout=WAIT_MS
    )
    expect(gauge.locator('span[class*="status"]')).to_have_text("On target", timeout=WAIT_MS)
    retained = api.get(server, path + "/readings")["values"]
    assert any(
        item["measure_id"] == measure["id"]
        and item["value"] == 52
        and item["coverage_status"] == "partial"
        and item["limitation"] == "The retained query covers only part of this reading."
        for item in retained
    )

    # Accepted manual models do not establish measure semantics. The cumulative
    # count uses a prior date, while a reading accepts the daily control unchanged.
    cumulative = page.locator('[data-chart-id="manual-period-cumulative"]')
    expect(cumulative.get_by_role("meter")).to_have_attribute(
        "aria-valuetext", "12 of 12, target reached", timeout=WAIT_MS
    )
    expect(cumulative.locator(".completion")).to_have_text(
        "Target reached", timeout=WAIT_MS
    )
    expect(cumulative.locator(".radial-fill")).to_have_attribute(
        "stroke", "var(--accent-done)", timeout=WAIT_MS
    )
    snapshot = page.locator('[data-chart-id="manual-snapshot-daily"]')
    expect(snapshot.locator(".comparison")).to_have_count(0)
    expect(snapshot.locator(".card-heading > :not(h3)")).to_have_count(0)
    expect(snapshot.locator(".bar-pick").last.locator(".bar")).to_have_attribute(
        "style", re.compile(r"background-color: var\(--accent-done\)"), timeout=WAIT_MS
    )
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"

    # Reuse this connected host to prove a real native title commit and reload.
    # Frontend-only cases cannot prove API acceptance and persisted browser reads.
    before_title = api.get(server, path)
    title_editor = page.get_by_role("textbox", name="Goal title", exact=True)
    title_editor.fill("Owner title", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as titled:
        title_editor.press("Enter", timeout=WAIT_MS)
    assert titled.value.ok, titled.value.text()
    assert titled.value.request.post_data_json == {
        "title": "Owner title",
    }
    accepted_title = api.get(server, path)
    assert accepted_title["title"] == "Owner title"
    assert accepted_title["page"] == before_title["page"]
    page.reload()
    page.wait_for_selector("[data-goal-page]", timeout=WAIT_MS)
    expect(title_editor).to_have_text("Owner title", timeout=WAIT_MS)

    # The header preview reads saved titles independently of the focused page draft.
    def preview_goal(goal_id: str) -> Locator:
        page.locator(".manager-card.chief .identity").hover(timeout=WAIT_MS)
        row = page.locator(
            f'[data-rail-hover-panel].visible [data-manager-goal-id="{goal_id}"]'
        )
        expect(row).to_be_visible(timeout=WAIT_MS)
        return row

    # An external revision reaches the mounted page without replacing a focused
    # draft. The later title-only save wins and preserves the remotely edited page.
    title_editor.fill("Unaccepted local title", timeout=WAIT_MS)
    page.locator("[data-goal-page]").evaluate(
        "node => node.dataset.nativeEditProof = 'retained'"
    )
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
            headers={"X-Plan-Actor": "chief"},
            json={
                "title": "Chief title",
                "page": reordered,
            },
            timeout=10,
        )
        assert remote.is_success, remote.text
    expect(title_editor).to_have_text("Unaccepted local title", timeout=WAIT_MS)
    expect(preview_goal(str(goal["id"])).locator(".goal-title")).to_have_text(
        "Chief title", timeout=WAIT_MS
    )
    expect(page.locator("[data-goal-page]")).to_have_attribute(
        "data-native-edit-proof", "retained", timeout=WAIT_MS
    )
    with page.expect_response(
        lambda reply: reply.url.endswith(path) and reply.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as local_title:
        title_editor.press("Enter", timeout=WAIT_MS)
    assert local_title.value.status == 200, local_title.value.text()
    assert local_title.value.request.post_data_json == {
        "title": "Unaccepted local title",
    }
    error = page.locator("[data-goal-page] .goal-heading .error-line")
    expect(error).to_have_count(0, timeout=WAIT_MS)
    expect(title_editor).to_have_text("Unaccepted local title", timeout=WAIT_MS)
    accepted_local = api.get(server, path)
    assert remote.json()["revision"] == current["revision"] + 1
    assert accepted_local["title"] == "Unaccepted local title"
    assert accepted_local["revision"] == remote.json()["revision"] + 1
    assert accepted_local["page"] == reordered
    expect(preview_goal(str(goal["id"])).locator(".goal-title")).to_have_text(
        "Unaccepted local title", timeout=WAIT_MS
    )
    expect(page.locator("[data-goal-page]")).to_have_attribute(
        "data-native-edit-proof", "retained", timeout=WAIT_MS
    )
    # The field stays locked until the page reads the saved goal back, so the
    # old title cannot flash. Playwright fill fails at once on a locked field.
    expect(title_editor).to_have_attribute("contenteditable", "true", timeout=WAIT_MS)
    title_editor.fill("Discard this unsaved title", timeout=WAIT_MS)
    title_editor.press("Escape", timeout=WAIT_MS)
    expect(title_editor).to_have_text("Unaccepted local title", timeout=WAIT_MS)
    expect(error).to_have_count(0, timeout=WAIT_MS)
    assert api.get(server, path)["revision"] == accepted_local["revision"]
    expect(page.locator('[data-block="table"] tbody tr').first).to_contain_text(
        "Second", timeout=WAIT_MS
    )
    assert api.get(server, path)["page"]["blocks"][1]["rows"] == [
        ["Review", "Second"],
        ["Outline", "First"],
    ]

    # The holder preview must subscribe to another active goal while this one is selected.
    other = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Another active goal",
            "kind": "binary",
            "primary_checklist_block_id": None,
        },
    )
    # The connected host proves that page completion reaches the saved holder preview.
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "PATCH",
        timeout=WAIT_MS,
    ) as checked:
        primary.get_by_role(
            "checkbox", name="Review the updated draft", exact=True
        ).click(timeout=WAIT_MS)
    assert checked.value.ok
    assert api.get(server, path)["completed"] is True
    completed_goal = preview_goal(str(goal["id"]))
    expect(completed_goal.locator("small")).to_contain_text("Done", timeout=WAIT_MS)
    completed_goal.click(timeout=WAIT_MS)
    expect(primary.get_by_role("progressbar")).to_have_attribute(
        "aria-valuetext", "2 of 2 done", timeout=WAIT_MS
    )
    # Opening either goal starts no model. Both pages use their shared holder.
    assert api.get(server, "/api/chief/conversation")["conversation_id"] is None
    other_message = api.direct_post(
        server,
        "/api/messages/send",
        {
            "target": {"kind": "chief", "id": "chief"},
            "message": "Work on our goals",
        },
    )
    holder_conversation_id = other_message["conversation_id"]
    chief = page.locator("[data-chief-destination]")
    expect(chief.locator('[data-conversation-signal="working"]')).to_have_count(
        1, timeout=WAIT_MS
    )
    # Goals retain no independent conversation marks, even when their holder works.
    expect(preview_goal(str(other["id"])).locator("[data-conversation-signal]")).to_have_count(0)
    assert api.get(server, "/api/chief/conversation")["conversation_id"] == holder_conversation_id
    api.direct_post(
        server,
        f"/api/test/connected/{holder_conversation_id}/message",
        {
            "kind": "chief",
            "id": "chief",
            "text": "A question from your goals holder",
        },
    )
    expect(chief.locator('[data-conversation-signal="message"]')).to_have_count(
        1, timeout=WAIT_MS
    )
    expect(preview_goal(str(other["id"])).locator("[data-conversation-signal]")).to_have_count(0)
    api.direct_post(server, f"/api/test/signals/{holder_conversation_id}/finish", {})
    # A browser message beside either goal reuses the holder's existing conversation.
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.locator("[data-conversation-input]").fill("Help with this goal", timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith("/api/chief/conversation/send"), timeout=WAIT_MS
    ) as delivered:
        page.locator("[data-conversation-input]").press("Enter", timeout=WAIT_MS)
    assert delivered.value.ok, delivered.value.text()
    conversation_id = delivered.value.json()["conversation_id"]
    assert conversation_id == holder_conversation_id
    assert api.get(server, "/api/chief/conversation")["conversation_id"] == conversation_id
    wire = api.get(server, f"/api/test/signals/{conversation_id}/delivered")["texts"][-1]
    assert "You are the agent for goal " not in wire
    assert "Help with this goal" in wire
    api.direct_post(server, f"/api/test/signals/{conversation_id}/finish", {})
    page.get_by_role("button", name="Conversation options", exact=True).click(timeout=WAIT_MS)
    page.locator("[data-conversation-new-arm]").click(timeout=WAIT_MS)
    with page.expect_response(
        lambda response: response.url.endswith("/api/chief/conversation/reset"), timeout=WAIT_MS
    ) as reset:
        page.locator("[data-conversation-new-confirm]").click(timeout=WAIT_MS)
    assert reset.value.ok
    assert api.get(server, "/api/chief/conversation")["conversation_id"] is None
    # Reset clears the shared pointer and preserves the holder's recorded messages.
    assert "Help with this goal" in api.get(
        server, f"/api/test/signals/{conversation_id}/delivered"
    )["texts"][-1]
    page.reload()
    page.wait_for_selector("[data-goal-page]", timeout=WAIT_MS)
    assert api.get(server, path)["page"]["blocks"][0]["items"][0]["state"] == "done"


def test_goal_delete_confirmation_persistence_and_open_artifact_cleanup(
    server: ServerHandle,
    api: ApiHelper,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Prove confirmed deletion reaches storage and invalidates another mounted page.

    The existing server and browser host cover the native confirmation, API write,
    mounted resource refresh, and artifact unmount together. Mocked browser responses
    cannot prove that deletion survives reload or reaches another open view.
    """
    project = api.direct_post(server, "/api/projects", {"name": "Goal deletion proof"})
    holder = api.direct_post(
        server, "/api/managers",
        {"title": "Temporary goal holder", "project_id": project["id"]},
    )
    goal = api.direct_post(
        server,
        "/api/goals",
        {
            "project_id": project["id"],
            "title": "Delete this temporary goal",
            "holder_kind": "manager",
            "holder_id": holder["id"],
            "kind": "binary",
            "primary_checklist_block_id": None,
        },
    )
    path = f"/api/goals/{goal['id']}"
    artifact_path = f"/files/goals/{goal['id']}/artifacts/note.md"
    api.direct_put(server, artifact_path, {"content": "Temporary goal artifact"})
    context = context_factory()
    route = f"#/workspace/goal/{goal['id']}"
    page = open_page(context, server, f"#/workspace/manager/{holder['id']}", ".goal-heading")
    other = open_page(
        context, server, route + f"?source=goal&goal={goal['id']}&path=artifacts%2Fnote.md",
        ".goal-heading",
    )
    expect(other.get_by_text("Temporary goal artifact", exact=True)).to_be_visible(timeout=WAIT_MS)
    # A separate network partition avoids HTTP/1 connection exhaustion from
    # three tabs' long-lived change and navigation streams on the same origin.
    standalone = context_factory().new_page()
    standalone.goto(
        server.base + f"/#/preview?source=goal&goal={goal['id']}&path=artifacts%2Fnote.md"
    )
    expect(standalone.get_by_text("Temporary goal artifact", exact=True)).to_be_visible(
        timeout=WAIT_MS
    )
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Delete goal", exact=True).click(timeout=WAIT_MS)
    assert api.get(server, path)["id"] == goal["id"]
    expect(page.get_by_role("button", name="Delete goal", exact=True)).to_be_enabled()
    if proof := os.environ.get("PANELS_UI_PROOF_DIR"):
        folder = Path(proof)
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / "goal-delete-before.png"), full_page=True)
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith(path) and response.request.method == "DELETE",
        timeout=WAIT_MS,
    ) as deleted:
        page.get_by_role("button", name="Delete goal", exact=True).click(timeout=WAIT_MS)
    assert deleted.value.ok, deleted.value.text()
    expect(page).to_have_url(re.compile(r"#/goals$"), timeout=WAIT_MS)
    expect(other.get_by_text("Goal not found.", exact=True)).to_be_visible(timeout=WAIT_MS)
    expect(other.get_by_text("Temporary goal artifact", exact=True)).to_have_count(0)
    expect(standalone.get_by_text("Goal not found.", exact=True)).to_be_visible(timeout=WAIT_MS)
    expect(standalone.locator("[data-ticket-artifact]")).to_have_count(0)
    expect(page.get_by_role("region", name="All goals", exact=True)).to_be_visible(timeout=WAIT_MS)
    expect(page.get_by_role("link", name="Delete this temporary goal", exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_role("region", name="All goals", exact=True)).to_be_visible(timeout=WAIT_MS)
    assert httpx.get(server.base + path, timeout=10).status_code == 404
    assert httpx.get(server.base + artifact_path, timeout=10).status_code == 404
    if proof := os.environ.get("PANELS_UI_PROOF_DIR"):
        page.screenshot(path=str(Path(proof) / "goal-delete-after.png"), full_page=True)
        other.screenshot(path=str(Path(proof) / "goal-delete-open-view.png"), full_page=True)
        standalone.screenshot(
            path=str(Path(proof) / "goal-delete-standalone-artifact.png"), full_page=True
        )
    other.reload()
    expect(other.get_by_text("Goal not found.", exact=True)).to_be_visible(timeout=WAIT_MS)
    expect(other.get_by_role("button", name="Delete goal", exact=True)).to_have_count(0)
