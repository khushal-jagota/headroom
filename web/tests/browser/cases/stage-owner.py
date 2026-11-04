"""Prove structured preset saves through the existing Config host.

This replaces the old immediate owner-save expectations. The product now saves a
coherent draft, and specialist Markdown is generated from that structured draft.
"""

import copy
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect


def run(browser, urls):
    record = {
        "worker_type": "coding",
        "label": "Coding",
        "guidance": "Keep the result concise.",
        "stages": [
            {
                "id": "needs_brief",
                "label": "Brief",
                "gating_field": "brief",
                "is_terminal": False,
                "ownership_mode": "worker",
                "instructions": "Define the job.",
            },
            {
                "id": "needs_consequences",
                "label": "Consequences",
                "gating_field": "consequences",
                "is_terminal": False,
                "ownership_mode": "worker",
                "instructions": "Record the result.",
            },
            {
                "id": "done",
                "label": "Done",
                "gating_field": None,
                "is_terminal": True,
                "ownership_mode": None,
                "instructions": "",
            },
        ],
        "fields": [
            {"id": "brief", "label": "Brief"},
            {"id": "consequences", "label": "Consequences"},
        ],
        "profile": {
            "specialist_skill": "panels-worker-coding",
            "default_model": "fixture-model",
            "default_reasoning_effort": None,
            "toolset_profile": "default",
            "default_backend": "codex",
        },
    }
    posts = []
    refuse_next = False
    errors = []

    def manifest():
        return {
            **copy.deepcopy(record),
            "advance": {"needs_brief": "needs_consequences", "needs_consequences": "done"},
            "ceiling_range": ["needs_brief", "needs_consequences", "done"],
            "default_ceiling": "needs_brief",
            "worker_profile_id": "panels-worker-coding",
            "default_backend": "codex",
            "default_model": "fixture-model",
            "default_reasoning_effort": None,
        }

    def detail():
        return {
            "manifest": manifest(),
            "settings": {
                "worker_type": "coding",
                "specialist_skill": {
                    "name": "panels-worker-coding",
                    "description": "Coding worker",
                    "markdown_body": record["guidance"],
                },
                "launch_defaults": {
                    "employee_backend": "codex",
                    "employee_launch_model": "fixture-model",
                    "employee_launch_reasoning_effort": None,
                },
            },
        }

    def respond(route):
        nonlocal refuse_next
        parsed = urlsplit(route.request.url)
        path = parsed.path
        resource_key = None
        if path == "/api/resource-body":
            resource_key = parse_qs(parsed.query)["key"][0]
            path = urlsplit(resource_key).path
        method = route.request.method
        if path == "/api/worker-types" and method == "POST":
            body = route.request.post_data_json
            posts.append(body)
            if refuse_next:
                refuse_next = False
                route.fulfill(
                    status=409,
                    json={"error": {"code": "validation", "message": "Preset save refused."}},
                )
                return
            record.update(body)
            result = copy.deepcopy(record)
        elif path == "/api/worker-types/coding":
            result = copy.deepcopy(record)
        elif path == "/api/workers/coding":
            result = detail()
        elif path == "/api/worker-types":
            result = {"worker_types": [manifest()]}
        elif path == "/api/projects":
            result = {"projects": []}
        elif path == "/api/managers":
            result = {"managers": []}
        elif path == "/api/conversation/backends":
            result = {"backends": []}
        else:
            result = {}
        if resource_key:
            result = {"epoch": "fixture", "key": resource_key, "revision": "1", "body": result}
        route.fulfill(json=result)

    page = browser.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)
    page.goto(urls[0])
    editor = page.locator("[data-preset-workflow]")
    expect(editor.locator("[data-workflow-stage]")).to_have_count(3)
    expect(editor.locator('[data-workflow-stage="done"] select')).to_have_count(0)
    owner = editor.locator('[data-workflow-stage="needs_consequences"] select')
    owner.select_option("user")
    editor.get_by_label("Across the workflow").fill("Keep the evidence concise.")
    assert posts == [], "Editing a draft must not save a partial preset"
    editor.locator("[data-save-workflow]").click()
    expect(editor.locator("[data-save-workflow]")).to_be_disabled()
    assert len(posts) == 1
    assert posts[0]["stages"][1]["ownership_mode"] == "user"
    assert posts[0]["guidance"] == "Keep the evidence concise."
    assert posts[0]["profile"] == record["profile"]
    page.reload()
    editor = page.locator("[data-preset-workflow]")
    expect(editor.locator('[data-workflow-stage="needs_consequences"] select')).to_have_value(
        "user"
    )
    expect(editor.get_by_label("Across the workflow")).to_have_value("Keep the evidence concise.")
    expect(page.locator("[data-worker-skill] [contenteditable]")).to_have_count(0)

    refuse_next = True
    editor.get_by_label("Across the workflow").fill("Preserve this draft after refusal.")
    editor.locator("[data-save-workflow]").click()
    expect(editor.get_by_text("Preset save refused.", exact=True)).to_be_visible()
    expect(editor.get_by_label("Across the workflow")).to_have_value(
        "Preserve this draft after refusal."
    )
    editor.locator("[data-save-workflow]").click()
    expect(editor.locator("[data-save-workflow]")).to_be_disabled()
    assert len(posts) == 3
    assert record["guidance"] == "Preserve this draft after refusal."

    assert errors == [], errors
    page.close()
