# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import os

    page = browser.new_page()
    screenshot_dir = os.environ.get("SCHEDULED_TASKS_SCREENSHOT_DIR")
    page.goto([__file__, *urls][1] + "#/scheduled-tasks", wait_until="networkidle")
    page.locator('section[data-screen="scheduled-tasks"]').wait_for()
    page.wait_for_function("window.location.hash === '#/settings/scheduled-tasks'")
    settings_navigation = page.get_by_role("navigation", name="Settings", exact=True)
    scheduled_tasks_link = settings_navigation.get_by_role(
        "link", name="Scheduled tasks", exact=True
    )
    assert scheduled_tasks_link.get_attribute("href") == "#/settings/scheduled-tasks"
    assert scheduled_tasks_link.get_attribute("aria-current") == "page"
    assert settings_navigation.locator('a[aria-current="page"]').count() == 1
    if screenshot_dir:
        page.screenshot(
            path=os.path.join(screenshot_dir, "scheduled-tasks-list-desktop.png"), full_page=True
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(
            path=os.path.join(screenshot_dir, "scheduled-tasks-list-phone.png"), full_page=True
        )
        page.set_viewport_size({"width": 1280, "height": 900})
    card = page.locator('[data-schedule-id="schedule_demo"]')
    card.wait_for()
    assert "Existing daily task" in card.inner_text()
    assert card.locator("[data-schedule-title]").inner_text() == "Existing daily task"
    assert card.locator("[data-schedule-toggle]").get_attribute("aria-checked") == "true"

    page.get_by_role("button", name="Add").click()
    editor = page.locator("[data-schedule-editor]")
    editor.wait_for()
    if screenshot_dir:
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(
            path=os.path.join(screenshot_dir, "scheduled-tasks-editor-phone.png"), full_page=True
        )
        page.set_viewport_size({"width": 1280, "height": 900})
    assert editor.locator('[data-input="manager"]').input_value() == ""
    assert editor.locator('[data-input="worker-type"] option[value="planning-day"]').count() == 0
    assert editor.locator('[data-input="cadence"]').inner_text() == "Every day"
    assert (
        editor.locator(
            '[data-input="placement"], [data-input="sprint"], [data-input="sprint-item"]'
        ).count()
        == 0
    )
    submit = editor.locator("[data-commit-schedule]")
    submit.click()
    page.locator("[data-schedule-error]").wait_for()
    assert "title" in page.locator("[data-schedule-error]").inner_text().lower()
    assert submit.is_enabled()

    title = editor.locator("[data-schedule-title] .ed")
    title.fill("Reject me")
    submit.click()
    page.locator(".error-line").wait_for()
    assert "schedule was rejected" in page.locator(".error-line").inner_text()

    title.fill("Created browser schedule")
    editor.locator('[data-input="local-time"]').fill("11:30")
    editor.locator('[data-input="project"]').select_option("project_panels")
    editor.locator('[data-input="manager"]').select_option("si_demo")
    submit.click()
    created = page.locator('[data-schedule-id="schedule_created"]')
    created.wait_for()
    assert "Created browser schedule" in created.inner_text()
    created.locator("[data-schedule-title]").click()
    editor.locator('[data-input="local-time"]').wait_for()
    assert editor.locator('[data-input="local-time"]').input_value() == "11:30"
    assert editor.locator('[data-input="manager"]').input_value() == "si_demo"
    page.locator("[data-back-to-schedules]").click()

    card.locator("[data-schedule-toggle]").click()
    page.wait_for_function(
        "document.querySelector('[data-schedule-id=\\\"schedule_demo\\\"] [data-schedule-toggle]')?.getAttribute('aria-checked') === 'false'"
    )
    assert card.locator("[data-schedule-toggle]").get_attribute("aria-checked") == "false"
    card.locator("[data-schedule-title]").click()
    assert editor.locator('[data-input="manager"]').input_value() == "si_demo"
    editor.locator('[data-input="project"]').select_option("project_panels")
    assert editor.locator('[data-input="manager"]').input_value() == "si_demo"
    editor.locator('[data-input="project"]').select_option("project_other")
    assert editor.locator('[data-input="manager"]').input_value() == ""
    editor.locator("[data-schedule-title] .ed").fill("Edited existing schedule")
    editor.locator('[data-input="local-time"]').fill("09:50")
    editor.get_by_text("Advanced Ticket settings").click()
    editor.locator('[data-input="employee-backend"]').fill("codex")
    editor.get_by_role("button", name="Save changes").click()
    card = page.locator('[data-schedule-id="schedule_demo"]')
    card.locator("[data-schedule-title]").filter(has_text="Edited existing schedule").wait_for()
    assert "Edited existing schedule" in card.inner_text()
    assert page.locator("[data-schedule-id]").count() == 5

    requests = page.evaluate("window.__requests()")
    assert any(
        request["path"] == "/api/schedules" and request["method"] == "POST" for request in requests
    )
    assert any(
        request["path"] == "/api/schedules/schedule_demo" and request["method"] == "PATCH"
        for request in requests
    )
    assert any(
        request["method"] == "PATCH" and request["body"]["enabled"] is False for request in requests
    )
    assert any(
        request["path"] == "/api/schedules/schedule_demo"
        and request["method"] == "PATCH"
        and request["body"].get("title") == "Edited existing schedule"
        for request in requests
    )
    placement = next(
        request["body"]
        for request in requests
        if request["method"] == "PATCH"
        and request["body"].get("title") == "Edited existing schedule"
    )
    assert placement["project_id"] == "project_other"
    assert placement["manager"] == {"kind": "chief", "id": "chief"}
    for request in requests:
        if request["method"] in ("POST", "PATCH") and "/api/schedules" in request["path"]:
            assert not ({"placement_mode", "sprint_id", "manager_id"} & set(request["body"]))
    created_body = next(
        request["body"]
        for request in requests
        if request["method"] == "POST"
        and request["body"].get("title") == "Created browser schedule"
    )
    assert created_body["cadence"] == "daily"
    assert created_body["manager"] == {"kind": "manager", "id": "si_demo"}
    retired = page.locator('[data-schedule-id="schedule_retired"]')
    assert retired.locator("[data-schedule-toggle]").is_disabled()
    before = len(page.evaluate("window.__requests()"))
    retired.locator("[data-schedule-title]").click()
    assert "Every planning day" in editor.locator("[data-retired-schedule]").inner_text()
    assert "Historical cadence" in editor.locator("[data-retired-schedule]").inner_text()
    assert "Current sprint" not in editor.locator("[data-retired-schedule]").inner_text()
    assert "sprint_history" not in editor.inner_text()
    assert (
        editor.locator('[data-input="worker-type"] option:checked').inner_text()
        == "Retired planning"
    )
    assert editor.locator('[data-input="worker-type"]').input_value() == "planning-day"
    assert editor.locator('[data-input="local-time"]').is_disabled()
    assert editor.locator('[data-input="enabled"]').is_disabled()
    assert editor.locator("[data-commit-schedule]").count() == 0
    assert editor.locator("[data-schedule-title] .ed").count() == 0
    assert editor.locator('[data-input="enabled"]').get_attribute("aria-checked") == "false"
    assert editor.locator('[data-input="project"]').is_disabled()
    assert editor.locator('[data-input="manager"]').is_disabled()
    if screenshot_dir:
        page.screenshot(path=os.path.join(screenshot_dir, "historical-schedule-locked.png"), full_page=True)
    page.locator("[data-back-to-schedules]").click()
    personal = page.locator('[data-schedule-id="schedule_personal"]')
    assert personal.locator("[data-schedule-toggle]").is_disabled()
    assert personal.get_by_role("switch", name="Enable schedule").count() == 0
    personal.locator("[data-schedule-title]").click()
    assert "Retired schedule" in editor.locator("[data-retired-schedule]").inner_text()
    assert "Every day" in editor.locator("[data-retired-schedule]").inner_text()
    assert editor.locator('[data-input="worker-type"]').is_disabled()
    assert editor.locator('[data-input="enabled"]').is_disabled()
    assert editor.get_by_role("switch", name="Enable schedule").count() == 0
    assert editor.locator("[data-commit-schedule]").count() == 0
    page.locator("[data-back-to-schedules]").click()
    active_retired = page.locator('[data-schedule-id="schedule_retired_active"]')
    active_retired.locator("[data-schedule-toggle]").click()
    page.wait_for_function(
        "window.__requests().some(r => r.path.endsWith('/schedule_retired_active') && r.method === 'PATCH')"
    )
    disable = next(
        request
        for request in page.evaluate("window.__requests()")
        if request["path"].endswith("/schedule_retired_active") and request["method"] == "PATCH"
    )
    assert disable["body"] == {"enabled": False}
    assert active_retired.locator("[data-schedule-toggle]").is_disabled()
    assert not any(
        request["method"] != "GET" and request["path"].endswith("/schedule_retired")
        for request in page.evaluate("window.__requests()")[before:]
    )
    if screenshot_dir:
        page.evaluate("window.location.hash = '#/sprint'")
        page.get_by_text("no such screen", exact=True).wait_for()
        page.screenshot(path=os.path.join(screenshot_dir, "sprint-route-unavailable.png"), full_page=True)
    pass  # Shared runner owns the browser lifecycle.

    print("scheduled tasks browser assertions passed")
