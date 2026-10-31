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
WRAPPED_LABEL = ("A completed checklist label long enough that it must wrap across "
                 "two full lines at both the narrow phone width and the wider "
                 "desktop width")


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
                        "coverage_status": self.coverage.get((identity, date), "not_applicable"),
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
            # A held write must not show pending-save text.
            expect(panel.get_by_text("Saving checklist…")).to_have_count(0)
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
    # A normal tap must stay quiet: no pending-save text, no panel height change.
    before = long_panel.bounding_box()
    last.tap()
    expect(last).to_have_attribute("aria-checked", "true")
    expect(long_panel.get_by_text("Saving checklist…")).to_have_count(0)
    after = long_panel.bounding_box()
    assert before and after and before["height"] == after["height"]
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
         "title": "Supporting captured events", "measure_id": "comparison-events",
         "target": 1, "direction": "atmost", "target_label": "limit"},
        {"type": "chart", "kind": "gauge", "id": "gauge-events",
         "placement": "supporting", "title": "Captured event gauge",
         "measure_id": "comparison-events", "unit": "events", "format": {"decimals": 0},
         "target": 1, "direction": "atmost", "target_label": "limit"},
        *[{"type": "chart", "kind": kind, "id": identity, "placement": "supporting",
           "title": identity, "target": 20, "stacked": stacked,
           "series": [
               {"name": "First", "points": [{"label": YESTERDAY, "value": 2},
                                               {"label": TODAY, "value": 3}]},
               {"name": "Second", "points": [{"label": YESTERDAY, "value": 4},
                                                {"label": TODAY, "value": 5}]},
           ]}
          for kind, identity, stacked in (("time-budget", "budget-events", False),
                                         ("allocation", "allocation-events", False),
                                         ("bar", "grouped-events", False),
                                         ("bar", "stacked-events", True))],
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
        "goal_id": goal["id"], "values": values + [
            {**value, "measure_id": "comparison-events",
             "coverage_status": "unknown" if value["date"] == "2026-10-29" else "partial"}
            for value in values
        ], "charts": {}, "sources": [],
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


def inspection_state(chart):
    return chart.evaluate("""node => {
        const canvas = node.querySelector('canvas');
        const observed = canvas ? globalThis.__goalCanvasInspection(canvas) : null;
        return {
            canvas: observed ? {opacity: observed.opacity, active: observed.active} : null,
            tips: [...node.querySelectorAll('[data-chart-tooltip], [role="tooltip"]')]
                .filter(tip => Number(getComputedStyle(tip).opacity) > .99)
                .map(tip => tip.textContent),
            active: node.querySelector('.allocation-legend')?.getAttribute('data-active') ?? null,
            headline: node.querySelector('.metric-value')?.getAttribute('data-current') ?? null,
        };
    }""")


def expect_inspection(page, chart, visible):
    deadline = time.monotonic() + 5
    while True:
        state = inspection_state(chart)
        actual = bool(state["tips"] or state["active"] is not None or (
            state["canvas"] and state["canvas"]["opacity"] > .99
        ))
        if actual == visible:
            return state
        assert time.monotonic() < deadline, (visible, state)
        page.wait_for_timeout(10)


def inspection_point(target, *, segment=False, canvas=False, fraction=.4):
    target.scroll_into_view_if_needed()
    box = target.bounding_box()
    assert box
    if canvas:
        point = target.evaluate("node => globalThis.__goalCanvasInspection(node).point")
        assert point
        return box["x"] + point["x"], box["y"] + point["y"]
    return (box["x"] + box["width"] * (.68 if segment else fraction),
            box["y"] + box["height"] * (.15 if segment else .5))


def desktop_inspection(page, chart, target, *, segment=False, canvas=False,
                       keyboard=True, output=None, name="inspection", fraction=.4,
                       after_click=None):
    x, y = inspection_point(target, segment=segment, canvas=canvas, fraction=fraction)
    page.mouse.move(x, y)
    hovered = expect_inspection(page, chart, True)
    # down/up deliberately preserve coordinates. A click cannot refresh, toggle,
    # or clear the hover, including when a real action moves native focus.
    page.mouse.down()
    page.mouse.up()
    if after_click:
        after_click()
    clicked = expect_inspection(page, chart, True)
    assert clicked == hovered, (name, hovered, clicked)
    if output:
        chart.screenshot(path=str(output / f"{name}-clicked.png"))
    page.mouse.move(0, 0)
    exited = expect_inspection(page, chart, False)
    if keyboard:
        def leave_with_tab():
            # A following date/legend can inspect too. Traverse native siblings
            # until focus actually leaves this chart before asserting dismissal.
            for _ in range(32):
                page.keyboard.press("Tab")
                if not chart.evaluate("node => node.contains(document.activeElement)"):
                    return expect_inspection(page, chart, False)
            raise AssertionError("Keyboard focus did not leave the chart")

        # Enter through native keyboard traversal, then cross back to mouse.
        target.focus()
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        if canvas:
            page.keyboard.press("ArrowLeft")
        expect_inspection(page, chart, True)
        leave_with_tab()
        # Isolate a mouse click from hover. Native focus is real, while renderer
        # events use the browser DOM because physical movement itself must inspect.
        target.dispatch_event("pointerdown", {"pointerType": "mouse", "button": 0})
        target.focus()
        target.dispatch_event("click", {"button": 0})
        target.dispatch_event("pointerup", {"pointerType": "mouse", "button": 0})
        expect_inspection(page, chart, False)
        # A fresh keyboard entry must recover after the mouse origin marker.
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        if canvas:
            page.keyboard.press("ArrowLeft")
        expect_inspection(page, chart, True)
        leave_with_tab()
    return {"hover": hovered, "click": clicked, "exit": exited}


def touch_inspection(page, session, chart, target, *, segment=False, canvas=False,
                     contact=True, retained=True, outside=False, fraction=.4):
    x, y = inspection_point(target, segment=segment, canvas=canvas, fraction=fraction)
    session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [
        {"x": x, "y": y},
    ]})
    down = expect_inspection(page, chart, contact)
    if canvas:
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
            {"x": x + 1, "y": y},
        ]})
        expect_inspection(page, chart, True)
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    up = expect_inspection(page, chart, retained)
    page.touchscreen.tap(5, 5)
    blurred = expect_inspection(page, chart, outside)
    return {"contact": down, "release": up, "outside": blurred}


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
                    # Element screenshots can scroll the chart during touch
                    # contact and cancel the gesture. Capture its viewport instead.
                    (page.screenshot if phone else primary.screenshot)(
                        path=str(output / f"{mode}-{value}-events.png")
                    )
                if phone:
                    session.send("Input.dispatchTouchEvent", {
                        "type": "touchEnd", "touchPoints": [],
                    })
                    expect_inspection(page, primary, False)
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
            # Source uncertainty stays factual while the observed values compare.
            if phone:
                summary.tap()
            comparison = supporting.locator(".comparison")
            expect(comparison).to_have_text("1 captured exception events under limit")
            expect(comparison).to_have_css("color", resolved_chart_color(page, "--accent-done"))
            expect(supporting.locator(".observation-details")).to_contain_text(
                "Source coverage for 2026-10-29: unknown."
            )
            support_plot = supporting.get_by_role("slider")
            support_plot.focus()
            support_plot.press("End")
            for _ in range(4):
                support_plot.press("ArrowLeft")
            expect(comparison).to_have_text("1 captured exception events over limit")
            expect(comparison).to_have_css("color", resolved_chart_color(page, "--accent-error"))
            expect(supporting.locator(".observation-details")).to_contain_text(
                "Source coverage for 2026-10-25: partial."
            )
            expect(supporting.locator(".observation-details")).to_contain_text(
                "Captured events only; provider ingestion completeness is not established."
            )
            support_plot.press("Escape")
            if phone:
                summary.tap()
            gauge = page.locator('[data-chart-id="gauge-events"]')
            if phone:
                gauge.get_by_role("button", name=re.compile("Captured event gauge")).tap()
            meter = gauge.get_by_role("meter")
            meter.scroll_into_view_if_needed()
            expect(meter).to_have_attribute(
                "aria-valuetext", "0 events, Within limit, 1 events limit"
            )
            expect(gauge.locator("figure")).to_have_class(re.compile(r".*success.*"))
            expect(gauge.locator(".observation-details")).to_contain_text(
                "Source coverage for 2026-10-29: unknown."
            )
            unit = gauge.locator('figure [aria-hidden="true"] span').filter(
                has_text=re.compile(r"^ events$")
            )
            expect(unit).to_have_count(1)
            gauge_space = rendered_separator(unit)
            if output:
                gauge.screenshot(path=str(output / f"{mode}-gauge.png"))
            inspection_started = time.monotonic()
            inspections = {}
            figure = gauge.locator("figure[tabindex]")
            if phone:
                inspections["gauge"] = touch_inspection(page, session, gauge, figure)
            else:
                inspections["gauge"] = desktop_inspection(
                    page, gauge, figure, output=output, name="desktop-gauge"
                )
            for identity in (
                "budget-events", "allocation-events", "grouped-events", "stacked-events"
            ):
                probe = page.locator(f'[data-chart-id="{identity}"]')
                if phone:
                    probe.get_by_role("button", name=re.compile(identity)).tap()
                canvas = identity in ("grouped-events", "stacked-events")
                if canvas:
                    target = probe.locator("canvas")
                    target.scroll_into_view_if_needed()
                    configuration = target.evaluate(
                        "node => globalThis.__goalCanvasInspection(node)"
                    )
                    assert configuration["stacked"] == (identity == "stacked-events")
                    assert "click" not in configuration["events"], configuration
                    assert {"mousemove", "mouseout", "touchstart", "touchmove"} <= set(
                        configuration["events"]
                    ), configuration
                    # Grouped/stacked use one renderer. Prove the second mode's
                    # event configuration without repeating its full input matrix.
                    if identity == "stacked-events":
                        continue
                else:
                    target = probe.locator(".allocation-legend button").last
                if phone:
                    inspections[identity] = touch_inspection(
                        page, session, probe, target, canvas=canvas,
                        contact=canvas, retained=identity != "allocation-events",
                    )
                    if not canvas:
                        inspections[identity + "-segment"] = touch_inspection(
                            page, session, probe, probe.locator(".ring-chart svg"),
                            segment=True, outside=True,
                        )
                else:
                    inspections[identity] = desktop_inspection(
                        page, probe, target, canvas=canvas,
                        output=output, name="desktop-" + identity,
                    )
                    if not canvas:
                        # Moving from a focused legend to its sibling must not
                        # let the first button's blur erase the second hover.
                        target = probe.locator(".allocation-legend button").first
                        inspections[identity + "-sibling"] = desktop_inspection(
                            page, probe, target, keyboard=False,
                        )
                        inspections[identity + "-segment"] = desktop_inspection(
                            page, probe, probe.locator(".ring-chart svg"), segment=True,
                            keyboard=False,
                        )
            if not phone:
                inspections["goal-line"] = desktop_inspection(
                    page, primary, plot, keyboard=False,
                    output=output, name="desktop-goal-line",
                )
            # Both hosts use the same automatic line renderer. Keep a single
            # representative read-only proof in the overview, without a chart matrix.
            page.goto(url + "#/workspace/chief-of-staff", wait_until="domcontentloaded")
            overview = page.locator('[data-goal-card="goal_z0g497zt"] .card-progress')
            target = overview.get_by_role("slider")
            if phone:
                inspections["overview-line"] = touch_inspection(
                    page, session, overview, target, retained=False,
                )
                x, y = inspection_point(target)
                session.send("Input.dispatchTouchEvent", {
                    "type": "touchStart", "touchPoints": [{"x": x, "y": y}],
                })
                expect_inspection(page, overview, True)
                session.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
                expect_inspection(page, overview, False)
            else:
                inspections["overview-line"] = desktop_inspection(
                    page, overview, target, output=output, name="desktop-overview-line"
                )
            inspection_seconds = time.monotonic() - inspection_started
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert fixture.writes == [], fixture.writes
            assert errors == [], errors
            receipts.append({"mode": mode, "counter": zero, "gauge": gauge_space,
                             "inspection_seconds": inspection_seconds,
                             "inspections": inspections, "errors": errors})
        finally:
            context.close()
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"goal chart unit spacing desktop/phone: passed (+{elapsed:.2f}s)", flush=True)


def resolved_chart_color(page, token):
    return page.evaluate("""token => {
        const probe = document.createElement('span');
        probe.style.color = `var(${token})`; document.body.append(probe);
        const color = getComputedStyle(probe).color; probe.remove(); return color;
    }""", token)


def gauge_visual_equivalence(browser, url):
    """Measure the extraction boundary in the existing application/HTTP host.

    Existing helpers own the full pointer matrix. These probes add the missing
    gauge layout, native-input accessibility, and two distinct motion mechanisms.
    The same file runs before and after extraction; receipts use semantic keys.
    """
    started = time.monotonic()
    artifact_root = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    output = Path(artifact_root) / "gauge-equivalence" if artifact_root else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    receipts = []
    recorder = """() => {
      const records = [];
      const watched = new WeakMap();
      let phase = 'reveal';
      function watch(meter) {
        if (watched.has(meter)) return;
        const token = {};
        watched.set(meter, token);
        const host = meter.closest('[data-chart-id]');
        if (!host) return;
        const manual = !!meter.closest('.manual-measurement');
        const record = {chart:host.dataset.chartId, manual, phase, samples:[]};
        records.push(record);
        const start = performance.now();
        function sample(now) {
          if (!meter.isConnected || watched.get(meter) !== token) return;
          const fill = meter.querySelectorAll('path')[1];
          const readout = meter.nextElementSibling;
          const style = getComputedStyle(fill);
          const fraction = manual ? parseFloat(style.strokeDasharray) :
            fill.getTotalLength() / (42 * Math.PI * 1.5);
          const digits = readout?.querySelector('[class*="digits"]');
          record.samples.push({ms:now-start, fraction,
            opacity:Number(style.opacity), stroke:style.stroke,
            count:digits ? Number(digits.textContent) : null});
          if (now-start < 1800) requestAnimationFrame(sample);
        }
        requestAnimationFrame(sample);
      }
      window.__gaugeEvidence = records;
      window.__gaugeArm = nextPhase => {
        phase = nextPhase;
        document.querySelectorAll('svg[role=meter]').forEach(meter => {
          watched.delete(meter); watch(meter);
        });
      };
      window.__gaugeReset = () => {
        records.length = 0;
        window.__gaugeArm('reveal');
      };
      new MutationObserver(() => document.querySelectorAll('svg[role=meter]')
        .forEach(watch)).observe(document, {childList:true,subtree:true});
    }"""

    def snapshot(chart):
        return chart.evaluate("""node => {
          const origin = node.getBoundingClientRect();
          const rect = element => {
            if (!element) return null;
            const box = element.getBoundingClientRect();
            return {x:box.x-origin.x,y:box.y-origin.y,width:box.width,height:box.height};
          };
          const meter = node.querySelector('svg[role=meter]');
          const readout = meter?.nextElementSibling;
          const input = node.querySelector('input');
          const summary = node.querySelector('.support-summary');
          const paths = meter ? [...meter.querySelectorAll('path')].slice(0,2) : [];
          return {host:{width:origin.width,height:origin.height},
            meter:rect(meter),readout:rect(readout),input:rect(input),summary:rect(summary),
            attributes:meter ? Object.fromEntries(['role','aria-label','aria-valuemin',
              'aria-valuemax','aria-valuenow','aria-valuetext','focusable']
              .map(name => [name,meter.getAttribute(name)])) : null,
            summaryExpanded:summary?.getAttribute('aria-expanded') ?? null,
            inputHidden:input ? !!input.closest('[aria-hidden=true],[inert]') : null,
            inputLabel:input?.getAttribute('aria-label') ?? null,
            inputValue:input?.value ?? null,
            readoutHidden:readout?.getAttribute('aria-hidden') ?? null,
            paths:paths.map(path => {const css=getComputedStyle(path);return {
              d:path.getAttribute('d'),stroke:css.stroke,strokeWidth:css.strokeWidth,
              strokeLinecap:css.strokeLinecap,opacity:css.opacity,
              dasharray:css.strokeDasharray};}),
            layout:readout ? {display:getComputedStyle(readout).display,
              gap:getComputedStyle(readout).gap,
              pointerEvents:getComputedStyle(readout).pointerEvents} : null};
        }""")

    def capture_geometry(chart):
        return chart.evaluate("""node => {
          const rect = element => {
            if (!element) return null;
            const box = element.getBoundingClientRect();
            return {x:box.x,y:box.y,width:box.width,height:box.height};
          };
          const matrix = element => {
            const value = element?.getScreenCTM();
            return value ? Object.fromEntries(['a','b','c','d','e','f']
              .map(key => [key,value[key]])) : null;
          };
          const meter = node.querySelector('svg[role=meter]');
          const fill = meter?.querySelectorAll('path')[1];
          return {chart:rect(node),visual:rect(meter?.parentElement),
            meter:rect(meter),readout:rect(meter?.nextElementSibling),
            input:rect(node.querySelector('input')),svgCTM:matrix(meter),
            fillCTM:matrix(fill),transform:getComputedStyle(node).transform,
            devicePixelRatio,viewport:{width:innerWidth,height:innerHeight},
            windowScroll:{x:scrollX,y:scrollY}};
        }""")

    def capture(page, chart, name):
        # Existing inspection helpers prove dismissal when the fade starts.
        # Capture only after that fade finishes, so a tooltip cannot ghost into
        # the paired image. Keep raw captures and their exact crop provenance.
        chart.scroll_into_view_if_needed()
        # Scrolling can put the figure under a stationary desktop pointer.
        # Leave through the real pointer path before waiting for its fade.
        page.mouse.move(0, 0)
        tooltip = chart.locator('[role="tooltip"]')
        if tooltip.count():
            expect(tooltip).to_have_attribute("aria-hidden", "true")
            expect(tooltip).to_have_css("opacity", "0")
        frames = ("() => new Promise(resolve => requestAnimationFrame("
                  "() => requestAnimationFrame(resolve)))")
        page.evaluate(frames)
        record = {"raw": {"before": capture_geometry(chart)}}
        chart.screenshot(path=str(output / f"{name}.png"))
        record["raw"]["after"] = capture_geometry(chart)
        original_style = chart.get_attribute("style")
        before = record["raw"]["after"]
        assert before["transform"] == "none", before
        origin = before["chart"]
        dx, dy = round(origin["x"]) - origin["x"], round(origin["y"]) - origin["y"]
        assert abs(dx) <= .5 and abs(dy) <= .5, (dx, dy)
        record["translation"] = {"x": dx, "y": dy}
        try:
            chart.evaluate("""(node, delta) => {
              node.style.transform = `translate(${delta.x}px, ${delta.y}px)`;
            }""", {"x": dx, "y": dy})
            page.evaluate(frames)
            record["aligned"] = {"before": capture_geometry(chart)}
            chart.screenshot(path=str(output / f"{name}-aligned.png"))
            record["aligned"]["after"] = capture_geometry(chart)
        finally:
            chart.evaluate("""(node, value) => {
              if (value === null) node.removeAttribute('style');
              else node.setAttribute('style', value);
            }""", original_style)
            page.evaluate(frames)
        assert chart.get_attribute("style") == original_style
        record["restored"] = capture_geometry(chart)
        return record

    for phone, reduced in ((False, False), (False, True), (True, False), (True, True)):
        mode = f"{'phone' if phone else 'desktop'}-{'reduced' if reduced else 'normal'}"
        context = browser.new_context(
            viewport={"width": 390 if phone else 1280, "height": 844 if phone else 900},
            has_touch=phone, is_mobile=phone,
            reduced_motion="reduce" if reduced else "no-preference",
        )
        page = context.new_page()
        page.set_default_timeout(5000)
        page.add_init_script(f"({recorder})()")
        settle_ms = 80 if reduced else 1900
        fixture = GoalFixture()
        fixture.observations["cumulative_target"] = {TODAY: 8}
        manual = fixture.goals["cumulative_target"]
        manual["page"]["blocks"] = [{**manual["primary_chart"],
            "id": "support-manual-gauge", "placement": "supporting"}]
        readonly = fixture.goal("readonly-gauge", "Read-only gauge", 6)
        readonly["kind"] = "quantitative"
        readonly["primary_chart"] = {
            "type": "chart", "kind": "gauge", "id": "readonly-gauge",
            "placement": "primary", "title": "Read-only gauge", "domain": [0, 20],
            "target": 12, "unit": "events", "ranges": [
                {"id": key, "label": label, "series": [{"name": "Current", "points": [
                    {"label": TODAY, "value": value}]}]}
                for key, label, value in (("week", "Week", 8), ("month", "Month", 16))
            ],
        }
        readonly["page"]["blocks"] = [{**readonly["primary_chart"],
            "id": "support-readonly-gauge", "placement": "supporting"}]
        page.route("**/api/**", fixture.respond)
        row = {"mode": mode, "surfaces": {}, "motion": {}, "focus": {}, "captures": {}}
        receipts.append(row)
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        try:
            for identity, support_id in (("cumulative_target", "support-manual-gauge"),
                                         ("readonly-gauge", "support-readonly-gauge")):
                # Hash navigation can retain the document and its recorder.
                page.evaluate("window.__gaugeReset?.()")
                page.goto(url + f"#/workspace/goal/{identity}", wait_until="domcontentloaded")
                chart = page.locator(f'[data-chart-id="{identity}"]')
                meter = chart.get_by_role("meter")
                expect(meter).to_be_visible()
                meter.scroll_into_view_if_needed()
                # Keep initial reveal samples before a screenshot waits for stability.
                page.wait_for_timeout(settle_ms)
                row["surfaces"][identity + ":normal"] = snapshot(chart)
                if identity == "cumulative_target":
                    value = chart.get_by_role("textbox")
                    expect(value).to_have_attribute("data-reading", "8")
                    assert not row["surfaces"][identity + ":normal"]["inputHidden"]
                    value.focus()
                    assert value.evaluate("node => node === document.activeElement")
                    row["focus"][identity] = value.get_attribute("aria-label")
                else:
                    figure = chart.locator("figure[tabindex]")
                    if phone:
                        touch_inspection(page, context.new_cdp_session(page), chart, figure)
                    else:
                        desktop_inspection(page, chart, figure)
                    row["focus"][identity] = figure.get_attribute("aria-label")
                if output:
                    row["captures"][identity + ":normal"] = capture(
                        page, chart, f"{mode}-{identity}-normal"
                    )

                support = page.locator(f'[data-chart-id="{support_id}"]')
                if phone:
                    summary = support.locator(".support-summary")
                    expect(summary).to_have_attribute("aria-expanded", "false")
                    row["surfaces"][identity + ":smallest"] = snapshot(support)
                    summary.tap()
                    expect(summary).to_have_attribute("aria-expanded", "true")
                expect(support.get_by_role("meter")).to_be_visible()
                support.get_by_role("meter").scroll_into_view_if_needed()
                page.wait_for_timeout(settle_ms)
                row["surfaces"][identity + ":support-expanded"] = snapshot(support)
                if output:
                    row["captures"][identity + ":support-expanded"] = capture(
                        page, support, f"{mode}-{identity}-support"
                    )
                if phone:
                    page.set_viewport_size({"width": 320, "height": 844})
                    row["surfaces"][identity + ":narrow320"] = snapshot(support)
                    page.set_viewport_size({"width": 390, "height": 844})

                page.evaluate("window.__gaugeArm('update')")
                if identity == "cumulative_target":
                    chart.get_by_role("button", name="+1", exact=True).click()
                    expect(chart.get_by_role("textbox")).to_have_attribute("data-reading", "9")
                else:
                    # The current preference starts at Month (16). Choose Week
                    # (8) to exercise a real sweep and threshold change.
                    chart.get_by_role("button", name="Week", exact=True).click()
                    expect(meter).to_have_attribute("aria-valuenow", "8")
                page.wait_for_timeout(settle_ms)
                row["surfaces"][identity + ":updated"] = snapshot(chart)
                row["motion"][identity] = page.evaluate(
                    "keys => window.__gaugeEvidence.filter(record => keys.includes(record.chart))",
                    [identity, support_id],
                )

            page.goto(url + "#/workspace/chief-of-staff", wait_until="domcontentloaded")
            for identity in ("cumulative_target", "readonly-gauge"):
                chart = page.locator(f'[data-goal-card="{identity}"] [data-chart-id="{identity}"]')
                expect(chart.get_by_role("meter")).to_be_visible()
                chart.scroll_into_view_if_needed()
                page.wait_for_timeout(settle_ms)
                row["surfaces"][identity + ":small"] = snapshot(chart)
                if output:
                    row["captures"][identity + ":small"] = capture(
                        page, chart, f"{mode}-{identity}-small"
                    )
            assert errors == [], errors
            row["errors"] = errors
        finally:
            if output:
                (output / "measurement.json").write_text(json.dumps({
                    "browser": browser.version, "seconds": time.monotonic() - started,
                    "modes": receipts,
                }, indent=2) + "\n")
            context.close()
    print(f"gauge extraction equivalence probes: passed (+{time.monotonic() - started:.2f}s)",
          flush=True)


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
            fixture.observations[model] = {
                TODAY: 12 if model == "daily_quota" else 12.25,
                YESTERDAY: 22 if model == "daily_quota" else 22.5,
                "2026-10-28": 20, "2026-10-26": 0,
            }
        fixture.goals["daily_quota"]["primary_chart"]["direction"] = "atmost"
        fixture.coverage[("daily_quota", YESTERDAY)] = "partial"
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
        inspections = {}
        inspection_seconds = 0
        session = context.new_cdp_session(page) if phone else None
        try:
            for model in ("daily_quota", "cumulative_target", "individual_readings"):
                page.goto(url + f"#/workspace/goal/{model}", wait_until="domcontentloaded")
                chart = page.locator(f'[data-chart-id="{model}"]')
                value = chart.get_by_role("textbox")
                expect(value).to_have_attribute(
                    "data-reading", "12" if model == "daily_quota" else "12.25"
                )
                expect(chart.locator(".comparison")).to_have_count(0)
                expect(chart.locator(".card-heading")).to_have_text(fixture.goals[model]["title"])
                assert "Coverage unavailable" not in chart.aria_snapshot()
                if model == "cumulative_target":
                    expect(chart.locator(".completion")).to_have_text("")
                    expect(chart.locator(".radial-fill")).to_have_css(
                        "stroke", resolved_chart_color(page, "--accent-error")
                    )
                elif model == "daily_quota":
                    today = chart.get_by_role("button", name=re.compile("^30 Oct,"))
                    yesterday = chart.get_by_role("button", name=re.compile("^29 Oct,"))
                    for day, token in (("30", "--accent-done"), ("29", "--accent-error"),
                                       ("28", "--accent-done"), ("26", "--accent-done")):
                        bar = chart.get_by_role(
                            "button", name=re.compile(f"^{day} Oct,")
                        ).locator(".bar")
                        expect(bar).to_have_css(
                            "background-color", resolved_chart_color(page, token)
                        )
                    # Inspection exposes a dated value but does not move the edit context.
                    if not phone:
                        yesterday.hover()
                        expect(value).to_have_attribute("data-reading", "12")
                        expect(today).to_have_attribute("aria-pressed", "true")
                    yesterday.focus()
                    expect(value).to_have_attribute("data-reading", "12")
                    expect(yesterday).to_have_attribute("aria-pressed", "false")
                    actual_tip = chart.locator('[data-tooltip-series="actual"] > span').last
                    target_tip = chart.locator('[data-tooltip-series="target"] > span').last
                    expect(actual_tip).to_have_text("22 minutes")
                    expect(target_tip).to_have_text("20 minutes")
                    target_label = chart.locator('[data-tooltip-series="target"] > span').nth(1)
                    assert target_label.evaluate("node => node.clientWidth >= node.scrollWidth")
                    missing = chart.get_by_role("button", name=re.compile("^27 Oct, no reading"))
                    expect(missing.locator(".bar")).to_have_class("bar missing")
                    expect(missing.locator(".bar")).to_have_css(
                        "background-color", "rgba(0, 0, 0, 0)"
                    )
                    expect(missing.locator(".bar")).to_have_css("border-top-style", "dashed")
                    missing.focus()
                    expect(actual_tip).to_have_text("No reading")
                    expect(value).to_have_attribute("data-reading", "12")
                    inspection_started = time.monotonic()
                    if phone:
                        # Release selects and inspects. Blur dismisses only the tip.
                        page.touchscreen.tap(5, 5)
                        inspections["goal-bars"] = touch_inspection(
                            page, session, chart, yesterday, contact=False,
                        )
                    else:
                        inspections["goal-bars"] = desktop_inspection(
                            page, chart, yesterday, output=output, name="desktop-goal-bars",
                        )
                    inspection_seconds += time.monotonic() - inspection_started
                    expect(value).to_have_attribute("data-reading", "22")
                    expect(value).to_have_attribute("aria-label", "Daily Quota for 29 Oct")
                    expect(yesterday).to_have_attribute("aria-pressed", "true")
                    if output:
                        chart.screenshot(path=str(output / f"{mode}-bar-inspection.png"))
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
                expect(summary.locator(".support-status")).to_have_text("")
                visual_suffix = counter.locator(':scope > [aria-hidden="true"] > span').last
                expect(visual_suffix).to_have_text(" minutes", use_inner_text=False)
                renders["summary_separator"] = rendered_separator(visual_suffix)
                renders["collapsed_summary"] = summary.aria_snapshot()
                if output:
                    supporting.screenshot(path=str(output / "phone-collapsed-summary.png"))
            else:
                for identity in ("literal-suffix", "empty-suffix"):
                    probe = page.locator(f'[data-chart-id="{identity}"]')
                    expect(probe.get_by_role("textbox")).to_have_attribute("data-reading", "12.25")
                    expect(probe.locator(".comparison")).to_have_count(0)
                    expect(probe.locator(".number-row > .unit")).to_have_count(
                        1 if identity == "literal-suffix" else 0
                    )
                    if identity == "literal-suffix":
                        expect(probe.locator(".unit")).to_have_text("h", use_inner_text=False)
                    renders[identity] = probe.aria_snapshot()

            plot = chart.get_by_role("slider")
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
                "aria-valuetext", "29 Oct: Reading 22.50 minutes, Target 20.00 minutes"
            )
            expect(chart.locator('[data-tooltip-series="actual"] > span').last).to_have_text(
                "22.50 minutes"
            )
            expect(chart.get_by_role("textbox")).to_have_attribute("data-reading", "12.25")
            expect(chart.locator('[data-dot="actual"]')).to_have_css(
                "fill", resolved_chart_color(page, "--accent-done")
            )
            renders["inspection"] = {"text": chart.inner_text(), "aria": chart.aria_snapshot()}
            if output:
                (page.screenshot if phone else chart.screenshot)(
                    path=str(output / f"{mode}-line-inspection.png")
                )
            if phone:
                # Release clears inspection and retains the action's edit date.
                session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                expect_inspection(page, chart, False)
                expect(value).to_have_attribute("data-reading", "22.5")
            else:
                plot.press("Enter")
                expect(chart.get_by_role("textbox")).to_have_attribute("data-reading", "22.5")
                expect(chart.get_by_role("textbox")).to_have_attribute(
                    "aria-label", "Individual Readings for 29 Oct"
                )
                expect(chart.locator(".comparison")).to_have_count(0)
                # The configured atleast direction includes equality. A missing point stays a gap.
                plot.press("Home")
                plot.press("ArrowRight")
                plot.press("ArrowRight")
                expect(plot).to_have_attribute(
                    "aria-valuetext", "26 Oct: Reading 0.00 minutes, Target 20.00 minutes"
                )
                expect(chart.locator('[data-dot="actual"]')).to_have_css(
                    "fill", resolved_chart_color(page, "--accent-error")
                )
                plot.press("ArrowRight")
                expect(plot).to_have_attribute(
                    "aria-valuetext", "27 Oct: Reading No reading, Target 20.00 minutes"
                )
                expect(chart.locator('[data-dot="actual"]')).to_have_css("display", "none")
                expect(chart.get_by_role("textbox")).to_have_attribute("data-reading", "22.5")
                plot.press("ArrowRight")
                expect(plot).to_have_attribute(
                    "aria-valuetext", "28 Oct: Reading 20.00 minutes, Target 20.00 minutes"
                )
                expect(chart.locator('[data-dot="actual"]')).to_have_css(
                    "fill", resolved_chart_color(page, "--accent-done")
                )
                inspection_started = time.monotonic()
                inspections["goal-manual-line"] = desktop_inspection(
                    page, chart, plot, keyboard=False, fraction=5 / 6,
                    output=output, name="desktop-manual-line",
                    after_click=lambda value=value: expect(value).to_be_focused(),
                )
                expect(value).to_be_focused()
                expect(value).to_have_attribute("data-reading", "22.5")
                inspection_seconds += time.monotonic() - inspection_started
            assert fixture.writes == [], fixture.writes
            inspection_started = time.monotonic()
            page.goto(url + "#/workspace/chief-of-staff", wait_until="domcontentloaded")
            overview = page.locator('[data-goal-card="daily_quota"] .card-progress')
            yesterday = overview.get_by_role("button", name=re.compile("^29 Oct,"))
            if phone:
                inspections["overview-bars"] = touch_inspection(
                    page, session, overview, yesterday, contact=False,
                )
            else:
                inspections["overview-bars"] = desktop_inspection(
                    page, overview, yesterday, keyboard=False,
                    output=output, name="desktop-overview-bars",
                )
            value = overview.get_by_role("textbox")
            expect(value).to_have_attribute("data-reading", "22")
            expect(yesterday).to_have_attribute("aria-pressed", "true")
            value.fill("9")
            value.press("Enter")
            expect(value).to_have_attribute("data-reading", "9")
            path, body = fixture.writes[-1]
            assert path == "/api/measures/daily_quota/manual-writes"
            assert (body["date"], body["operation"], body["value"]) == (YESTERDAY, "replace", 9)
            assert body["expected_definition_revision"] == 0
            assert body["expected_write_revision"] == 0
            assert len(fixture.writes) == 1, fixture.writes
            inspection_seconds += time.monotonic() - inspection_started
            assert errors == [], errors
            receipts.append({"mode": mode, "renders": renders, "errors": errors,
                             "inspection_seconds": inspection_seconds,
                             "inspections": inspections, "writes": fixture.writes})
        finally:
            context.close()
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"manual unit spacing desktop/phone: passed (+{elapsed:.2f}s)", flush=True)


def goal_presentation_fixture():
    fixture = GoalFixture()
    goal = fixture.goal("goal_wrenfixture", "Ship Wren to the iOS App Store", 6)
    goal["primary_checklist_block_id"] = "release"
    goal["page"]["blocks"] = [
        {
            "type": "checklist", "id": "release", "title": "Release readiness",
            "items": [
                {"id": "accepted", "label": "Scope accepted", "state": "done"},
                {"id": "intake", "label": "Confirm intake", "state": "open"},
                {"id": "review", "label": "Review release", "state": "open",
                 "detail": "Read the release notes before submission."},
                {"id": "blocked", "label": "Await entitlement", "state": "blocked"},
                {"id": "action", "label": "Submit build", "state": "open",
                 "action": "Submit for review"},
            ],
        },
        {
            "type": "text", "id": "intake-notes",
            "text": "Release **intake** stays beside the checklist.\nKeep this second line.\n\n"
                    "[Wren intake](/files/goals/goal_wrenfixture/artifacts/intake.md)\n\n"
                    "[App Store guidance](https://example.org/app-store) and "
                    "[Build bundle](/files/goals/goal_wrenfixture/artifacts/build.wrenbundle).",
        },
    ]
    wrap = fixture.goal("goal_wrapfixture", "Wrapped checklist strike", 7)
    wrap["primary_checklist_block_id"] = "wrap"
    wrap["page"]["blocks"] = [
        {
            "type": "checklist", "id": "wrap", "title": "Wrap check",
            "items": [
                {"id": "short", "label": "Short label", "state": "done"},
                {"id": "wrapped", "label": WRAPPED_LABEL, "state": "done"},
            ],
        },
    ]
    return fixture


def strike_mark_rects(checklist, label):
    # getClientRects() returns one box per rendered line only when the mark is an
    # inline fragment; a single box here would mean the old whole-row strike regressed.
    return checklist.evaluate(
        """(node, label) => {
            const mark = Array.from(node.querySelectorAll('.strike-mark'))
                .find(el => el.textContent.trim() === label);
            if (!mark) return null;
            return Array.from(mark.getClientRects()).map(
                rect => ({width: rect.width, height: rect.height})
            );
        }""",
        label,
    )


def centered_checklist_row(row):
    # Use actual pointer hover and rendered glyph/control rectangles. The padded
    # empty disclosure formerly shifted both centers six pixels above the surface.
    row.locator("[data-title]").hover()
    geometry = row.evaluate("""node => {
        const surface = node.querySelector('.step-inner');
        const rect = element => {
            const box = element.getBoundingClientRect();
            return {x:box.x, y:box.y, width:box.width, height:box.height,
                    centerY:box.y + box.height / 2};
        };
        return {surface:rect(surface), title:rect(node.querySelector('.title-text')),
                checkbox:rect(node.querySelector('.check-visual')),
                hovered:surface.matches(':hover'),
                background:getComputedStyle(surface).backgroundColor};
    }""")
    assert geometry["hovered"], geometry
    assert geometry["background"] != "rgba(0, 0, 0, 0)", geometry
    for part in ("title", "checkbox"):
        assert abs(geometry[part]["centerY"] - geometry["surface"]["centerY"]) <= 1, geometry
    return geometry


def disclosure_motion(title, reduced):
    # Observe the real Svelte disclosure, rather than replacing animation with a
    # stylesheet assertion. Normal motion grows height; reduced motion fades.
    samples = title.evaluate("""async node => {
        node.click();
        const samples = [];
        const start = performance.now();
        while (performance.now() - start < 340) {
            await new Promise(requestAnimationFrame);
            const panel = document.getElementById(node.getAttribute('aria-controls'));
            if (panel) samples.push({height:panel.getBoundingClientRect().height,
                full:panel.scrollHeight, opacity:Number(getComputedStyle(panel).opacity)});
        }
        return samples;
    }""")
    assert samples and samples[-1]["height"] > 0, samples
    assert samples[-1]["opacity"] == 1, samples
    assert any(0 < sample["opacity"] < 1 for sample in samples), samples
    if not reduced:
        assert any(0 < sample["height"] < samples[-1]["height"] - 1 for sample in samples), samples
    return samples


def overview_checklist_remount(page, fixture, phone, output, mode):
    # Active filtering destroys a card while its GoalsOverview parent survives.
    # Existing preview receipts do not prove this separate registry lifetime.
    identity = "goal_wrenfixture"
    page.evaluate("location.hash='#/workspace'")
    page.locator('button[data-chief-destination]').click()
    card = page.locator(f'[data-goal-card="{identity}"]')
    checklist = card.locator('[data-block="checklist"]')
    expect(checklist).to_have_attribute('data-presentation', 'normal')
    control = checklist.get_by_role('checkbox', name='Confirm intake', exact=True)
    expect(control).to_be_visible()
    fixture.hold = True
    count = len(fixture.writes)
    with page.expect_request(f'**/api/goals/{identity}'):
        (control.tap if phone else control.click)()
    # Held writes must not show pending-save text.
    expect(checklist.get_by_text('Saving checklist…')).to_have_count(0)
    assert len(fixture.held) == 1

    def active(value):
        fixture.goals[identity]['active'] = value
        fixture.version += 1
        with page.expect_response(lambda response: (
            urlsplit(response.url).path == '/api/goals'
            or parse_qs(urlsplit(response.url).query).get('key') == ['/api/goals']
        )):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange', {bubbles:true}))")
        expect(card).to_have_count(1 if value else 0)

    active(False)
    active(True)
    # The pending write survives the remount without showing pending-save text.
    expect(checklist.get_by_text('Saving checklist…')).to_have_count(0)
    expect(control).to_have_attribute('aria-checked', 'true')
    expect(checklist.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '1')
    fixture.reject_held()
    expect(checklist.get_by_role('alert')).to_contain_text('Save rejected')
    active(False)
    active(True)
    expect(checklist.get_by_role('alert')).to_contain_text('Save rejected')
    expect(control).to_have_attribute('aria-checked', 'false')
    assert len(fixture.writes) == count + 1
    retry = checklist.get_by_role('button', name='Retry save', exact=True)
    (retry.tap if phone else retry.click)()
    expect(checklist.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '2')
    expect(checklist.get_by_role('alert')).to_have_count(0)
    assert len(fixture.writes) == count + 2
    assert fixture.goals[identity]['page']['blocks'][0]['items'][1]['state'] == 'done'
    toggle = checklist.locator('[data-checklist-toggle]')
    (toggle.tap if phone else toggle.click)()
    expect(toggle).to_have_attribute('aria-expanded', 'false')
    (toggle.tap if phone else toggle.click)()
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    assert len(fixture.writes) == count + 2
    if output:
        expect(checklist.get_by_text('Saving checklist…', exact=True)).to_have_count(0)
        expect(checklist.get_by_role('button', name='2 completed', exact=True)).to_be_visible()
        card.screenshot(path=str(output / f'{mode}-overview.png'))


def authored_goal_presentation(browser, url):
    started = time.monotonic()
    artifact_root = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    output = Path(artifact_root) / "goal-presentation" if artifact_root else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    receipts = []
    for phone, reduced in ((False, False), (False, True), (True, False), (True, True)):
        mode = f"{'phone' if phone else 'desktop'}-{'reduced' if reduced else 'normal'}"
        context = browser.new_context(
            viewport={"width": 390 if phone else 1440, "height": 844 if phone else 1000},
            has_touch=phone, is_mobile=phone,
            reduced_motion="reduce" if reduced else "no-preference",
        )
        page = context.new_page()
        page.set_default_timeout(5000)
        fixture = goal_presentation_fixture()
        writes, errors = [], []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        page.on("request", lambda request, writes=writes: writes.append(
            (request.method, request.url)
        ) if request.method not in ("GET", "HEAD") else None)
        page.route("**/api/**", fixture.respond)
        page.route(
            "**/files/goals/goal_wrenfixture/artifacts/intake.md",
            lambda route: route.fulfill(
                content_type="text/markdown",
                body="# Wren intake\n\nThe release identity is Wren iOS.\n",
            ),
        )
        try:
            page.goto(url + "#/workspace/goal/goal_wrenfixture", wait_until="domcontentloaded")
            goal = page.locator('[data-goal-page="goal_wrenfixture"]')
            checklist = goal.get_by_role("region", name="Completion checklist")
            expect(checklist.locator("[data-presentation]")).to_have_attribute(
                "data-presentation", "normal"
            )
            expect(checklist.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "1")
            # Saved done rows start in the completed group, without a write or a
            # settlement delay. Existing native controls cover check/uncheck saves.
            expect(checklist.get_by_role("button", name="1 completed", exact=True)).to_be_visible()
            expect(checklist.get_by_role(
                "checkbox", name="Scope accepted", exact=True
            )).to_have_count(0)
            active = checklist.locator('[data-step="intake"]')
            expect(active.locator("[data-title]")).to_have_attribute("aria-expanded", "true")
            geometry = centered_checklist_row(active)
            if output:
                page.screenshot(path=str(output / f"{mode}.png"))
            # Multi-line completed labels must carry one strike per rendered line, not
            # one across the whole wrapped box. Read-only: runs before the no-write check.
            page.goto(url + "#/workspace/goal/goal_wrapfixture", wait_until="domcontentloaded")
            wrap_checklist = page.locator(
                '[data-goal-page="goal_wrapfixture"]'
            ).get_by_role("region", name="Completion checklist")
            expect(
                wrap_checklist.get_by_role("button", name="2 completed", exact=True)
            ).to_be_visible()
            wrap_checklist.locator("[data-group-toggle]").click()
            short_lines = strike_mark_rects(wrap_checklist, "Short label")
            assert short_lines is not None and len(short_lines) == 1, short_lines
            wrapped_lines = strike_mark_rects(wrap_checklist, WRAPPED_LABEL)
            assert wrapped_lines is not None and len(wrapped_lines) >= 2, wrapped_lines
            if output:
                wrap_checklist.screenshot(path=str(output / f"{mode}-wrap.png"))
            page.goto(url + "#/workspace/goal/goal_wrenfixture", wait_until="domcontentloaded")
            motion = disclosure_motion(
                checklist.get_by_role("button", name="Review release", exact=True),
                reduced,
            )
            expect(checklist.get_by_text(
                "Read the release notes before submission."
            )).to_be_visible()
            expect(goal.locator("[data-artifacts-band]")).to_have_count(0)

            prose_geometry = None
            if not reduced:
                text = goal.locator('[data-block="text"]')
                expect(text.locator("strong")).to_have_text("intake")
                expect(text).to_contain_text("Keep this second line.")
                expect(text.get_by_role(
                    "link", name="App Store guidance", exact=True
                )).to_have_attribute("href", "https://example.org/app-store")
                download = text.get_by_role("link", name="Download build.wrenbundle", exact=True)
                expect(download).to_have_attribute("download", "")
                expect(download).to_have_attribute(
                    "href", "/files/goals/goal_wrenfixture/artifacts/build.wrenbundle"
                )
                card = text.locator('[data-file-preview-kind="markdown"]')
                card.scroll_into_view_if_needed()
                if phone:
                    expect(card.locator(".pv-name")).to_be_visible()
                else:
                    expect(card.get_by_role(
                        "heading", name="Wren intake", exact=True
                    )).to_be_visible()
                    expect(card).to_contain_text("The release identity is Wren iOS.")
                    expect(card.locator(".file-preview-natural p")).to_have_css(
                        "white-space", "normal"
                    )
                    prose_geometry = text.locator(".markdown-block > p").first.evaluate("""p => {
                        const source = p.querySelector('strong').nextSibling;
                        const top = phrase => {
                            const range = document.createRange();
                            const offset = source.textContent.indexOf(phrase);
                            range.setStart(source, offset);
                            range.setEnd(source, offset + 1);
                            return range.getBoundingClientRect().y;
                        };
                        return {firstLine:top('checklist.'), secondLine:top('Keep this'),
                                lineHeight:parseFloat(getComputedStyle(p).lineHeight)};
                    }""")
                    assert abs(prose_geometry["secondLine"] - prose_geometry["firstLine"]
                               - prose_geometry["lineHeight"]) <= 1, prose_geometry
                link = card.locator("a.pv-name:visible")
                expect(link).to_have_text("intake.md")
                original = page.url
                link.click()
                viewer = page.locator("[data-ticket-artifact]")
                expect(viewer.get_by_role(
                    "heading", name="Wren intake", exact=True
                )).to_be_visible()
                expect(viewer).to_contain_text("The release identity is Wren iOS.")
                assert "/workspace/goal/goal_wrenfixture" in page.url
                assert page.url != original
                if output:
                    page.screenshot(path=str(output / f"{mode}-artifact.png"))
                viewer.get_by_role("button", name="Close", exact=True).click()
                expect(viewer).to_have_count(0)
                expect(page).to_have_url(original)
                link.click()
                expect(viewer).to_be_visible()
                page.go_back()
                expect(viewer).to_have_count(0)
                expect(page).to_have_url(original)

            if not phone and not reduced:
                for identity, name, child in (
                    ("blocked", "Await entitlement", ".blocked"),
                    ("action", "Submit build", "[data-action]"),
                ):
                    row = checklist.locator(f'[data-step="{identity}"]')
                    row.get_by_role("button", name=name, exact=True).click()
                    expect(row.locator(child)).to_be_visible()
                    expect(row.locator(".detail")).to_have_count(0)
                # The shared native checklist renderer also owns the rail preview.
                # Existing tests exercise its writes and dismissal lifecycle.
                page.evaluate("location.hash='#/workspace'")
                panel = open_preview(page, "goal_wrenfixture")
                expect(panel.locator("[data-block=checklist]")).to_have_attribute(
                    "data-presentation", "small"
                )
                rail_geometry = centered_checklist_row(panel.locator('[data-step="intake"]'))
                if output:
                    panel.screenshot(path=str(output / "desktop-rail.png"))
            else:
                rail_geometry = None
            assert writes == [], writes
            assert fixture.writes == [], fixture.writes
            assert errors == [], errors
            if not reduced:
                overview_checklist_remount(page, fixture, phone, output, mode)
                # Reopening a wrapped completed label must drop the strike and keep
                # the label split one rendered line per fragment, not one merged box.
                page.goto(url + "#/workspace/goal/goal_wrapfixture", wait_until="domcontentloaded")
                wrap_checklist = page.locator(
                    '[data-goal-page="goal_wrapfixture"]'
                ).get_by_role("region", name="Completion checklist")
                expect(
                    wrap_checklist.get_by_role("button", name="2 completed", exact=True)
                ).to_be_visible()
                wrap_checklist.locator("[data-group-toggle]").click()
                reopen = wrap_checklist.get_by_role("checkbox", name=WRAPPED_LABEL, exact=True)
                (reopen.tap if phone else reopen.click)()
                wrapped_row = wrap_checklist.locator('[data-step="wrapped"]')
                expect(wrapped_row).not_to_have_class(re.compile("struck"))
                reopened_lines = strike_mark_rects(wrap_checklist, WRAPPED_LABEL)
                assert reopened_lines is not None and len(reopened_lines) >= 2, reopened_lines
                page.goto(url + "#/workspace/goal/goal_wrenfixture", wait_until="domcontentloaded")
            receipts.append({"mode": mode, "geometry": geometry, "disclosure": motion,
                             "prose_geometry": prose_geometry,
                             "rail_geometry": rail_geometry, "writes": writes, "errors": errors})
        finally:
            context.close()
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"goal checklist/artifact presentation: passed (+{elapsed:.2f}s)", flush=True)


def run(browser, urls):
    started = time.monotonic()
    checklist_deadline_controls(browser, urls[0])
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
    authored_goal_presentation(browser, urls[0])
    native_goal_edits(browser, urls[0])
    gauge_visual_equivalence(browser, urls[0])


class NativeGoalEditFixture(GoalFixture):
    def __init__(self):
        self.__dict__.update(goal_presentation_fixture().__dict__)
        self.fail_next = 0
        self.reads = {}
        goal = self.goals["goal_wrenfixture"]
        goal["why"] = ""
        goal["page"]["blocks"].append(
            {
                "type": "section",
                "id": "native-section",
                "title": "Authored approach",
                "summary": "Saved summary",
                "blocks": [
                    {
                        "type": "grid",
                        "id": "native-grid",
                        "blocks": [
                            {
                                "type": "callout",
                                "id": "native-callout",
                                "tone": "neutral",
                                "title": "Saved callout",
                                "text": "Saved callout body",
                            },
                            {
                                "type": "metrics",
                                "id": "native-metrics",
                                "items": [
                                    {
                                        "label": "Authored metric",
                                        "value": "Seven releases",
                                        "change": "Plus one",
                                        "hint": "Saved metric hint",
                                    }
                                ],
                            },
                            {
                                "type": "properties",
                                "id": "native-properties",
                                "items": [{"label": "Owner", "value": "Wren team"}],
                            },
                            {
                                "type": "status",
                                "id": "native-status",
                                "label": "Draft status",
                                "tone": "neutral",
                            },
                            {
                                "type": "rows",
                                "id": "native-rows",
                                "items": [
                                    {
                                        "title": "Saved row",
                                        "detail": "Saved detail",
                                        "status": "Saved status",
                                        "meta": "Saved meta",
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "type": "segmented",
                        "id": "native-views",
                        "views": [
                            {
                                "id": "first",
                                "label": "Readiness",
                                "blocks": [
                                    {
                                        "type": "checklist",
                                        "id": "native-checklist",
                                        "title": "Nested readiness",
                                        "doneTitle": "Nested ready",
                                        "items": [
                                            {
                                                "id": "native-step",
                                                "label": "Native item",
                                                "state": "open",
                                                "detail": "Native detail",
                                                "action": "Complete native item",
                                            }
                                        ],
                                    }
                                ],
                            },
                            {
                                "id": "second",
                                "label": "Evidence",
                                "blocks": [
                                    {
                                        "type": "table",
                                        "id": "native-table",
                                        "caption": "Saved table",
                                        "columns": ["Name", "Score"],
                                        "rows": [["Zulu", "2"], ["Alpha", "1"]],
                                        "cell_tones": [["attention", None], [None, "good"]],
                                    },
                                    {
                                        "type": "table",
                                        "id": "native-empty",
                                        "columns": ["Empty"],
                                        "rows": [],
                                        "empty_message": "Nothing saved",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }
        )
        self.goals["daily_quota"]["primary_chart"]["unit"] = "minutes"

    @staticmethod
    def blocks(blocks):
        for block in blocks:
            yield block
            yield from NativeGoalEditFixture.blocks(block.get("blocks", []))
            for view in block.get("views", []):
                yield from NativeGoalEditFixture.blocks(view.get("blocks", []))

    def accept(self, route, path, body):
        if path.endswith("/manual-writes"):
            return super().accept(route, path, body)
        if self.fail_next:
            self.fail_next -= 1
            route.fulfill(
                status=503, json={"error": {"code": "fixture", "message": "Save rejected"}}
            )
            return
        goal = self.goals[path.split("/")[3]]
        if body["expected_revision"] != goal["revision"]:
            route.fulfill(
                status=409,
                json={
                    "error": {
                        "code": "conflict",
                        "message": "Goal changed. Discard the draft before another edit.",
                    }
                },
            )
            return
        goal.update(
            copy.deepcopy({key: value for key, value in body.items() if key != "expected_revision"})
        )
        goal["revision"] += 1
        self.version += 1
        selected = next(
            (
                block
                for block in self.blocks(goal["page"]["blocks"])
                if block["id"] == goal["primary_checklist_block_id"]
            ),
            None,
        )
        if selected:
            goal["completed"] = bool(selected["items"]) and all(
                item["state"] == "done" for item in selected["items"]
            )
        goal["status"] = "good" if goal["completed"] else "neutral"
        route.fulfill(json=goal)

    def respond(self, route):
        parts = urlsplit(route.request.url)
        query = parse_qs(parts.query)
        key = query.get("key", [parts.path])[0]
        self.reads[key] = self.reads.get(key, 0) + 1
        if route.request.method == "GET" and key.startswith("/api/projects"):
            result = {
                "projects": [
                    {"id": "project", "name": "Field guide"},
                    {"id": "project-two", "name": "Native editing"},
                ]
            }
            route.fulfill(
                json={
                    "key": key,
                    "epoch": "goal-fixture",
                    "revision": str(self.version),
                    "body": result,
                }
                if parts.path == "/api/resource-body"
                else result
            )
            return
        return super().respond(route)


def native_goal_edits(browser, url):
    started = time.monotonic()
    artifact_root = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    output = Path(artifact_root) / "native-goal-edits" if artifact_root else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    receipts = []
    # All field variants run once on desktop. Phone adds touch/keyboard separation
    # and overflow/spacing evidence, rather than duplicating the whole matrix.
    def run_mode(phone):
        mode = "phone" if phone else "desktop"
        context = browser.new_context(
            viewport={"width": 390 if phone else 1440, "height": 844 if phone else 1000},
            has_touch=phone,
            is_mobile=phone,
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.set_default_timeout(5000)
        fixture = NativeGoalEditFixture()
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        page.expose_function("__nativeWriteCount", lambda: len(fixture.writes))
        page.expose_function("__nativeReadCount", lambda key: fixture.reads.get(key, 0))
        page.expose_function(
            "__nativeAccepted", lambda identity: copy.deepcopy(fixture.goals[identity])
        )
        page.route("**/api/**", fixture.respond)
        page.route(
            "**/files/goals/goal_wrenfixture/artifacts/intake.md",
            lambda route: route.fulfill(
                content_type="text/markdown", body="# Wren intake\n\nWren iOS release.\n"
            ),
        )
        identity = "goal_wrenfixture"

        def workspace(goal_id=identity):
            page.evaluate("hash => {location.hash=hash;}", f"#/workspace/goal/{goal_id}")
            result = page.locator(f'[data-goal-page="{goal_id}"]')
            expect(result.get_by_role("textbox", name="Goal title", exact=True)).to_be_visible()
            return result

        def wait_fixture(predicate, message):
            # This Playwright build returns a false async predicate immediately.
            # Poll the Python fixture while browser events and routes progress.
            deadline = time.monotonic() + 5
            while not predicate():
                assert time.monotonic() < deadline, (
                    message,
                    page.evaluate(
                        "({active:document.activeElement?.tagName,"
                        "label:document.activeElement?.getAttribute('aria-label'),"
                        "errors:[...document.querySelectorAll('.error-line')].map(n=>n.textContent)})"
                    ),
                )
                page.wait_for_timeout(10)

        def wait_writes(count):
            wait_fixture(
                lambda: len(fixture.writes) >= count,
                f"Expected {count} writes, got {len(fixture.writes)}",
            )

        def field_write(field, value, action, goal_id=identity):
            before = len(fixture.writes)
            revision = fixture.goals[goal_id]["revision"]
            configuration = {
                key: copy.deepcopy(fixture.goals[goal_id][key])
                for key in ("primary_chart", "primary_checklist_block_id")
            }
            with page.expect_response(
                lambda response: (
                    urlsplit(response.url).path == f"/api/goals/{goal_id}"
                    and response.request.method == "PATCH"
                    and response.request.post_data_json == {
                        field: value, "expected_revision": revision
                    }
                )
            ) as response:
                action()
            assert response.value.status == 200, (
                field,
                fixture.writes[-1],
                fixture.goals[goal_id]["revision"],
                {
                    "mode": mode,
                    "focus": page.evaluate(
                        "({active:document.activeElement?.tagName,label:document.activeElement?.getAttribute('aria-label')})"
                    ),
                },
                response.value.text(),
            )
            assert fixture.goals[goal_id]["revision"] > revision
            expect(
                page.locator(
                    f'[data-goal-page="{goal_id}"] .ed[contenteditable="false"], '
                    f'[data-goal-page="{goal_id}"] .goal-heading input:disabled, '
                    f'[data-goal-page="{goal_id}"] .goal-heading select:disabled'
                )
            ).to_have_count(0)
            assert len(fixture.writes) == before + 1, fixture.writes[before:]
            path, body = fixture.writes[-1]
            assert path == f"/api/goals/{goal_id}", (path, body)
            assert body == {field: value, "expected_revision": revision}, body
            for key, accepted in configuration.items():
                assert fixture.goals[goal_id][key] == accepted, (field, key)

        def text_action(locator, value, multiline=False):
            locator.fill(value)
            locator.press("Control+Enter" if multiline else "Enter")

        def block(identity):
            return next(
                item
                for item in fixture.blocks(fixture.goals["goal_wrenfixture"]["page"]["blocks"])
                if item["id"] == identity
            )

        def page_text(locator, value, block_id, field_path, multiline=False):
            expected = copy.deepcopy(fixture.goals[identity]["page"])
            target = next(
                item for item in fixture.blocks(expected["blocks"]) if item["id"] == block_id
            )
            for key in field_path[:-1]:
                target = target[key]
            target[field_path[-1]] = value
            field_write("page", expected, lambda: text_action(locator, value, multiline))

        def refresh(goal_id=identity):
            key = f"/api/goals/{goal_id}"
            before = fixture.reads.get(key, 0)
            with page.expect_response(
                lambda response: (
                    urlsplit(response.url).path == key
                    or parse_qs(urlsplit(response.url).query).get("key", [None])[0] == key
                )
            ):
                page.evaluate(
                    "document.dispatchEvent(new Event('visibilitychange', {bubbles:true}))"
                )
            assert fixture.reads.get(key, 0) > before

        try:
            page.goto(url + f"#/workspace/goal/{identity}", wait_until="domcontentloaded")
            goal = workspace()
            title = goal.get_by_role("textbox", name="Goal title", exact=True)
            why = goal.get_by_role("textbox", name="Why this goal matters", exact=True)
            expect(why).to_be_visible()
            for label in ("Completion display", "Success threshold", "Threshold direction"):
                expect(goal.get_by_label(label, exact=True)).to_have_count(0)
            for label in ("Project", "By date", "State"):
                expect(goal.get_by_label(label, exact=True)).to_be_visible()
            assert why.bounding_box()["height"] > 0
            field_write(
                "title", "Native Wren release", lambda: text_action(title, "Native Wren release")
            )
            if not phone:
                field_write(
                    "why",
                    "A direct reason",
                    lambda: (why.fill("A direct reason"), why.press("Tab")),
                )
                field_write(
                    "why",
                    "A direct reason\nWith a second line",
                    lambda: text_action(why, "A direct reason\nWith a second line", True),
                )
                # Native Enter creates div/br nodes rather than literal text newlines.
                field_write(
                    "why",
                    "First keyboard line\nSecond keyboard line",
                    lambda: (
                        why.fill("First keyboard line"),
                        why.press("End"),
                        why.press("Enter"),
                        why.type("Second keyboard line"),
                        why.press("Control+Enter"),
                    ),
                )
                field_write(
                    "why",
                    "First paragraph\n\nSecond paragraph",
                    lambda: text_action(why, "First paragraph\n\nSecond paragraph", True),
                )
                field_write(
                    "why",
                    "Trailing line\n",
                    lambda: (
                        why.fill("Trailing line"),
                        why.press("End"),
                        why.press("Enter"),
                        why.press("Control+Enter"),
                    ),
                )
                field_write("why", "", lambda: (why.fill(""), why.press("Control+Enter")))
                expect(why).to_have_attribute("data-empty", "true")
                assert why.bounding_box()["height"] > 0
                before = len(fixture.writes)
                title.fill("Discard this title")
                title.press("Escape")
                expect(title).to_have_text("Native Wren release")
                assert len(fixture.writes) == before
                title.fill("")
                title.press("Enter")
                expect(goal.locator(".error-line")).to_contain_text("Enter a goal title")
                assert len(fixture.writes) == before
                title.press("Escape")
                expect(title).to_have_text("Native Wren release")
                # Plain metadata must not become Markdown markup.
                field_write(
                    "why",
                    "**literal metadata**",
                    lambda: text_action(why, "**literal metadata**", True),
                )
                expect(why.locator("strong")).to_have_count(0)
                deadline = goal.get_by_label("By date", exact=True)
                field_write("deadline", None, lambda: (deadline.fill(""), deadline.press("Enter")))
                expect(deadline).to_have_value("")
                expect(goal.locator(".deadline-empty")).to_have_text("No deadline")
                deadline.click()
                expect(deadline).to_be_focused()
                deadline.press("Escape")
                field_write(
                    "deadline",
                    "2027-02-03",
                    lambda: (
                        deadline.focus(),
                        deadline.fill("2027-02-03"),
                        goal.get_by_label("Project", exact=True).focus(),
                    ),
                )
                field_write(
                    "project_id",
                    "project-two",
                    lambda: goal.get_by_label("Project", exact=True).select_option("project-two"),
                )
                field_write(
                    "active",
                    False,
                    lambda: goal.get_by_label("State", exact=True).select_option("false"),
                )
                field_write(
                    "active",
                    True,
                    lambda: goal.get_by_label("State", exact=True).select_option("true"),
                )
                # Configuration comes from an agent/source update. Owner metadata
                # saves must preserve nested, absent, and top-level checklist bindings.
                for selected in ("native-checklist", None, "release"):
                    fixture.goals[identity]["primary_checklist_block_id"] = selected
                    fixture.goals[identity]["revision"] += 1
                    fixture.version += 1
                    refresh()
                    reason = f"Keep configured completion: {selected}"
                    field_write("why", reason, lambda reason=reason: text_action(why, reason, True))

                # A pristine focus cycle must not retain an obsolete edit revision.
                title.focus()
                page.locator("[data-chief-destination]").first.focus()
                fixture.goals[identity]["why"] = "Remote purpose"
                fixture.goals[identity]["revision"] += 1
                fixture.version += 1
                refresh()
                expect(why).to_have_text("Remote purpose")
                field_write(
                    "title",
                    "Fresh revision title",
                    lambda: text_action(title, "Fresh revision title"),
                )

                if not phone:
                    # Preserve a focused draft through a background accepted refresh.
                    title.fill("Local draft after refresh")
                    original_revision = fixture.goals[identity]["revision"]
                    fixture.goals[identity]["title"] = "Remote accepted title"
                    fixture.goals[identity]["why"] = "Remote accepted purpose"
                    fixture.goals[identity]["revision"] += 1
                    fixture.version += 1
                    refresh()
                    expect(why).to_have_text("Remote accepted purpose")
                    expect(title).to_have_text("Local draft after refresh")
                    count = len(fixture.writes)
                    title.press("Enter")
                    expect(goal.locator(".error-line").first).to_contain_text("Goal changed")
                    assert fixture.writes[-1][1] == {
                        "title": "Local draft after refresh",
                        "expected_revision": original_revision,
                    }
                    assert len(fixture.writes) == count + 1
                    expect(title).to_have_text("Local draft after refresh")
                    title.press("Escape")
                    expect(title).to_have_text("Remote accepted title")

                    # Failed structured drafts retain their selected value. Explicit
                    # retry uses the same payload; discard restores accepted data.
                    state = goal.get_by_label("State", exact=True)
                    fixture.fail_next = 1
                    count = len(fixture.writes)
                    state.select_option("false")
                    expect(goal.locator(".error-line").first).to_contain_text("Save rejected")
                    failed = copy.deepcopy(fixture.writes[-1])
                    expect(state).to_have_value("false")
                    assert fixture.goals[identity]["active"] is True
                    assert len(fixture.writes) == count + 1
                    goal.get_by_role("button", name="Retry save", exact=True).click()
                    wait_writes(count + 2)
                    expect(goal.locator(".error-line")).to_have_count(0)
                    assert fixture.writes[-1] == failed
                    assert fixture.goals[identity]["active"] is False
                    field_write("active", True, lambda: state.select_option("true"))
                    fixture.fail_next = 1
                    state.select_option("false")
                    expect(goal.locator(".error-line").first).to_contain_text("Save rejected")
                    count = len(fixture.writes)
                    goal.get_by_role("button", name="Discard edit", exact=True).click()
                    expect(state).to_have_value("true")
                    assert len(fixture.writes) == count

                    # The same binary metadata snapshot also rejects stale selects.
                    state.focus()
                    revision = fixture.goals[identity]["revision"]
                    fixture.goals[identity]["revision"] += 1
                    fixture.version += 1
                    state.select_option("false")
                    expect(goal.locator(".error-line").first).to_contain_text("Goal changed")
                    assert fixture.writes[-1][1] == {"active": False, "expected_revision": revision}
                    expect(state).to_have_value("false")
                    assert fixture.goals[identity]["active"] is True
                    goal.get_by_role("button", name="Discard edit", exact=True).click()
                    expect(state).to_have_value("true")

                    # A held receipt must retain the old goal identity after routing
                    # reuses the workspace for another goal.
                    fixture.hold = True
                    count = len(fixture.writes)
                    title = goal.get_by_role("textbox", name="Goal title", exact=True)
                    title.fill("Accepted after goal switch")
                    title.press("Enter")
                    wait_writes(count + 1)
                    assert len(fixture.held) == 1
                    quantitative = workspace("daily_quota")
                    other_title = fixture.goals["daily_quota"]["title"]
                    route, path, body = fixture.held.pop(0)
                    fixture.hold = False
                    fixture.accept(route, path, body)
                    expect(
                        quantitative.get_by_role("textbox", name="Goal title", exact=True)
                    ).to_have_text(other_title)
                    assert fixture.goals[identity]["title"] == "Accepted after goal switch"
                    assert fixture.goals["daily_quota"]["title"] == other_title
                    assert path == f"/api/goals/{identity}"
                    for label in ("Success threshold", "Threshold direction"):
                        expect(quantitative.get_by_label(label, exact=True)).to_have_count(0)
                    # Agent-owned zero/fraction targets and direction survive an
                    # ordinary metadata patch and an authoritative reload.
                    for number, direction in ((0, "atleast"), (0.125, "atmost")):
                        chart = copy.deepcopy(fixture.goals["daily_quota"]["primary_chart"])
                        chart["target"] = number
                        chart["direction"] = direction
                        fixture.goals["daily_quota"]["primary_chart"] = chart
                        fixture.goals["daily_quota"]["revision"] += 1
                        fixture.version += 1
                        refresh("daily_quota")
                        reason = f"Keep configured target: {number} {direction}"
                        field_write(
                            "why", reason,
                            lambda reason=reason: text_action(quantitative.get_by_role(
                                "textbox", name="Why this goal matters", exact=True
                            ), reason, True),
                            "daily_quota",
                        )
                    page.reload(wait_until="domcontentloaded")
                    quantitative = workspace("daily_quota")
                    expect(quantitative.get_by_role(
                        "textbox", name="Why this goal matters", exact=True
                    )).to_have_text(reason)
                    assert fixture.goals["daily_quota"]["primary_chart"] == chart
                    goal = workspace()

            else:
                field_write(
                    "why",
                    "A direct reason",
                    lambda: (why.tap(), why.fill("A direct reason"), why.press("Tab")),
                )
                deadline = goal.get_by_label("By date", exact=True)
                field_write("deadline", None, lambda: (deadline.fill(""), deadline.press("Enter")))
                expect(goal.locator(".deadline-empty")).to_have_text("No deadline")
                deadline.tap()
                expect(deadline).to_be_focused()
                deadline.press("Escape")
                field_write("deadline", "2026-12-31", lambda: (
                    deadline.fill("2026-12-31"), deadline.press("Enter"),
                ))

            # Every renderer category receives one persisted authored edit. Full
            # page equality proves that neighboring fields and stable IDs survive.
            if not phone:
                edits = [
                    ("Section title", "Edited approach", "native-section", ["title"], False),
                    (
                        "Section summary",
                        "Edited summary\nWith its second line",
                        "native-section",
                        ["summary"],
                        True,
                    ),
                    ("Callout title", "Edited callout", "native-callout", ["title"], False),
                    (
                        "Callout text",
                        "Edited callout body\n\nWith a second paragraph",
                        "native-callout",
                        ["text"],
                        True,
                    ),
                    (
                        "Metric value",
                        "Eight authored releases",
                        "native-metrics",
                        ["items", 0, "value"],
                        False,
                    ),
                    (
                        "Property value",
                        "Wren release team",
                        "native-properties",
                        ["items", 0, "value"],
                        False,
                    ),
                    ("Status label", "Ready status", "native-status", ["label"], False),
                    ("Row meta", "Edited row meta", "native-rows", ["items", 0, "meta"], False),
                ]
                for label, value, block_id, path, multiline in edits:
                    page_text(
                        goal.get_by_role("textbox", name=label, exact=True),
                        value,
                        block_id,
                        path,
                        multiline,
                    )
            nested = goal.locator('[data-block="segmented"]')
            count = len(fixture.writes)
            label = nested.get_by_role("textbox", name="View label: Evidence", exact=True)
            if phone:
                label.tap()
                label.press("ArrowLeft")
                expect(
                    nested.get_by_role("radio", name="Show Readiness view", exact=True)
                ).to_have_attribute("aria-checked", "true")
                label.press("Escape")
            else:
                page_text(label, "Proof", "native-views", ["views", 1, "label"])
            selected_label = "Evidence" if phone else "Proof"
            radio = nested.get_by_role("radio", name=f"Show {selected_label} view", exact=True)
            if phone:
                radio.tap()
            else:
                native_radio = nested.get_by_role("radio", name="Show Readiness view", exact=True)
                native_radio.focus()
                native_radio.press("ArrowRight")
            expect(radio).to_have_attribute("aria-checked", "true")
            count = len(fixture.writes)
            table = nested.locator('[data-block="table"]').first
            sort = table.get_by_role("button", name="Sort by Name", exact=True)
            (sort.tap if phone else sort.click)()
            expect(table.locator("tbody tr").first).to_contain_text("Alpha")
            assert len(fixture.writes) == count
            cell = table.get_by_role("textbox", name="Score, row 2", exact=True)
            page_text(cell, "1.5", "native-table", ["rows", 1, 1])
            assert block("native-table")["rows"] == [["Zulu", "2"], ["Alpha", "1.5"]]
            if not phone:
                page_text(
                    table.get_by_role("textbox", name="Table caption", exact=True),
                    "Edited table",
                    "native-table",
                    ["caption"],
                )
                page_text(
                    table.get_by_role("textbox", name="Table column 2", exact=True),
                    "Reading",
                    "native-table",
                    ["columns", 1],
                )
                empty = nested.get_by_role("textbox", name="Table empty message", exact=True)
                page_text(empty, "No evidence yet", "native-empty", ["empty_message"])

            nested.get_by_role("radio", name="Show Readiness view", exact=True).click()
            checklist = nested.locator('[data-block="checklist"]')
            if phone:
                expect(checklist).to_have_attribute("data-presentation", "smallest")
                if output:
                    checklist.screenshot(path=str(output / "phone-smallest.png"))
                checklist.locator('[data-checklist-toggle]').tap()
            expect(checklist).to_have_attribute("data-presentation", "small")
            if phone and output:
                checklist.screenshot(path=str(output / "phone-small-expanded.png"))
            # Item dates add native inputs. Authored checklist text stays read-only.
            expect(checklist.locator(
                '[contenteditable]:not([contenteditable="false"]), '
                '[role="textbox"]:not(input[type="date"]), input:not([type="date"]), textarea'
            )).to_have_count(0)
            expect(checklist.get_by_role(
                "heading", name="Nested readiness", exact=True
            )).to_be_visible()
            row = checklist.locator('[data-step="native-step"]')
            detail_button = row.get_by_role("button", name="Native item", exact=True)
            if detail_button.get_attribute("aria-expanded") != "true":
                (detail_button.tap if phone else detail_button.click)()
            expect(row.locator('.detail')).to_have_text("Native detail")
            assert block("native-checklist")["items"][0]["state"] == "open"
            count = len(fixture.writes)
            action = row.get_by_role("button", name="Complete native item", exact=True)
            (action.tap if phone else action.click)()
            wait_writes(count + 1)
            expect(checklist.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "1")
            assert block("native-checklist")["items"][0]["state"] == "done"
            assert len(fixture.writes) == count + 1
            expect(checklist.get_by_role(
                "heading", name="Nested ready", exact=True
            )).to_be_visible()
            # Item dates add native inputs. Authored checklist text stays read-only.
            expect(checklist.locator(
                '[contenteditable]:not([contenteditable="false"]), '
                '[role="textbox"]:not(input[type="date"]), input:not([type="date"]), textarea'
            )).to_have_count(0)
            assert block("native-checklist")["title"] == "Nested readiness"
            assert block("native-checklist")["doneTitle"] == "Nested ready"
            if output:
                expect(checklist.get_by_text("Saving checklist…", exact=True)).to_have_count(0)
                expect(checklist.get_by_role(
                    "button", name="1 completed", exact=True
                )).to_be_visible()
                checklist.screenshot(path=str(output / f"{mode}-complete.png"))

            # Artifact cards stay outside the editing action. This is one quick
            # check of that collision, not a repeat of artifact presentation tests.
            text = goal.locator('[data-block="text"]').first
            markdown = text.get_by_role("textbox", name="Text", exact=True)
            count = len(fixture.writes)
            link = text.locator('[data-file-preview-kind="markdown"] a.pv-name:visible')
            (link.tap if phone else link.click)()
            viewer = page.locator("[data-ticket-artifact]")
            expect(viewer.get_by_role("heading", name="Wren intake", exact=True)).to_be_visible()
            assert len(fixture.writes) == count
            viewer.get_by_role("button", name="Close", exact=True).click()
            expect(viewer).to_have_count(0)
            if not phone:
                # Alter a prose text node only. The managed Markdown serializer
                # must retain its artifact source token and sanitized link.
                before = fixture.goals[identity]["revision"]
                markdown.focus()
                markdown.locator("strong").evaluate(
                    "node => {node.textContent='release intake'; "
                    "node.dispatchEvent(new InputEvent('input',"
                    "{bubbles:true,inputType:'insertText'}));}"
                )
                markdown.press("Control+Enter")
                wait_fixture(
                    lambda: fixture.goals[identity]["revision"] > before,
                    "No accepted revision after checklist edit",
                )
                stored = block("intake-notes")["text"]
                assert "**release intake**" in stored, stored
                assert (
                    "[Wren intake](/files/goals/goal_wrenfixture/artifacts/intake.md)" in stored
                ), stored
                assert "https://example.org/app-store" in stored, stored
                expect(text.locator('[data-file-preview-kind="markdown"]')).to_be_visible()

            # Capture only real renders from this scenario. The receipt measures
            # Chief -> goal container and Chief -> first goal, on both viewports.
            page.locator(".board-workspace-right").evaluate("node => node.scrollTop=0")
            heading_geometry = goal.locator(".goal-heading").evaluate("""header => {
                const why=header.querySelector('.why'), editor=why.querySelector('.ed');
                const w=why.getBoundingClientRect(), e=editor.getBoundingClientRect();
                const context=header.querySelector('.goal-context');
                return {wrapperHeight:w.height,editorHeight:e.height,
                    alignment:getComputedStyle(context).alignItems,
                    controls:[...context.querySelectorAll('input,select')].map(control=>{
                        const c=control.getBoundingClientRect();
                        const sizer=control.parentElement.querySelector('.control-size');
                        const size=sizer.getBoundingClientRect();
                        return {name:control.getAttribute('aria-label'),width:c.width,
                            measuredWidth:size.width,top:c.top,bottom:c.bottom,
                            appearance:getComputedStyle(control).appearance};
                    }), viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth};
            }""")
            print(f"{mode} native header geometry: {heading_geometry}", flush=True)
            assert abs(heading_geometry["wrapperHeight"] - heading_geometry["editorHeight"]) <= 1, (
                mode,
                heading_geometry,
            )
            assert heading_geometry["alignment"] == "baseline", heading_geometry
            assert [control["name"] for control in heading_geometry["controls"]] == [
                "Project", "By date", "State",
            ], heading_geometry
            for control in heading_geometry["controls"]:
                assert abs(control["width"] - control["measuredWidth"]) <= 1, control
                assert control["appearance"] == "none", control
            assert heading_geometry["scrollWidth"] <= heading_geometry["viewport"], heading_geometry
            if output:
                goal.locator(".goal-heading").screenshot(path=str(output / f"{mode}-metadata.png"))
                page.screenshot(path=str(output / f"{mode}.png"), full_page=True)
            if phone:
                page.set_viewport_size({"width": 320, "height": 844})
                expect(goal.get_by_label("Project", exact=True)).to_be_visible()
                expect(goal.get_by_label("By date", exact=True)).to_be_visible()
                expect(goal.get_by_label("State", exact=True)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if output:
                    goal.locator(".goal-heading").screenshot(
                        path=str(output / "phone-320-metadata.png")
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate("location.hash='#/workspace'")
            rail = page.locator(".goals-rail")
            expect(rail).to_be_visible()
            geometry = rail.evaluate("""rail => {
                const chief = rail.closest('.chief').querySelector('[data-chief-destination]');
                const c = chief.getBoundingClientRect(), r = rail.getBoundingClientRect();
                const firstGoal = rail.querySelector('a[data-goal-id] .row-heading');
                const first = firstGoal.getBoundingClientRect();
                return {containerGap:r.top-c.bottom, firstGoalInset:first.top-c.bottom,
                    railMargin:getComputedStyle(rail).marginTop,
                    viewport:innerWidth, scrollWidth:document.documentElement.scrollWidth};
            }""")
            assert abs(geometry["containerGap"] - 8) <= 1, geometry
            assert abs(geometry["firstGoalInset"] - 16) <= 1, geometry
            assert geometry["railMargin"] == "8px", geometry
            assert geometry["scrollWidth"] <= geometry["viewport"], geometry
            if output:
                page.screenshot(path=str(output / f"{mode}-rail.png"))
            assert errors == [], errors
            receipts.append(
                {
                    "mode": mode,
                    "writes": len(fixture.writes),
                    "geometry": geometry,
                    "heading_geometry": heading_geometry,
                    "errors": errors,
                }
            )
        finally:
            context.close()
    for phone in (False, True):
        run_mode(phone)
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"native goal edits metadata/page desktop/phone: passed (+{elapsed:.2f}s)", flush=True)


class ChecklistDeadlineFixture(NativeGoalEditFixture):
    """Date writes reuse the native goal fixture and its controlled HTTP boundary."""

    def __init__(self):
        GoalFixture.__init__(self)
        self.fail_next = 0
        self.reads = {}
        self.calendar = TODAY
        goal = self.goals["checklist"]
        goal["deadline"] = TODAY
        goal["page"]["blocks"] = [
            {
                "type": "checklist",
                "id": "delivery",
                "title": "Delivery",
                "doneTitle": "Delivered",
                "items": [
                    {
                        "id": "draft",
                        "label": "Draft accepted",
                        "state": "done",
                        "deadline": YESTERDAY,
                    },
                    {"id": "publish", "label": "Publish guide", "state": "open", "deadline": TODAY},
                    {
                        "id": "review",
                        "label": "Review guide",
                        "state": "blocked",
                        "deadline": YESTERDAY,
                    },
                    {
                        "id": "future",
                        "label": "Follow up",
                        "state": "open",
                        "deadline": "2026-11-02",
                    },
                    {"id": "undated", "label": "Collect notes", "state": "open"},
                ],
            },
            {
                "type": "checklist",
                "id": "supporting",
                "title": "Supporting steps",
                "items": [
                    {"id": "extra", "label": "Check links", "state": "open", "deadline": TODAY},
                ],
            },
        ]

    def advance_day(self):
        self.calendar = "2026-10-31"
        self.version += 1
        for goal in self.goals.values():
            goal["status_date"] = self.calendar


def checklist_deadline_controls(browser, url):
    """Prove native draft lifetimes and server-day refresh in the existing App host."""
    started = time.monotonic()
    artifact_root = os.environ.get("PANELS_SIDEBAR_ARTIFACT_DIR")
    output = Path(artifact_root) / "checklist-deadlines" if artifact_root else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    receipts = []

    def run_mode(phone):
        mode = "phone" if phone else "desktop"
        context = browser.new_context(
            viewport={"width": 390 if phone else 1440, "height": 844 if phone else 1000},
            has_touch=phone,
            is_mobile=phone,
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.set_default_timeout(5000)
        fixture = ChecklistDeadlineFixture()
        errors = []
        page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
        page.route("**/api/**", fixture.respond)

        def wait_fixture(predicate, message):
            deadline = time.monotonic() + 5
            while not predicate():
                assert time.monotonic() < deadline, (mode, message, fixture.writes)
                page.wait_for_timeout(10)

        def accepted_item(item_id="publish", block_id="delivery"):
            block = next(
                block
                for block in fixture.goals["checklist"]["page"]["blocks"]
                if block["id"] == block_id
            )
            return next(item for item in block["items"] if item["id"] == item_id)

        def date_control(host, item_id="publish"):
            return host.locator(f'[data-item-deadline="{item_id}"]')

        def save_date(host, value, *, blur=False, item_id="publish"):
            control = date_control(host, item_id)
            field = control.locator('input[type="date"]')
            before = len(fixture.writes)
            field.fill(value)
            if blur:
                page.locator('[data-goal-page="checklist"] input[aria-label="By date"]').focus()
            else:
                field.press("Enter")
            wait_fixture(lambda: len(fixture.writes) == before + 1, "One explicit date write")
            expect(field).to_be_enabled()
            wait_fixture(
                lambda: accepted_item(item_id).get("deadline") == (value or None),
                "Saved item deadline",
            )
            expect(control.locator('[role="alert"]')).to_have_count(0)
            assert not fixture.goals["checklist"]["completed"]
            assert fixture.goals["checklist"]["primary_checklist_block_id"] == "delivery"
            return control

        def refresh():
            page.evaluate("document.dispatchEvent(new Event('visibilitychange', {bubbles:true}))")

        try:
            page.goto(url + "#/workspace/goal/checklist", wait_until="domcontentloaded")
            goal = page.locator('[data-goal-page="checklist"]')
            primary = goal.locator('[aria-label="Completion checklist"] [data-block="checklist"]')
            publish = date_control(primary)
            expect(publish).to_have_attribute("data-tone", "attention")
            expect(date_control(primary, "review")).to_have_attribute("data-tone", "bad")
            expect(date_control(primary, "future")).to_have_attribute("data-tone", "neutral")
            expect(date_control(primary, "undated")).to_have_attribute("data-tone", "neutral")
            primary.get_by_role("button", name="1 completed", exact=True).click()
            expect(date_control(primary, "draft")).to_have_attribute("data-tone", "good")
            expect(goal.locator(".goal-heading [data-deadline-tone]")).to_have_attribute(
                "data-deadline-tone", "attention"
            )
            assert primary.get_by_text("Add deadline", exact=True).count() == 0
            assert (
                "Due today" not in publish.locator(".date-face").inner_text()
            )  # Status stays nonvisual.

            # Native entry, intentional clear, and Escape never edit authored labels.
            original = copy.deepcopy(fixture.goals["checklist"]["page"])
            save_date(primary, "2026-11-03")
            save_date(primary, "2026-10-29", blur=True)
            expect(publish).to_have_attribute("data-tone", "bad")
            before = len(fixture.writes)
            field = publish.locator('input[type="date"]')
            field.fill("2026-11-10")
            field.press("Escape")
            expect(field).to_have_value("2026-10-29")
            assert len(fixture.writes) == before
            field.focus()
            clear = publish.get_by_role(
                "button", name="Clear deadline for Publish guide", exact=True
            )
            clear.tap() if phone else clear.click()
            wait_fixture(lambda: len(fixture.writes) == before + 1, "Clear submits once")
            wait_fixture(lambda: accepted_item().get("deadline") is None, "Clear persists null")
            expect(publish).to_have_attribute("data-tone", "neutral")
            save_date(primary, TODAY)
            for old, saved in zip(
                original["blocks"][0]["items"],
                fixture.goals["checklist"]["page"]["blocks"][0]["items"],
                strict=True,
            ):
                assert {key: value for key, value in old.items() if key != "deadline"} == {
                    key: value for key, value in saved.items() if key != "deadline"
                }

            # A native partial segment is not an intentional empty date.
            before = len(fixture.writes)
            field.focus()
            field.press("ArrowRight")
            field.press("Backspace")
            assert field.evaluate("node => node.validity.badInput"), mode
            page.locator('[data-goal-page="checklist"] input[aria-label="By date"]').focus()
            expect(publish.get_by_role("alert")).to_contain_text("Enter a complete date")
            assert len(fixture.writes) == before
            page.evaluate("location.hash='#/workspace'")
            expect(goal).to_have_count(0)
            page.evaluate("location.hash='#/workspace/goal/checklist'")
            expect(primary).to_be_visible()
            # Workspace remount owns a new session manager. Preview dismissal below
            # proves retained invalid drafts within the long-lived rail owner.
            assert accepted_item()["deadline"] == TODAY

            # Complete and reopen one dated item. Only accepted completion controls green.
            row = primary.locator('[data-step="publish"]')
            row.get_by_role("checkbox").click()
            wait_fixture(lambda: accepted_item()["state"] == "done", "Accepted item completion")
            if (
                primary.get_by_role("button", name="2 completed", exact=True).get_attribute(
                    "aria-expanded"
                )
                != "true"
            ):
                primary.get_by_role("button", name="2 completed", exact=True).click()
            expect(date_control(primary)).to_have_count(1)
            expect(date_control(primary)).to_have_attribute("data-tone", "good")
            done_row = date_control(primary).locator("xpath=ancestor::li")
            done_row.get_by_role("checkbox").click()
            wait_fixture(lambda: accepted_item()["state"] == "open", "Reopened item")
            expect(date_control(primary)).to_have_count(1)
            expect(date_control(primary)).to_have_attribute("data-tone", "attention")

            # The compact supplementary disclosure retains the full date interaction.
            supporting = goal.locator('[data-block="checklist"][data-supplementary]')
            if phone:
                expect(supporting).to_have_attribute("data-presentation", "smallest")
                supporting.locator("[data-checklist-toggle]").click()
            expect(date_control(supporting, "extra")).to_have_attribute("data-tone", "attention")
            if output:
                primary.screenshot(path=str(output / f"{mode}-compact-controls.png"))
                supporting.screenshot(path=str(output / f"{mode}-supplementary.png"))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

            if not phone:
                # Both current detail and rail metadata refresh from the server day.
                title_color = goal.locator(".goal-heading h1").evaluate(
                    "node => getComputedStyle(node).color"
                )
                ring_color = primary.locator(".ring-fill").evaluate(
                    "node => getComputedStyle(node).stroke"
                )
                numeric = page.locator('a[data-goal-id="daily_quota"] small > span').first
                numeric_color = numeric.evaluate("node => getComputedStyle(node).color")
                fixture.advance_day()
                refresh()
                expect(date_control(primary)).to_have_attribute("data-tone", "bad")
                expect(goal.locator(".goal-heading [data-deadline-tone]")).to_have_attribute(
                    "data-deadline-tone", "bad"
                )
                expect(
                    page.locator('a[data-goal-id="checklist"] [data-deadline-tone]')
                ).to_have_attribute("data-deadline-tone", "bad")
                assert (
                    goal.locator(".goal-heading h1").evaluate(
                        "node => getComputedStyle(node).color"
                    )
                    == title_color
                )
                assert (
                    primary.locator(".ring-fill").evaluate("node => getComputedStyle(node).stroke")
                    == ring_color
                )
                assert numeric.evaluate("node => getComputedStyle(node).color") == numeric_color

                # Dismissal does not cancel submitted writes or lose invalid drafts.
                page.evaluate("location.hash='#/workspace'")
                panel = open_preview(page, "checklist")
                control = date_control(panel)
                expect(control).to_have_attribute("data-tone", "bad")
                native = control.locator('input[type="date"]')
                before = len(fixture.writes)
                native.focus()
                native.press("ArrowRight")
                native.press("Backspace")
                assert native.evaluate("node => node.validity.badInput")
                dismiss(page)
                panel = open_preview(page, "checklist")
                expect(date_control(panel).get_by_role("alert")).to_contain_text(
                    "Enter a complete date"
                )
                assert len(fixture.writes) == before
                date_control(panel).get_by_role("button", name="Discard edit", exact=True).click()
                fixture.hold = True
                native = date_control(panel).locator('input[type="date"]')
                native.fill("2026-11-04")
                native.press("Enter")
                wait_fixture(lambda: len(fixture.held) == 1, "Held date write")
                expect(date_control(panel)).to_have_attribute("data-tone", "bad")
                original_request = copy.deepcopy(fixture.writes[-1])
                dismiss(page)
                panel = open_preview(page, "checklist")
                expect(date_control(panel).locator('input[type="date"]')).to_be_disabled()
                fixture.reject_held()
                expect(date_control(panel).get_by_role("alert")).to_contain_text(
                    "Date was not saved"
                )
                assert len(fixture.writes) == before + 1
                date_control(panel).get_by_role("button", name="Retry save", exact=True).click()
                wait_fixture(
                    lambda: accepted_item().get("deadline") == "2026-11-04", "Explicit retry saved"
                )
                assert fixture.writes[-1] == original_request
                assert len(fixture.writes) == before + 2
                expect(date_control(panel).get_by_role("alert")).to_have_count(0)
                fixture.fail_next = 1
                native = date_control(panel).locator('input[type="date"]')
                native.fill("2026-11-05")
                native.press("Enter")
                expect(date_control(panel).get_by_role("alert")).to_contain_text(
                    "Date was not saved"
                )
                date_control(panel).get_by_role("button", name="Discard edit", exact=True).click()
                expect(native).to_have_value("2026-11-04")
                # An external edit invalidates the focused draft. Recovery refreshes
                # accepted data, but only a new explicit edit can use that revision.
                native.fill("2026-11-06")
                accepted_item("review")["detail"] = "Accepted neighboring context"
                fixture.goals["checklist"]["revision"] += 1
                fixture.version += 1
                external_revision = fixture.goals["checklist"]["revision"]
                native.press("Enter")
                expect(date_control(panel).get_by_role("alert")).to_contain_text("Goal changed")
                assert accepted_item()["deadline"] == "2026-11-04"
                date_control(panel).get_by_role("button", name="Discard edit", exact=True).click()
                native.fill("2026-11-07")
                native.press("Enter")
                wait_fixture(
                    lambda: accepted_item().get("deadline") == "2026-11-07",
                    "New explicit edit after conflict",
                )
                assert fixture.writes[-1][1]["expected_revision"] == external_revision
                assert accepted_item("review")["detail"] == "Accepted neighboring context"
                expect(date_control(panel).get_by_role("alert")).to_have_count(0)
                if output:
                    panel.screenshot(path=str(output / "desktop-small-preview.png"))
                dismiss(page)
                page.evaluate("location.hash='#/workspace/chief-of-staff'")
                card = page.locator('[data-goal-card="checklist"]')
                expect(card.locator("[data-deadline-tone]")).to_have_attribute(
                    "data-deadline-tone", "bad"
                )
                expect(date_control(card)).to_have_attribute("data-tone", "neutral")
                page.evaluate("location.hash='#/workspace/goals'")
                table_row = page.locator("tbody tr").filter(
                    has=page.get_by_role("link", name="Prepare the field guide", exact=True)
                )
                expect(table_row.locator("[data-deadline-tone]")).to_have_attribute(
                    "data-deadline-tone", "bad"
                )

            assert errors == [], errors
            receipts.append({"mode": mode, "writes": len(fixture.writes), "errors": errors})
        finally:
            context.close()

    for phone in (False, True):
        run_mode(phone)
    elapsed = time.monotonic() - started
    if output:
        (output / "receipt.json").write_text(
            json.dumps({"seconds": elapsed, "renders": receipts}, indent=2) + "\n"
        )
    print(f"checklist deadlines native desktop/phone: passed (+{elapsed:.2f}s)", flush=True)
