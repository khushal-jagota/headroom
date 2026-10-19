# ruff: noqa: E501
"""Independent browser case, extracted without assertion changes."""


def run(browser, urls):

    import copy
    import json
    import sys
    import time

    from playwright.sync_api import expect

    # The stored record, in the exact shape GET returns and POST takes back.
    record = {
        "worker_type": "coding",
        "label": "Coding",
        "stages": [
            {
                "id": "needs_success_condition",
                "label": "Success Condition",
                "gating_field": "success_condition",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "needs_plan",
                "label": "Plan",
                "gating_field": "plan",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "done",
                "label": "Done",
                "gating_field": None,
                "is_terminal": True,
                "ownership_mode": None,
            },
        ],
        "fields": [
            {"id": "success_condition", "label": "Success Condition"},
            {"id": "plan", "label": "Plan"},
        ],
        "profile": {
            "specialist_skill": "panels-worker-coding",
            "default_model": "opus",
            "default_reasoning_effort": "medium",
            "toolset_profile": "coding",
            "default_backend": "claude",
        },
    }

    def detail():
        return {
            "manifest": {
                "worker_type": "coding",
                "label": "Coding",
                "stages": copy.deepcopy(record["stages"]),
                "advance": {"needs_success_condition": "needs_plan", "needs_plan": "done"},
                "fields": copy.deepcopy(record["fields"]),
                "ceiling_range": ["needs_success_condition", "needs_plan", "done"],
                "default_ceiling": "needs_plan",
                "worker_profile_id": "panels-worker-coding",
                "default_backend": "claude",
                "default_model": "opus",
                "default_reasoning_effort": "medium",
            },
            "settings": {
                "worker_type": "coding",
                "specialist_skill": {
                    "name": "panels-worker-coding",
                    "description": "Coding worker",
                    "markdown_body": "Body",
                },
                "launch_defaults": {
                    "employee_backend": "claude",
                    "employee_launch_model": "opus",
                    "employee_launch_reasoning_effort": "medium",
                },
            },
        }

    posts = []
    save_events = []
    browser_events = []
    started_at = time.monotonic()

    def save_event(number, state, **details):
        save_events.append(
            {
                "save": number,
                "state": state,
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                **details,
            }
        )

    refuse_next = {"value": False}

    def respond(route):
        path = route.request.url.split("/api/", 1)[1]
        if path == "worker-types" and route.request.method == "POST":
            body = route.request.post_data_json
            posts.append(body)
            number = len(posts)
            save_event(number, "submitted", stages=copy.deepcopy(body["stages"]))
            if refuse_next["value"]:
                refuse_next["value"] = False
                route.fulfill(
                    status=409,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": {
                                "code": "validation",
                                "message": "removing a Stage would strand unfinished Tickets",
                            }
                        }
                    ),
                )
                save_event(number, "refused", status=409)
                return
            record["stages"] = body["stages"]
            result = body
        elif path == "worker-types/coding":
            result = copy.deepcopy(record)
        elif path == "workers/coding":
            result = detail()
        elif path == "test/refuse-next":
            refuse_next["value"] = True
            result = {}
        elif path == "conversation/backends":
            result = {"backends": []}
        else:
            result = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))
        if path == "worker-types" and route.request.method == "POST":
            save_event(number, "completed", status=200)

    def wait_for_completed_save(page, number, timeout_ms=5000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if any(
                event["save"] == number and event["state"] == "completed" for event in save_events
            ):
                return
            page.wait_for_timeout(20)
        raise AssertionError(f"Save {number} did not complete: {save_events!r}")

    def failure_evidence(page):
        # Collect independently: a closed page or a collector error must not hide the
        # original assertion or the in-memory save and storage evidence.
        collectors = {
            "stored_stages": lambda: copy.deepcopy(record["stages"]),
            "save_events": lambda: copy.deepcopy(save_events),
            "browser_events": lambda: list(browser_events),
            "page": lambda: page.evaluate("""() => ({
                url: location.href, visibility: document.visibilityState,
                has_focus: document.hasFocus(),
                rows: [...document.querySelectorAll('[data-worker-stage-row]')].map(row => ({
                    stage: row.getAttribute('data-stage'),
                    owner: row.querySelector('[data-stage-owner]')?.getAttribute('data-stage-owner'),
                    selected: row.querySelector('select')?.value,
                    disabled: row.querySelector('select')?.disabled,
                    error: row.querySelector('[data-stage-owner-error]')?.textContent
                }))
            })"""),
        }
        for name, collect in collectors.items():
            try:
                value = collect()
            except Exception as error:
                value = {"collector_error": str(error)}
            print(
                "STAGE_OWNER_FAILURE " + json.dumps({name: value}, default=str),
                file=sys.stderr,
                flush=True,
            )

    page = browser.new_page()
    page.on(
        "pageerror", lambda error: browser_events.append({"type": "pageerror", "text": str(error)})
    )
    page.on(
        "console",
        lambda message: browser_events.append({"type": message.type, "text": message.text}),
    )
    page.route("**/api/**", respond)
    try:
        page.goto([__file__, *urls][1])

        table = page.locator("[data-worker-stage-table]")
        expect(table).to_have_count(1)

        # Every non-terminal Stage carries a control over the two stored modes.
        selects = table.locator("[data-stage-owner-select]")
        expect(selects).to_have_count(2)
        plan_row = table.locator('[data-worker-stage-row][data-stage="needs_plan"]')
        plan_select = plan_row.locator("[data-stage-owner-select]")
        expect(plan_select).to_have_attribute("aria-label", "Owner for Plan")
        assert plan_select.locator("option").all_inner_texts() == ["worker", "user"]
        expect(plan_select).to_have_value("worker")

        # A terminal Stage has no control.
        done_row = table.locator('[data-worker-stage-row][data-stage="done"]')
        expect(done_row.locator("[data-terminal-owner]")).to_have_text("terminal")
        expect(done_row.locator("[data-stage-owner-select]")).to_have_count(0)

        # Changing the owner posts the whole record with one value changed.
        plan_select.select_option("user")
        for _ in range(200):
            if len(posts) == 1:
                break
            page.wait_for_timeout(20)
        assert len(posts) == 1, posts
        assert "skill" not in posts[0]
        assert set(posts[0]) == {"worker_type", "label", "stages", "fields", "profile"}
        assert posts[0]["profile"] == record["profile"]
        assert [(stage["id"], stage["ownership_mode"]) for stage in posts[0]["stages"]] == [
            ("needs_success_condition", "worker"),
            ("needs_plan", "user"),
            ("done", None),
        ]
        expect(plan_row.locator("[data-stage-owner]")).to_have_attribute(
            "data-stage-owner", "user", timeout=5000
        )
        expect(plan_select).to_have_value("user")
        expect(plan_row.locator("[data-stage-owner-error]")).to_have_count(0)

        # A reload shows the stored owner, not a local memory of the change.
        page.reload()
        plan_row = page.locator('[data-worker-stage-row][data-stage="needs_plan"]')
        expect(plan_row.locator("[data-stage-owner-select]")).to_have_value("user")

        # A refused save shows the refusal on the row and reverts the control.
        page.evaluate("fetch('/api/test/refuse-next', {method: 'POST'})")
        plan_select = plan_row.locator("[data-stage-owner-select]")
        plan_select.select_option("worker")
        expect(plan_row.locator("[data-stage-owner-error]")).to_have_text(
            "removing a Stage would strand unfinished Tickets"
        )
        expect(plan_select).to_have_value("user")
        assert len(posts) == 2, posts

        # The next save after a refusal still works.
        plan_select.select_option("worker")
        expect(plan_row.locator("[data-stage-owner]")).to_have_attribute(
            "data-stage-owner", "worker", timeout=5000
        )
        expect(plan_row.locator("[data-stage-owner-error]")).to_have_count(0)
        # The row is optimistic. Only the completed retry proves persistence.
        wait_for_completed_save(page, 3)
        assert [(stage["id"], stage["ownership_mode"]) for stage in record["stages"]] == [
            ("needs_success_condition", "worker"),
            ("needs_plan", "worker"),
            ("done", None),
        ]
        # Two Stages changed in quick succession both survive: the second save reads the
        # record the first one wrote, instead of posting the value from before it.
        page.evaluate("""() => {
          const rows = document.querySelectorAll('[data-stage-owner-select]');
          for (const row of rows) {
            row.value = 'user';
            row.dispatchEvent(new Event('change', {bubbles: true}));
          }
        }""")
        for _ in range(400):
            if len(posts) == 5:
                break
            page.wait_for_timeout(20)
        assert [(stage["id"], stage["ownership_mode"]) for stage in record["stages"]] == [
            ("needs_success_condition", "user"),
            ("needs_plan", "user"),
            ("done", None),
        ], record["stages"]
    except BaseException:
        try:
            failure_evidence(page)
        except BaseException as error:
            print("STAGE_OWNER_FAILURE collector_error: " + str(error), file=sys.stderr, flush=True)
        raise
    finally:
        pass  # Shared runner owns the browser lifecycle.
