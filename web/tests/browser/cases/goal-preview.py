"""Native preview DOM and late write receipts through the production application.

The existing shell fixture supplies scoped navigation. Controlled HTTP responses keep
failure timing deterministic without a second backend or a second preview owner.
"""

import copy
import json
import os
import re
import runpy
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

TODAY = "2026-10-30"
YESTERDAY = "2026-10-29"


class GoalFixture:
    def __init__(self):
        self.navigation = runpy.run_path(
            str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py")
        )
        self.version = 1
        self.writes = []
        self.held = []
        self.hold = False
        self.hold_cumulative_read = False
        self.held_reads = []
        self.observations = {}
        self.coverage = {}
        self.manual_revisions = {}
        self.goals = {}
        self.reading_overrides = {}
        for index, model in enumerate(("daily_quota", "cumulative_target", "individual_readings")):
            goal = self.goal(model, model.replace("_", " ").title(), index)
            goal["kind"] = "quantitative"
            goal["primary_chart"] = {
                "type": "chart",
                "id": model,
                "kind": "gauge" if index == 1 else "line" if index == 2 else "bar",
                "placement": "primary",
                "title": goal["title"],
                "target": 20,
                "measure_id": model,
                "manual": {"model": model, "increments": [1]},
            }
            self.observations[model] = {TODAY: 3, YESTERDAY: 2}
            self.manual_revisions[model] = 0
        self.goal("binary", "Send the field guide", 3)
        checklist = self.goal("checklist", "Prepare the field guide", 4)
        checklist["primary_checklist_block_id"] = "delivery"
        checklist["page"]["blocks"] = [
            {
                "type": "checklist",
                "id": "delivery",
                "title": "Delivery",
                "doneTitle": "Delivered",
                "items": [
                    {"id": "draft", "label": "Draft accepted", "state": "done"},
                    {
                        "id": "publish",
                        "label": "Publish guide",
                        "state": "open",
                        "detail": "Check the published guide.",
                    },
                ],
            }
        ]
        long = self.goal(
            "long", "A full long title remains reachable inside a narrow preview " * 3, 5
        )
        long["primary_checklist_block_id"] = "long-list"
        long["page"]["blocks"] = [
            {
                "type": "checklist",
                "id": "long-list",
                "title": "Long checklist",
                "items": [
                    {"id": f"item-{index}", "label": f"Long item {index}", "state": "open"}
                    for index in range(30)
                ],
            }
        ]

    def goal(self, identity, title, index):
        goal = {
            "id": identity,
            "title": title,
            "project_id": "project",
            "why": "",
            "kind": "binary",
            "active": True,
            "completed": False,
            "primary_checklist_block_id": None,
            "deadline": "2026-12-31",
            "primary_chart": None,
            "revision": 0,
            "created_at": index + 1,
            "updated_at": 1,
            "conversation_id": None,
            "employee_backend": "codex",
            "employee_launch_model": "",
            "employee_launch_reasoning_effort": None,
            "status": "neutral",
            "status_date": TODAY,
            "next_refresh_at": int(time.time()) + 86400,
            "page": {
                "kind": "headroom.page",
                "version": 1,
                "title": "Approach",
                "summary": "",
                "blocks": [],
            },
        }
        self.goals[identity] = goal
        return goal

    def observation(self, measure, date):
        value = self.observations[measure].get(date)
        if value is None:
            return None
        return {
            "measure_id": measure,
            "date": date,
            "value": value,
            "run_id": None,
            "observed_at": 1,
            "definition_revision": 0,
            "period_interval": None,
            "manual_write_id": "fixture",
            "query_start_at": None,
            "query_end_at": None,
            "limitation": None,
            "coverage_status": self.coverage.get((measure, date), "not_applicable"),
            "coverage_reasons": [],
        }

    def state(self, measure, date=TODAY):
        return {
            "measure_id": measure,
            "definition_revision": 0,
            "write_revision": self.manual_revisions[measure],
            "timezone": "UTC",
            "current_date": TODAY,
            "date": date,
            "observation": self.observation(measure, date),
            "latest_observation": self.observation(measure, max(self.observations[measure])),
        }

    def readings(self, identity):
        if identity in self.reading_overrides:
            return self.reading_overrides[identity]
        values = []
        if identity in self.observations:
            for date, value in self.observations[identity].items():
                values.append(
                    {
                        "measure_id": identity,
                        "date": date,
                        "value": value,
                        "definition_revision": 0,
                        "status": "value",
                        "inputs": [],
                        "missing_inputs": [],
                        "query_start_at": None,
                        "query_end_at": None,
                        "limitation": None,
                        "coverage_status": "not_applicable",
                        "coverage_reasons": [],
                    }
                )
        return {
            "goal_id": identity,
            "values": values,
            "charts": {},
            "sources": [],
            "status_date": TODAY,
            "next_refresh_at": int(time.time()) + 86400,
        }

    def accept(self, route, path, body):
        self.version += 1
        if path.endswith("/manual-writes"):
            measure = path.split("/")[3]
            date = body["date"]
            values = self.observations[measure]
            basis = max(values) if body["basis"] == "latest" else date
            values[date] = (
                body["value"]
                if body["operation"] == "replace"
                else values.get(basis, 0) + body["delta"]
            )
            self.manual_revisions[measure] += 1
            route.fulfill(
                json={
                    "receipt": {"request_id": body["request_id"]},
                    "state": self.state(measure, date),
                    "replayed": False,
                }
            )
        else:
            identity = path.split("/")[3]
            goal = self.goals[identity]
            assert body["expected_revision"] == goal["revision"], body
            goal.update({key: value for key, value in body.items() if key != "expected_revision"})
            goal["revision"] += 1
            if goal["primary_checklist_block_id"]:
                items = goal["page"]["blocks"][0]["items"]
                goal["completed"] = bool(items) and all(item["state"] == "done" for item in items)
            goal["status"] = "good" if goal["completed"] else "neutral"
            route.fulfill(json=goal)

    def reject_held(self):
        route, _, _ = self.held.pop(0)
        route.fulfill(status=503, json={"error": {"code": "fixture", "message": "Save rejected"}})
        self.hold = False

    def respond(self, route):
        request = route.request
        parts = urlsplit(request.url)
        path, query = parts.path, parse_qs(parts.query)
        if request.method in ("POST", "PATCH") and (
            path.endswith("/manual-writes") or path.startswith("/api/goals/")
        ):
            body = request.post_data_json
            self.writes.append((path, copy.deepcopy(body)))
            if self.hold:
                self.held.append((route, path, body))
            else:
                self.accept(route, path, body)
            return
        if path == "/api/navigation/view/snapshot":
            source = self.navigation["navigation_response"](
                {"managers": []}, {"chief_of_staff": {"label": "Chief of Staff"}}
            )
            source["snapshot"]["records"].extend(
                {
                    "kind": "goal",
                    "id": goal["id"],
                    "value": {
                        key: goal[key]
                        for key in (
                            "title",
                            "conversation_id",
                            "active",
                            "completed",
                            "created_at",
                            "updated_at",
                        )
                    },
                }
                for goal in self.goals.values()
            )
            route.fulfill(
                json=self.navigation["scoped_navigation_response"](
                    source, json.loads(query["view"][0])
                )
            )
            return
        if path.startswith("/api/measures/"):
            if self.hold_cumulative_read and path == "/api/measures/cumulative_target/manual-state":
                self.held_reads.append(route)
                return
            route.fulfill(json=self.state(path.split("/")[3], query.get("date", [TODAY])[0]))
            return
        key = query.get("key", [path + ("?" + parts.query if parts.query else "")])[0]
        if key == "/api/goals":
            result = {
                "goals": list(self.goals.values()),
                "status_date": TODAY,
                "next_refresh_at": int(time.time()) + 86400,
            }
        elif key.endswith("/conversation/start-values"):
            result = {"backend_key": "codex", "model": None, "reasoning_effort": None}
        elif key.startswith("/api/artifact-catalog/"):
            result = {"items": [], "total": 0, "has_more": False, "next_offset": None}
        elif key.startswith("/api/goals/"):
            identity = key.split("/")[3]
            result = self.readings(identity) if key.endswith("/readings") else self.goals[identity]
        elif key.startswith("/api/projects"):
            result = {"projects": [{"id": "project", "name": "Field guide"}]}
        else:
            result = {
                "/api/worker-types": {"worker_types": []},
                "/api/feedback/count": {"open_count": 0},
                "/api/review": {"items": [], "running_worker_count": 0},
                "/api/meta": {"test_mode": True, "app_sha": "fixture"},
                "/api/conversation/backends": {"backends": []},
                "/api/workers/chief-of-staff/settings": {
                    "employee_id": "chief",
                    "label": "Chief of Staff",
                    "recap": "",
                    "skill": None,
                    "launch_defaults": {
                        "employee_backend": "codex",
                        "employee_launch_model": None,
                        "employee_launch_reasoning_effort": None,
                    },
                },
            }.get(key)
            if result is None:
                route.fulfill(status=503, json={"error": {"code": "fixture", "message": key}})
                return
        route.fulfill(
            json={
                "key": key,
                "epoch": "goal-fixture",
                "revision": str(self.version),
                "body": result,
            }
            if path == "/api/resource-body"
            else result
        )


def open_preview(page, identity):
    row = page.locator(f'a[data-goal-id="{identity}"]')
    # Native programmatic focus follows the browser's current input modality.
    # A keyboard user establishes focus-visible before choosing the row.
    page.keyboard.press("Shift")
    row.focus()
    panel = page.locator(f'[data-goal-preview="{identity}"]')
    expect(panel).to_be_visible()
    expect(panel.locator("h1")).to_have_text(re.compile(".+"))
    return panel


def dismiss(page):
    page.mouse.move(1000, 600)
    page.locator(".board-workspace-right").click(position={"x": 30, "y": 30})
    expect(page.locator("[data-goal-preview]")).to_have_count(0)


def native_manual_controls(page, fixture):
    for model in ("daily_quota", "cumulative_target", "individual_readings"):
        panel = open_preview(page, model)
        value = panel.get_by_role("textbox")
        expect(value).to_have_value("3")
        value.fill("invalid")
        value.press("Enter")
        expect(value).to_have_attribute("aria-invalid", "true")
        before = len(fixture.writes)
        if model != "individual_readings":
            panel.get_by_role("button", name="+1", exact=True).click()
        expect(panel.get_by_role("alert")).to_contain_text("Enter a number.")
        assert len(fixture.writes) == before
        value.press("Escape")
        expect(value).to_have_value("3")
        expect(panel).to_be_visible()  # Native defaultPrevented Escape wins first.
        value.fill("7")
        value.press("Enter")
        expect(value).to_have_value("7")
        expect(value).to_have_attribute("data-reading", "7")
        path, body = fixture.writes[-1]
        assert path == f"/api/measures/{model}/manual-writes"
        assert (body["value"], body["basis"], body["date"]) == (
            7,
            "latest" if model == "cumulative_target" else "date",
            TODAY,
        )
        if model == "daily_quota":
            panel.get_by_role("button", name=re.compile("29 Oct,")).click()
            expect(value).to_have_value("2")
            value.fill("8")
            value.press("Tab")
            expect(value).to_have_attribute("data-reading", "8")
            assert fixture.writes[-1][1]["date"] == YESTERDAY
        elif model == "individual_readings":
            plot = panel.get_by_role("slider")
            plot.focus()
            plot.press("End")
            plot.press("ArrowLeft")
            plot.press("Enter")
            expect(value).to_have_value("2")
            value.fill("8")
            value.press("Tab")
            expect(value).to_have_attribute("data-reading", "8")
            assert fixture.writes[-1][1]["date"] == YESTERDAY
        else:
            panel.get_by_role("button", name="+1", exact=True).click()
            expect(value).to_have_attribute("data-reading", "8")
            assert fixture.writes[-1][1]["operation"] == "increment"
        dismiss(page)


def late_failure_sessions(page, fixture):
    # Both dismissal and replacement must preserve failed receipts for native retry.
    for replacement in (False, True):
        panel = open_preview(page, "daily_quota")
        value = panel.get_by_role("textbox")
        fixture.hold = True
        value.fill("14" if replacement else "13")
        with page.expect_request("**/api/measures/daily_quota/manual-writes"):
            if replacement:
                open_preview(page, "binary")  # Replacement blurs while the field is mounted.
            else:
                dismiss(page)
        expect(page.locator('[data-goal-preview="daily_quota"]')).to_have_count(0)
        page.wait_for_function("async () => await window.__fixturePendingWrites() === 1")
        assert len(fixture.held) == 1, fixture.writes
        original = copy.deepcopy(fixture.writes[-1])
        fixture.reject_held()
        panel = open_preview(page, "daily_quota")
        expect(panel.get_by_role("alert").first).to_contain_text("Save rejected")
        count = len(fixture.writes)
        # Reading/reopening is not permission to retry the failed write.
        expect(panel.get_by_role("button", name="Retry", exact=True).first).to_be_visible()
        assert len(fixture.writes) == count
        panel.get_by_role("button", name="Retry", exact=True).first.click()
        expect(panel.get_by_role("textbox")).to_have_attribute(
            "data-reading", str(original[1]["value"])
        )
        assert fixture.writes[-1] == original
        assert len(fixture.writes) == count + 1
        dismiss(page)

        panel = open_preview(page, "checklist")
        expect(panel.get_by_role("progressbar")).to_have_attribute(
            "aria-valuenow", "2" if replacement else "1"
        )
        if replacement:
            panel.get_by_role("button", name="2 completed", exact=True).click()
        fixture.hold = True
        with page.expect_request("**/api/goals/checklist"):
            panel.get_by_role("checkbox", name="Publish guide", exact=True).click()
            expect(panel.get_by_text("Saving checklist…")).to_be_visible()
            if replacement:
                open_preview(page, "binary")
            else:
                dismiss(page)
        page.wait_for_function("async () => await window.__fixturePendingWrites() === 1")
        assert len(fixture.held) == 1
        fixture.reject_held()
        # Publish newer accepted data before reopening. Changing it after the
        # mount read starts instead creates a legitimate stale-revision conflict.
        fixture.goals["checklist"]["revision"] += 1
        fixture.goals["checklist"]["page"]["blocks"][0]["items"][0]["label"] = (
            "A newer accepted draft"
        )
        fixture.version += 1
        panel = open_preview(page, "checklist")
        expect(panel.get_by_role("alert")).to_contain_text("Save rejected")
        panel.get_by_role(
            "button", name="2 completed" if replacement else "1 completed", exact=True
        ).click()
        expect(panel.get_by_role("checkbox", name="Publish guide", exact=True)).to_have_attribute(
            "aria-checked", "true" if replacement else "false"
        )
        count = len(fixture.writes)
        # Retry must apply the item command to this newly accepted document.
        expect(
            panel.get_by_role("checkbox", name="A newer accepted draft", exact=True)
        ).to_be_visible()
        panel.get_by_role("button", name="Retry save", exact=True).click()
        expect(panel.get_by_role("progressbar")).to_have_attribute(
            "aria-valuenow", "1" if replacement else "2"
        )
        assert len(fixture.writes) == count + 1
        assert fixture.writes[-1][0] == "/api/goals/checklist"
        assert (
            fixture.goals["checklist"]["page"]["blocks"][0]["items"][0]["label"]
            == "A newer accepted draft"
        )
        dismiss(page)


def cumulative_preflight_lifetime(page, fixture):
    panel = open_preview(page, "cumulative_target")
    value = panel.get_by_role("textbox")
    expect(value).to_have_attribute("data-reading", "8")
    before = len(fixture.writes)
    fixture.hold_cumulative_read = True
    value.fill("19")
    with page.expect_request("**/api/measures/cumulative_target/manual-state"):
        dismiss(page)
    panel = open_preview(page, "cumulative_target")
    expect(panel.locator("[data-manual-model]")).to_have_attribute("aria-busy", "true")
    expect(panel.get_by_role("textbox")).to_have_value("19")
    assert len(fixture.writes) == before  # No write precedes the current-date read.
    fixture.hold_cumulative_read = False
    for route in fixture.held_reads:
        route.fulfill(status=503, json={"error": {"code": "fixture", "message": "Read rejected"}})
    fixture.held_reads.clear()
    expect(panel.get_by_role("alert").first).to_contain_text("Read rejected")
    expect(panel.get_by_role("button", name="Retry", exact=True).first).to_be_visible()
    assert len(fixture.writes) == before
    panel.get_by_role("button", name="Retry", exact=True).first.click()
    expect(panel.get_by_role("textbox")).to_have_attribute("data-reading", "19")
    path, body = fixture.writes[-1]
    assert path == "/api/measures/cumulative_target/manual-writes"
    assert (body["value"], body["basis"], body["date"]) == (19, "latest", TODAY)
    assert len(fixture.writes) == before + 1
    dismiss(page)


def native_owner_interactions(page):
    panel = open_preview(page, "daily_quota")
    row = page.locator('a[data-goal-id="daily_quota"]')
    row.press("Tab")
    expect(panel.get_by_role("textbox")).to_be_focused()
    panel.get_by_role("textbox").fill("42")
    panel.get_by_role("textbox").press("Escape")
    expect(panel).to_be_visible()
    # Outer bubble Escape dismisses after native cancellation and restores the row.
    page.keyboard.press("Escape")
    expect(panel).to_have_count(0)
    expect(row).to_be_focused()
    page.mouse.move(1000, 600)
    row.hover()
    expect(panel).to_be_visible()
    panel.hover()
    other = open_preview(page, "binary")
    expect(other).to_be_visible()
    # A real old anchor's late leave event cannot dismiss its replacement.
    row.dispatch_event("pointerleave", {"pointerType": "mouse"})
    other.hover()
    expect(other).to_be_visible()
    page.mouse.move(1000, 600)
    expect(other).to_have_count(0)
    panel = open_preview(page, "long")
    panel.hover()
    assert panel.evaluate("node => node.scrollHeight > node.clientHeight")
    panel.evaluate("node => node.scrollTop = 400")
    expect(panel).to_be_visible()
    # A real click establishes pointer input mode. A later generic scroll event
    # can come from accepted-state reflow and is not itself user scroll intent.
    panel.get_by_role("button", name="Long item 12", exact=True).click()
    page.locator("[data-active-work-scroll]").evaluate(
        'node => node.dispatchEvent(new Event("scroll"))'
    )
    expect(panel).to_be_visible()
    page.keyboard.press("Tab")
    expect(panel).to_be_visible()
    # Keyboard focus scroll repositions instead of dismissing the preview.
    page.locator("[data-active-work-scroll]").evaluate(
        'node => node.dispatchEvent(new Event("scroll"))'
    )
    expect(panel).to_be_visible()
    page.mouse.move(1000, 600)
    page.mouse.wheel(0, 100)
    expect(panel).to_have_count(0)


def touch_overlap(browser, url):
    context = browser.new_context(
        viewport={"width": 390, "height": 700}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    fixture = GoalFixture()
    page.route("**/api/**", fixture.respond)
    page.goto(url + "#/workspace", wait_until="domcontentloaded")
    row = page.locator('a[data-goal-id="binary"]')
    expect(row).to_be_visible()
    bounds = row.bounding_box()
    assert bounds
    session = context.new_cdp_session(page)
    point = {"x": bounds["x"] + bounds["width"] / 2, "y": bounds["y"] + bounds["height"] / 2}
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
    panel = page.locator('[data-goal-preview="binary"]')
    expect(panel).to_be_visible()
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    expect(panel).to_be_visible()
    assert page.url.endswith("#/workspace")
    # At phone width the overlay is above its rail. A native tap reaches the action
    # and its click does not bubble into the enclosing Chief card.
    panel.get_by_role("button", name="Mark done", exact=True).tap()
    expect(panel.get_by_role("button", name="Mark not done", exact=True)).to_be_visible()
    assert page.url.endswith("#/workspace")
    box = panel.bounding_box()
    assert box and box["x"] >= 12 and box["x"] + box["width"] <= 378
    page.touchscreen.tap(5, 5)
    expect(panel).to_have_count(0)
    assert page.url.endswith("#/workspace")

    # A short phone's footer must not cover the final native checklist control.
    page.set_viewport_size({"width": 320, "height": 568})
    long_row = page.locator('a[data-goal-id="long"]')
    long_row.scroll_into_view_if_needed()
    bounds = long_row.bounding_box()
    assert bounds
    point = {"x": bounds["x"] + bounds["width"] / 2, "y": bounds["y"] + bounds["height"] / 2}
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [point]})
    long_panel = page.locator('[data-goal-preview="long"]')
    expect(long_panel).to_be_visible()
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    long_panel.evaluate("node => node.scrollTop = node.scrollHeight")
    last = long_panel.get_by_role("checkbox", name="Long item 29", exact=True)
    last.tap()
    expect(last).to_have_attribute("aria-checked", "true")
    box = long_panel.bounding_box()
    footer = page.locator(".workspace-mobile-nav").bounding_box()
    assert box and footer and box["y"] + box["height"] <= footer["y"] - 12
    page.touchscreen.tap(5, 5)
    expect(long_panel).to_have_count(0)
    # A normal tap still follows the real goal destination.
    row.tap()
    expect(page).to_have_url(re.compile(r"#/workspace/goal/binary$"))
    context.close()


def agent_chart_fixture():
    fixture = GoalFixture()
    # Retained native-authored primary chart and daily counts from discovery-integrated/
    # {goal,readings}-goal_z0g497zt.json in ticket t_waf8g2v3. Keep the ordinary unit
    # verbatim: this proof must not repair a saved chart with prefixed whitespace.
    goal = fixture.goal("goal_z0g497zt", "Reduce captured exception events in Vylo Dev", 6)
    goal["kind"] = "quantitative"
    goal["primary_chart"] = {
        "type": "chart", "kind": "line",
        "title": "Vylo Dev — captured exception events per UTC date",
        "measure_id": "measure_q2v1r9mf", "unit": "captured exception events",
        "period": "2026-09-29–2026-10-29 (UTC)",
        "caption": "UTC dates, 2026-09-29 through 2026-10-29. September 29 starts at "
        "06:11:26 UTC (partial). October 29 ends at 2026-10-29 22:06:17 UTC (partial). "
        "Captured counts only. Provider completeness is unverified. No full-day compliance claim.",
        "format": {"decimals": 0}, "id": "chart_zpvw2fud", "placement": "primary",
    }
    # Derived fixture blocks exercise disclosure and the gauge's split unit span.
    # They do not change the retained primary definition or any persisted data.
    goal["page"]["blocks"] = [
        {**goal["primary_chart"], "id": "supporting-events", "placement": "supporting",
         "title": "Supporting captured events"},
        {"type": "chart", "kind": "gauge", "id": "gauge-events",
         "placement": "supporting", "title": "Captured event gauge",
         "measure_id": "measure_q2v1r9mf", "unit": "events", "format": {"decimals": 0}},
    ]
    limitation = (
        "Captured events only; provider ingestion completeness is not established. "
        "No full-day compliance claim."
    )
    values = []
    for index in range(31):
        day = str(date(2026, 9, 29) + timedelta(days=index))
        partial = index in (0, 30)
        reason = "Partial UTC date. " + limitation if partial else limitation
        values.append({
            "measure_id": "measure_q2v1r9mf", "date": day,
            "definition_revision": 1, "status": "value", "value": 2 if day == "2026-10-25" else 0,
            "inputs": [], "missing_inputs": [],
            "query_start_at": 1790662286 if index == 0 else 1790640000 + index * 86400,
            "query_end_at": 1793311577 if index == 30 else 1790640000 + (index + 1) * 86400,
            "limitation": reason, "coverage_status": "partial",
            "coverage_reasons": [reason] + ([
                "The query interval does not match the complete local period."
            ] if partial else []),
        })
    fixture.reading_overrides[goal["id"]] = {
        "goal_id": goal["id"], "values": values, "charts": {}, "sources": [],
        "status_date": TODAY, "next_refresh_at": int(time.time()) + 86400,
    }
    return fixture


def rendered_counter(counter, value):
    reading = f"{value} captured exception events"
    expect(counter.locator(":scope > span").first).to_have_text(reading)
    assert reading in counter.aria_snapshot()
    visual = counter.locator(':scope > [aria-hidden="true"]')
    expect(visual.locator(f'[data-digit="{value}"]')).to_have_css("opacity", "1")
    suffix = visual.locator(":scope > span").last
    expect(suffix).to_have_text(" captured exception events", use_inner_text=False)
    return rendered_separator(suffix)


def rendered_separator(suffix):
    # textContent alone passes when CSS collapses a flex item's leading space.
    geometry = suffix.evaluate("""node => {
        const range = document.createRange();
        range.setStart(node.firstChild, 0); range.setEnd(node.firstChild, 1);
        return {spaceWidth: range.getBoundingClientRect().width,
                whiteSpace: getComputedStyle(node).whiteSpace};
    }""")
    assert geometry["whiteSpace"] in ("pre", "pre-wrap", "break-spaces"), geometry
    assert geometry["spaceWidth"] > 0, geometry
    return geometry


def agent_unit_spacing(browser, url):
    started = time.monotonic()
    artifact_root = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    output = Path(artifact_root) / "goal-unit-spacing" if artifact_root else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    receipts = []
    for phone in (False, True):
        mode = "phone" if phone else "desktop"
        context = browser.new_context(
            viewport={"width": 390 if phone else 1280, "height": 844 if phone else 900},
            has_touch=phone, is_mobile=phone, reduced_motion="reduce",
        )
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        fixture = agent_chart_fixture()
        page.route("**/api/**", fixture.respond)
        try:
            page.goto(url + "#/workspace/goal/goal_z0g497zt", wait_until="domcontentloaded")
            primary = page.locator('[data-chart-id="chart_zpvw2fud"]')
            primary.get_by_role("button", name="All time", exact=True).click()
            plot = primary.get_by_role("slider")
            expect(plot).to_have_attribute("aria-valuemax", "31")
            counter = primary.locator(".metric-value > span")
            zero = rendered_counter(counter, 0)
            session = context.new_cdp_session(page) if phone else None
            if not phone:
                plot.focus()
            # Chromium's touch adjustment can select the axis gutter at the edge.
            # Inspect an interior retained zero on phone, and the latest zero by keyboard.
            zero_point = (15, 0, "2026-10-14") if phone else (30, 0, "2026-10-29")
            for index, value, day in (zero_point, (26, 2, "2026-10-25")):
                if phone:
                    plot.scroll_into_view_if_needed()
                    box = plot.bounding_box()
                    assert box
                    point = {"x": box["x"] + box["width"] * index / 30,
                             "y": box["y"] + box["height"] / 2}
                    session.send("Input.dispatchTouchEvent", {
                        "type": "touchStart", "touchPoints": [point],
                    })
                else:
                    plot.press("End")
                    for _ in range(30 - index):
                        plot.press("ArrowLeft")
                expect(plot).to_have_attribute(
                    "aria-valuetext", f"{day}: Current {value} captured exception events"
                )
                expect(primary.locator('[data-tooltip-series="actual"] > span').last).to_have_text(
                    f"{value} captured exception events"
                )
                rendered_counter(counter, value)
                if output:
                    primary.screenshot(path=str(output / f"{mode}-{value}-events.png"))
                if phone:
                    session.send("Input.dispatchTouchEvent", {
                        "type": "touchEnd", "touchPoints": [],
                    })
            supporting = page.locator('[data-chart-id="supporting-events"]')
            if phone:
                summary = supporting.get_by_role(
                    "button", name=re.compile("Supporting captured events")
                )
                expect(summary).to_have_attribute("aria-expanded", "false")
                rendered_counter(supporting.locator(".support-reading > span"), 0)
                summary.tap()
                expect(summary).to_have_attribute("aria-expanded", "true")
                expect(supporting.get_by_role("slider")).to_be_visible()
                if output:
                    supporting.screenshot(path=str(output / "phone-supporting-expanded.png"))
                summary.tap()
                expect(summary).to_have_attribute("aria-expanded", "false")
            else:
                rendered_counter(supporting.locator(".metric-value > span"), 0)
            gauge = page.locator('[data-chart-id="gauge-events"]')
            if phone:
                gauge.get_by_role("button", name=re.compile("Captured event gauge")).tap()
            meter = gauge.get_by_role("meter")
            meter.scroll_into_view_if_needed()
            expect(meter).to_have_attribute("aria-valuetext", "0 events")
            unit = gauge.locator('figure [aria-hidden="true"] span').filter(
                has_text=re.compile(r"^ events$")
            )
            expect(unit).to_have_count(1)
            gauge_space = rendered_separator(unit)
            if output:
                gauge.screenshot(path=str(output / f"{mode}-gauge.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert fixture.writes == [], fixture.writes
            assert errors == [], errors
            receipts.append({"mode": mode, "counter": zero, "gauge": gauge_space, "errors": errors})
        finally:
            context.close()
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"goal chart unit spacing desktop/phone: passed (+{elapsed:.2f}s)", flush=True)


def manual_unit_spacing(browser, url):
    # Shared formatting tests own the suffix matrix. This boundary proves that
    # manual controls, comparisons, and split flex counters use that contract.
    started = time.monotonic()
    artifact_root = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    output = Path(artifact_root) / "manual-unit-spacing" if artifact_root else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    receipts = []
    for phone in (False, True):
        mode = "phone" if phone else "desktop"
        context = browser.new_context(
            viewport={"width": 390 if phone else 1280, "height": 844 if phone else 900},
            has_touch=phone, is_mobile=phone, reduced_motion="reduce",
        )
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        fixture = GoalFixture()
        for model in ("daily_quota", "cumulative_target", "individual_readings"):
            fixture.goals[model]["primary_chart"].update(
                unit="minutes", format={"decimals": 2 if model == "individual_readings" else 0}
            )
            fixture.observations[model] = {TODAY: 12 if model == "daily_quota" else 12.25,
                                           YESTERDAY: 22.5}
        fixture.coverage[("daily_quota", TODAY)] = "unknown"
        fixture.coverage[("cumulative_target", TODAY)] = "partial"
        primary = fixture.goals["individual_readings"]["primary_chart"]
        fixture.goals["individual_readings"]["page"]["blocks"] = [
            {**primary, "id": "supporting-minutes", "placement": "supporting",
             "title": "Supporting minutes"},
            # Two probes protect precedence at the manual caller, without
            # repeating the shared symbol/currency/whitespace matrix.
            *[{**primary, "id": identity, "placement": "supporting", "title": identity,
               "format": {"decimals": 2, "suffix": suffix}}
              for identity, suffix in (("literal-suffix", "h"), ("empty-suffix", ""))],
        ]
        page.route("**/api/**", fixture.respond)
        renders = {}
        try:
            for model, expected in (
                ("daily_quota", "Coverage unavailable · "
                 "Difference (coverage unavailable): -8 minutes"),
                ("cumulative_target", "Partial reading · Partial difference: -7.75 minutes"),
                ("individual_readings", "7.75 minutes to a 20 minutes reading"),
            ):
                page.goto(url + f"#/workspace/goal/{model}", wait_until="domcontentloaded")
                chart = page.locator(f'[data-chart-id="{model}"]')
                comparison = chart.locator(
                    ".completion" if model == "cumulative_target" else ".comparison"
                )
                expect(comparison).to_have_text(expected)
                assert expected in chart.aria_snapshot()
                renders[model] = {"text": chart.inner_text(), "aria": chart.aria_snapshot()}
                if output:
                    chart.screenshot(path=str(output / f"{mode}-{model}.png"))

            suffix = chart.locator(".number-row > .unit")
            expect(suffix).to_have_text(" minutes", use_inner_text=False)
            renders["inline_separator"] = rendered_separator(suffix)
            expect(chart.locator(".number-row")).to_have_css("gap", "0px")
            supporting = page.locator('[data-chart-id="supporting-minutes"]')
            if phone:
                summary = supporting.get_by_role("button", name=re.compile("Supporting minutes"))
                expect(summary).to_have_attribute("aria-expanded", "false")
                counter = supporting.locator(".support-reading > span")
                expect(counter.locator(":scope > span").first).to_have_text("12.25 minutes")
                assert "12.25 minutes" in summary.aria_snapshot()
                visual_suffix = counter.locator(':scope > [aria-hidden="true"] > span').last
                expect(visual_suffix).to_have_text(" minutes", use_inner_text=False)
                renders["summary_separator"] = rendered_separator(visual_suffix)
                renders["collapsed_summary"] = summary.aria_snapshot()
                if output:
                    supporting.screenshot(path=str(output / "phone-collapsed-summary.png"))
            else:
                for identity, expected in (("literal-suffix", "7.75h to a 20h reading"),
                                           ("empty-suffix", "7.75 to a 20 reading")):
                    probe = page.locator(f'[data-chart-id="{identity}"]')
                    expect(probe.locator(".comparison")).to_have_text(expected)
                    expect(probe.locator(".number-row > .unit")).to_have_count(
                        1 if identity == "literal-suffix" else 0
                    )
                    if identity == "literal-suffix":
                        expect(probe.locator(".unit")).to_have_text("h", use_inner_text=False)
                    renders[identity] = probe.aria_snapshot()

            plot = chart.get_by_role("slider")
            session = context.new_cdp_session(page) if phone else None
            if phone:
                plot.scroll_into_view_if_needed()
                box = plot.bounding_box()
                assert box
                session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [
                    {"x": box["x"] + box["width"] * 5 / 6, "y": box["y"] + box["height"] / 2}
                ]})
            else:
                plot.focus()
                plot.press("End")
                plot.press("ArrowLeft")
            expect(plot).to_have_attribute(
                "aria-valuetext", "29 Oct: Reading 22.50 minutes, 20 minutes target 20.00 minutes"
            )
            expect(chart.locator('[data-tooltip-series="actual"] > span').last).to_have_text(
                "22.50 minutes"
            )
            renders["inspection"] = {"text": chart.inner_text(), "aria": chart.aria_snapshot()}
            if output:
                chart.screenshot(path=str(output / f"{mode}-line-inspection.png"))
            if phone:
                # Touch inspection ends without an explicit reading selection.
                session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            else:
                plot.press("Enter")
                expect(chart.get_by_role("textbox")).to_have_attribute("data-reading", "22.5")
                expect(chart.locator(".comparison")).to_have_text("20 minutes target reached")
            assert fixture.writes == [], fixture.writes
            assert errors == [], errors
            receipts.append({"mode": mode, "renders": renders, "errors": errors})
        finally:
            context.close()
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"manual unit spacing desktop/phone: passed (+{elapsed:.2f}s)", flush=True)


def run(browser, urls):
    started = time.monotonic()
    fixture = GoalFixture()
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.expose_function("__fixturePendingWrites", lambda: len(fixture.held))
    page.route("**/api/**", fixture.respond)
    page.goto(urls[0] + "#/workspace", wait_until="domcontentloaded")
    expect(page.locator("a[data-goal-id]")).to_have_count(6)
    try:
        native_manual_controls(page, fixture)
        late_failure_sessions(page, fixture)
        cumulative_preflight_lifetime(page, fixture)
        native_owner_interactions(page)
    finally:
        if errors:
            print("Goal preview page errors:", errors, flush=True)
    assert errors == [], errors
    page.close()
    touch_overlap(browser, urls[0])
    print(
        f"goal preview native DOM/HTTP controls: passed ({time.monotonic() - started:.2f}s)",
        flush=True,
    )
    agent_unit_spacing(browser, urls[0])
    manual_unit_spacing(browser, urls[0])
