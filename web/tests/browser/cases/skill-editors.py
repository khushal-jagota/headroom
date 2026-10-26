# ruff: noqa: E501
"""Prove real skill editor callbacks, canonical saves, refetch, and retry.

SSR coverage cannot invoke InlineEdit callbacks or prove mounted query refresh.
One host uses the catalog runner's shared build, static server, and browser.
"""

from urllib.parse import quote, urlencode, urlsplit

from playwright.sync_api import expect


def run(browser, urls):
    backend = {
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

    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    names = [
        "panels-chief-of-staff",
        "panels-worker",
        "panels-manager",
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
        ("skill", names[2], "Manager"),
        ("worker", "coding", "Specialist"),
    ]
    for name, (role_kind, role_id, label) in zip(names, destinations, strict=True):
        page.goto(urls[0] + "?" + urlencode({"roleKind": role_kind, "roleId": role_id}))
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
            # Retry the preserved source without extra input.
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
