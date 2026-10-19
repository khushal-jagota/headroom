# ruff: noqa: E501
"""Rendered production components for configuration and frontend wiring gaps."""

from urllib.parse import quote, urlencode, urlsplit

from playwright.sync_api import expect


def run(browser, url):
    context = browser.new_context(has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "?mode=chart")
    plot = page.get_by_role("button", name="Inspect recorded usage.", exact=False)
    box = plot.bounding_box()
    # First touch must select the later period without pointer movement.
    page.touchscreen.tap(box["x"] + box["width"] - 2, box["y"] + 20)
    expect(page.locator(".inspection")).to_contain_text("333")
    expect(page.locator(".inspection")).to_contain_text("Codex")
    plot.focus()
    plot.press("Home")
    expect(page.locator(".inspection")).to_contain_text("111")
    plot.press("ArrowRight")
    expect(page.locator(".inspection")).to_contain_text("222")
    plot.press("End")
    expect(page.locator(".inspection")).to_contain_text("333")

    snapshot = {
        "backend_key": "codex",
        "installed": True,
        "executable_path": "/fixture/codex",
        "version": "fixture",
        "identity": {
            "status": "unauthenticated",
            "account_label": None,
            "detail": "Credential rejected",
            "login_command": "codex login",
        },
        "available_models": [],
        "reasoning_effort_options": [],
        "cached_usage": None,
        "update_advisory": None,
        "diagnoses": [],
    }
    pending = {
        "session_id": "sign-in-fixture",
        "state": "pending",
        "verification_url": "https://login.invalid/device",
        "user_code": "ABCD-EFGH",
        "started_at": "2026-10-18T00:00:00Z",
        "deadline_at": "2026-10-18T00:10:00Z",
        "detail": None,
        "account_label": None,
    }
    requests = []
    session = None
    refuse = True

    def respond(route):
        nonlocal session, refuse
        path = route.request.url.split("/api/", 1)[1]
        method = route.request.method
        requests.append((method, path))
        if path.startswith("conversation/backends?") or path == "conversation/backends":
            result = {"backends": [snapshot]}
        elif path == "conversation/backends/codex/sign-in" and method == "POST":
            if refuse:
                refuse = False
                route.fulfill(status=503, json={"detail": "Sign-in start refused"})
                return
            session = pending.copy()
            result = {"sign_in": session}
        elif path.endswith("/sign-in/sign-in-fixture") and method == "DELETE":
            session = {**pending, "state": "ended", "user_code": None, "verification_url": None}
            result = {"sign_in": session}
        elif path.endswith("/sign-in"):
            result = {"sign_in": session}
        else:
            result = {}
        route.fulfill(json=result)

    page.route("**/api/**", respond)
    page.goto(url)
    caveat = page.locator("[data-backend-sign-in-caveat]")
    expect(caveat).to_have_text(
        "Signing in writes a new credential for this account. If a turn is running, it may have to be retried."
    )
    expect(page.locator("[data-backend-identity-note]").first).to_be_visible()
    assert not any(method == "POST" for method, _ in requests)
    start = page.locator('[data-backend-sign-in-start="codex"]')
    start.click()
    expect(page.get_by_text("Sign-in start refused", exact=False)).to_be_visible()
    expect(start).to_be_enabled()
    start.click()
    expect(page.locator("[data-backend-sign-in-code]")).to_have_text("ABCD-EFGH")
    expect(page.locator("[data-backend-sign-in-url]")).to_have_attribute(
        "href", "https://login.invalid/device"
    )
    page.locator('[data-backend-sign-in-cancel="codex"]').click()
    expect(start).to_be_visible()
    assert ("DELETE", "conversation/backends/codex/sign-in/sign-in-fixture") in requests
    start.click()
    expect(page.locator("[data-backend-sign-in-code]")).to_be_visible()
    before = len(requests)
    page.evaluate("window.hideBackend()")
    expect(page.locator('[data-screen="backends"]')).to_have_count(0)
    page.wait_for_timeout(2300)  # Exceeds the real 2000ms sign-in poll interval after departure.
    assert len(requests) == before, requests[before:]
    assert errors == [], errors
    context.close()

    # A real mounted query consumer must receive new data after source callbacks.
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script("""window.EventSource = class {
      constructor(url) {window.gapSource = this; this.url=url;}
      close() {this.closed=true;}
    };""")
    revision = 1

    def visible(route):
        route.fulfill(json={"revision": revision})

    page.route("**/api/gap-visible", visible)
    page.goto(url + "?mode=stream")
    expect(page.locator("[data-visible-revision]")).to_have_text("1")
    revision = 2
    page.evaluate("window.gapSource.onopen()")
    expect(page.locator("[data-visible-revision]")).to_have_text("2")
    revision = 3
    page.evaluate("window.gapSource.onmessage()")
    expect(page.locator("[data-visible-revision]")).to_have_text("3")
    context.close()
    run_skill_editors(browser, url, snapshot)
    print("configuration gaps: rendered sign-in, touch/keyboard chart, mounted stream passed")


def run_skill_editors(browser, url, backend):
    # Existing SSR coverage cannot invoke InlineEdit callbacks or prove query refresh.
    # Reuse this host, provider, static server, and browser with intercepted API reads.
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    names = [
        "panels-chief-of-staff",
        "panels-worker",
        "panels-sprint-item-supervisor",
        "coding-specialist+candidate",
    ]
    skills = {
        name: {"name": name, "description": "Original description", "markdown_body": "Original body"}
        for name in [*names, "panels-worker-coding"]
    }
    defaults = {
        "employee_backend": "codex",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": None,
    }
    manifest = {
        "worker_type": "coding",
        "label": "Coding",
        "stages": [
            {"id": "needs_implementation", "label": "Implementation", "gating_field": "implementation", "is_terminal": False, "ownership_mode": "worker"},
            {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True, "ownership_mode": None},
        ],
        "fields": [{"id": "implementation", "label": "Implementation"}],
    }
    writes = []
    expected_writes = []
    refuse_body = True

    def respond(route):
        nonlocal refuse_body
        path = urlsplit(route.request.url).path
        method = route.request.method
        if method == "PATCH":
            body = route.request.post_data_json
            writes.append((method, path, body))
            if path == "/api/skills/" + quote(names[-1], safe="") and "markdown_body" in body and refuse_body:
                refuse_body = False
                route.fulfill(status=503, json={"error": {"code": "fixture_refusal", "message": "Skill save refused. Retry the edit."}})
                return
            name = next((name for name in names if path == "/api/skills/" + quote(name, safe="")), None)
            assert name is not None, f"Unexpected skill save destination: {path}"
            assert len(body) == 1 and set(body) <= {"description", "markdown_body"}, body
            skills[name].update(body)
            # A concurrent edit becomes visible only through the invalidated query.
            other_field = "markdown_body" if "description" in body else "description"
            skills[name][other_field] = "Refetched " + other_field
            result = skills[name]
        elif method == "GET" and path == "/api/workers":
            result = {"workers": [], "chief_of_staff": {"employee_id": "chief", "label": "Chief", "skill": skills[names[0]], "launch_defaults": defaults}}
        elif method == "GET" and path == "/api/skills":
            result = {"skills": list(skills.values())}
        elif method == "GET" and path == "/api/workers/coding":
            result = {"manifest": manifest, "settings": {"worker_type": "coding", "specialist_skill": skills["panels-worker-coding"], "candidate_specialist_skill": skills[names[-1]], "launch_defaults": defaults}}
        elif method == "GET" and path == "/api/conversation/backends":
            result = {"backends": [backend]}
        else:
            raise AssertionError(f"Unexpected Config request: {method} {path}")
        route.fulfill(json=result)

    page.route("**/api/**", respond)
    destinations = [
        ("agent", "chief_of_staff", "Chief"),
        ("skill", names[1], "Worker"),
        ("skill", names[2], "Sprint Item supervisor"),
        ("worker", "coding", "Specialist"),
    ]
    for name, (role_kind, role_id, label) in zip(names, destinations, strict=True):
        page.goto(url + "?" + urlencode({"mode": "config", "roleKind": role_kind, "roleId": role_id}))
        expect(page.locator("[data-skill-name]")).to_have_text(name)
        description = page.get_by_role("textbox", name=f"{label} skill description", exact=True)
        body = page.get_by_role("textbox", name=f"{label} skill Markdown body", exact=True)
        description.fill("Saved description")
        description.press("Control+Enter")
        expect(body).to_have_text("Refetched markdown_body")
        expected_writes.append(("PATCH", "/api/skills/" + quote(name, safe=""), {"description": "Saved description"}))

        body.fill("Saved body")
        body.press("Control+Enter")
        body_write = ("PATCH", "/api/skills/" + quote(name, safe=""), {"markdown_body": "Saved body"})
        expected_writes.append(body_write)
        if role_kind == "worker":
            expect(page.get_by_text("Skill save refused. Retry the edit.", exact=True)).to_be_visible()
            expect(body).to_have_text("Saved body")
            # Focus and blur the preserved source, without any extra input.
            body.focus()
            body.press("Control+Enter")
            expected_writes.append(body_write)
        expect(description).to_have_text("Refetched description")
        page.reload()
        expect(description).to_have_text("Refetched description")
        expect(body).to_have_text("Saved body")

    assert writes == expected_writes
    assert errors == [], errors
    context.close()
    print("configuration skills: four canonical destinations, field refresh, candidate identity, and Markdown retry passed")
