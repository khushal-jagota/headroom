# ruff: noqa: E501
"""An artifact opened from a conversation keeps that conversation under it.

The press that opens an artifact is claimed across the whole pane, the conversation layer
included. Before this, a press on an artifact link inside a Manager's conversation was
left to the browser, which followed it to the one-document `#/preview` route and took the
conversation the reader was in with it. That is a fact about where the handler sits and
about the browser following a link, so neither half can be proved without a real browser,
and neither can be proved on a stand-in host: the two real hosts differ in exactly the
thing under test, because the Chief's pane keeps its artifact in the Workspace address and
a Manager's pane keeps its own.

So this mounts the application shell and drives the real Chief pane and a real Manager
pane, each with its own real conversation, through the real navigation projection and the
real conversation view reader.

The same host also protects the distinct standalone/Review boundary: the file owner
supplies the recipient, an artifact visit does not acknowledge Review or activate its
shortcuts, and the return restores the card, draft, and skipped cohort. Existing
source-pane assertions do not exercise this boundary. Synthetic HTTP responses establish
browser routing and native send payloads; they do not establish provider delivery.
"""

import json
import os
import runpy
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

sys.path.insert(0, str(Path(__file__).resolve().parents[3].parent / "src"))
from planner.conversation.views.contracts import (  # noqa: E402
    VIEW_PROJECTION_VERSION,
    VIEW_PROTOCOL_VERSION,
)

MANAGER_ID = "si_fixture"
MANAGER_CONVERSATION = "manager-conversation"
CHIEF_CONVERSATION = "chief-conversation"
FILE_CONVERSATION = "file-conversation"
MANAGER_TITLE = "Rollout manager"
# Each conversation names an artifact owned by a Ticket that is not that conversation's
# owner. That cross-ownership is the reported case, and it is why the artifact cannot be
# reached by going to the owner's own page.
MANAGER_ARTIFACT = "/files/tickets/t_elsewhere/artifacts/plan.md"
CHIEF_ARTIFACT = "/files/tickets/t_faraway/artifacts/budget.md"
MANAGER_TRANSCRIPT = "The rollout manager keeps its own thread."
CHIEF_TRANSCRIPT = "The Chief keeps its own thread."


def row(sequence, body):
    """One projected transcript row, in the shape the production reader validates."""
    return {
        "kind": "row",
        "key": f"row:{sequence}",
        "row_kind": body["kind"],
        "order": {"sequence": sequence, "slot": 0},
        "source_sequence": sequence,
        "sender_message_id": None,
        "changed_through_sequence": sequence,
        "created_at": sequence,
        "turn_key": None,
        "work_group_key": None,
        "fold_rule": "never",
        "body": {"kind": "inline", "value": body},
    }


def prompt(sequence, text):
    return row(sequence, {"kind": "prompt", "content": [{"piece": "text", "text": text}],
                          "sender_label": "owner", "mode": "queue",
                          "sent_at_unix_milliseconds": sequence})


def answer(sequence, text):
    return row(sequence, {"kind": "agent_message", "content": [{"piece": "text", "text": text}],
                          "to_owner": True})


TRANSCRIPTS = {
    FILE_CONVERSATION: [prompt(1, "Read this artifact"), answer(2, "The file owner answers here. Read [the budget](/files/tickets/t_faraway/artifacts/budget.md).")],
    MANAGER_CONVERSATION: [
        prompt(1, "Where did the rollout plan end up?"),
        answer(2, f"It landed on another Ticket: [the plan]({MANAGER_ARTIFACT}).\n\n{MANAGER_TRANSCRIPT}"),
    ],
    CHIEF_CONVERSATION: [
        prompt(1, "What should I read first?"),
        answer(2, f"Start with [the note]({CHIEF_ARTIFACT}).\n\n{CHIEF_TRANSCRIPT}"),
    ],
}


def conversation_page(conversation_id, lens, transcript=True):
    items = TRANSCRIPTS[conversation_id] if transcript else []
    return {
        "conversation_id": conversation_id,
        "protocol_version": VIEW_PROTOCOL_VERSION,
        "projection_version": VIEW_PROJECTION_VERSION,
        "lens": lens,
        "scope": {"kind": "timeline", "key": None},
        "items": items,
        "older": None,
        "newer": None,
        "body_bytes": len(json.dumps(items).encode()),
    }


def conversation_status(conversation_id):
    rows = TRANSCRIPTS[conversation_id]
    latest = rows[-1]["body"]["value"]["content"][0]["text"]
    return {
        "diagnostics_expired": False,
        "runtime_epoch": "fixture",
        "revision": len(rows),
        "activity_at_unix_milliseconds": None,
        "authority": "current",
        "turn_key": None,
        "turn_running": False,
        "stopped_without_ending": False,
        "pending_permission": None,
        "pending_user_input": None,
        "plan": None,
        "live_output": None,
        "live_tool_key": None,
        "live_tool_progress": None,
        "rest": {
            "latest_happened": {"kind": "agent_message", "sender_label": None, "excerpt": latest},
            "latest_turn": None,
            "latest_tool": None,
            "permission_title_excerpt": None,
            "latest_owner_message_preview": latest,
            "latest_owner_message_sequence": len(rows),
            "last_boundary_failed": False,
            "plan_progress": {"total_count": 0, "completed_count": 0, "current_position": None,
                              "current_text_excerpt": None, "has_unfinished_entry": False},
        },
    }


def conversation_detail(conversation_id):
    return {
        "conversation_id": conversation_id,
        "backend_key": "codex",
        "supports_steer": True,
        "model": None,
        "reasoning_effort": None,
        "workspace_folder": "/fixture",
        "access": "full",
        "role_text": None,
        "identity_environment_variable_names": [],
        "latest_sequence": len(TRANSCRIPTS[conversation_id]),
        "owner_read_through_sequence": len(TRANSCRIPTS[conversation_id]),
        "is_running": False,
        "held_prompts": [],
        "pending_permission_ask": None,
        "pending_user_input": None,
        "composer_catalog": [],
    }


def run(browser, urls):
    url = urls[0]
    fixture = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "support/navigation/browser-fixture.py")
    )
    # Enough Workers under the Manager that its document is taller than the pane, so the
    # reading position it is scrolled to is a real one.
    tickets = [
        {"id": f"t_child{index}", "title": f"Rollout worker {index}", "worker_type": "coding",
         "stage": "needs_implementation", "ticket_status": "agent", "manager_id": MANAGER_ID,
         "created_at": index + 1,
         "recap_preview": "This row keeps its place while an artifact is open."}
        for index in range(18)
    ]
    board = {"managers": [{
        "id": MANAGER_ID, "title": MANAGER_TITLE, "created_at": 1, "project_id": "p_fixture",
        "project": "Fixture project", "settled": False,
        "conversation_id": MANAGER_CONVERSATION,
        "recap_preview": "The Manager's own conversation names another Ticket's artifact.",
        "tickets": tickets,
    }]}
    source = fixture["navigation_response"](
        board, {"chief_of_staff": {"label": "Chief", "conversation_id": CHIEF_CONVERSATION}}
    )
    manifest = {"worker_types": [{
        "worker_type": "coding", "label": "Coding",
        "stages": [{"id": "needs_implementation", "label": "Implementation", "gating_field": "implementation", "is_terminal": False, "ownership_mode": "worker"},
                   {"id": "done", "label": "Done", "gating_field": None, "is_terminal": True, "ownership_mode": None}],
        "fields": [{"id": "implementation", "label": "Implementation"}],
        "advance": {"needs_implementation": "done"},
        "ceiling_range": ["none", "needs_implementation", "done"], "default_ceiling": "done",
        "worker_profile_id": "coding", "default_backend": "codex",
        "default_model": None, "default_reasoning_effort": None,
    }]}
    start_values = {"backend_key": "codex", "model": None, "reasoning_effort": None}
    values = {
        "/api/projects?detail=full": {"projects": [{"id": "p_fixture", "name": "Fixture project", "summary": "", "created_at": 1, "updated_at": 1}]},
        "/api/goals": {"goals": [], "status_date": time.strftime("%Y-%m-%d"), "next_refresh_at": int(time.time()) + 86400},
        "/api/workers/chief-of-staff/settings": {"employee_id": "chief", "label": "Chief", "recap": "The Chief's conversation names another Ticket's artifact.", "skill": None, "launch_defaults": {"employee_backend": "codex", "employee_launch_model": None, "employee_launch_reasoning_effort": None}},
        "/api/chief/conversation/start-values": start_values,
        "/api/chief/conversation": {"conversation_id": CHIEF_CONVERSATION},
        "/api/chief/page": {"page": None, "page_revision": 0},
        f"/api/managers/{MANAGER_ID}/page": {"page": None, "page_revision": 0},
        f"/api/managers/{MANAGER_ID}/conversation/start-values": start_values,
        f"/api/managers/{MANAGER_ID}/workspace-content": {"goals": [], "goal": "Ship the rollout.", "recap": "The Manager's own conversation names another Ticket's artifact."},
        "/api/worker-types?supported=true": manifest,
        "/api/worker-types": manifest,
        "/api/feedback/count": {"open_count": 0},
        "/api/deployment-status": {"state": "idle", "deployed_sha": None, "target_sha": None, "outcome": None, "detail": None, "valid_until": None},
        "/api/updates": {"configured": False, "available": None, "request": None, "activity": "unknown", "detail": None, "state": "up_to_date", "allowed_actions": [], "phase": None, "app_sha": None, "deadline_at": None, "restart_deadline_at": None},
        "/api/meta": {"test_mode": True, "app_sha": None},
        "/api/review": {"items": [], "running_worker_count": 0},
        "/api/conversation/backends": {"backends": []},
        # The Manager's own artifact, which its document names in the Artifacts band.
        f"/api/artifact-catalog/manager/{MANAGER_ID}": {
            "revision": "1", "directory": "", "offset": 0, "total": 1,
            "entries": [{"key": "notes", "path": "notes.md", "label": "notes", "kind": "md",
                         "href": f"/files/managers/{MANAGER_ID}/notes.md",
                         "directory": None, "child_count": 0}],
        },
    }
    # The standalone viewer resolves the file owner, not the source Review item.
    values.update({
        "/api/tickets?detail=full&id=t_elsewhere": {"id": "t_elsewhere", "title": "Artifact owner", "conversation_id": FILE_CONVERSATION, "locale": "vps", "worker_type": "coding"},
        "/api/tickets/t_elsewhere/conversation/start-values": start_values,
        f"/api/managers/{MANAGER_ID}/workspace": {"id": MANAGER_ID, "title": MANAGER_TITLE, "manager": {"conversation_id": MANAGER_CONVERSATION}},
        "/api/goals/goal_fixture": {"id": "goal_fixture", "title": "Artifact goal", "holder_kind": "manager", "holder_id": MANAGER_ID},
        "/api/goals/goal_chieffixture": {"id": "goal_chieffixture", "title": "Chief artifact goal", "holder_kind": "chief", "holder_id": None},
    })
    unexpected = []
    writes = []
    review_mode = False
    review_anchors = []
    rebased_review_anchors = []

    def respond(route):
        parts = urlsplit(route.request.url)
        path, query = parts.path, parts.query
        if route.request.method != "GET":
            writes.append((path, route.request.post_data_json))
        if path == "/api/tickets/t_elsewhere/conversation/send":
            route.fulfill(json={"conversation_id": FILE_CONVERSATION, "fate": "started"})
            return
        if path == "/api/tickets/t_missing/conversation/start-values" or path == "/api/tickets" and parse_qs(query).get("id") == ["t_missing"]:
            route.fulfill(status=404, json={"detail": "Ticket not found"})
            return
        if path == "/api/navigation/view/snapshot":
            view = json.loads(parse_qs(query)["view"][0])
            review_anchors.extend(window["anchor"] for window in view["windows"] if window["section"] == "review")
            response = fixture["scoped_navigation_response"](source, view)
            requested = next((window["anchor"] for window in view["windows"] if window["section"] == "review"), None)
            if requested is not None:
                rebased_review_anchors.extend(window["request"]["anchor"] for window in response["content"]["windows"]
                                              if window["request"]["section"] == "review" and window["request"]["anchor"] != requested)
            route.fulfill(json=response)
            return
        if path.startswith("/api/conversation/conversations/"):
            rest = path[len("/api/conversation/conversations/"):].split("/")
            conversation_id, tail = rest[0], "/".join(rest[1:])
            if conversation_id not in TRANSCRIPTS:
                unexpected.append(path)
                route.fulfill(status=503, json={"error": {"code": "fixture", "message": path}})
                return
            arguments = parse_qs(query)
            lens = arguments.get("lens", ["focus"])[0]
            if tail == "events":
                text = f"Review the [artifact]({MANAGER_ARTIFACT})."
                route.fulfill(json={"events": [{"conversation_id": conversation_id, "sequence": 2,
                    "kind": "message_to_owner", "created_at": 2,
                    "payload": {"text": text, "sender_label": "Manager", "sender": {"kind": "manager", "id": MANAGER_ID},
                    "recipient": {"kind": "owner", "id": "owner"}}}]})
                return
            if tail == "view/snapshot":
                # The sibling of the live tail address. The case host's EventSource
                # replacement fetches this and republishes it through the production
                # transport, so the reader under test is the real one.
                route.fulfill(json={
                    "page": conversation_page(conversation_id, lens, arguments.get("transcript", ["true"])[0] == "true"),
                    "coverage": {"through_sequence": len(TRANSCRIPTS[conversation_id])},
                    "status": conversation_status(conversation_id),
                })
                return
            if tail == "view/pages":
                route.fulfill(json=conversation_page(conversation_id, lens))
                return
            if tail == "view/receipts":
                route.fulfill(json={"conversation_id": conversation_id,
                                    "coverage": {"through_sequence": len(TRANSCRIPTS[conversation_id])},
                                    "receipts": []})
                return
            if tail == "composer-catalog":
                route.fulfill(json={"composer_catalog": []})
                return
            if tail == "owner-read":
                route.fulfill(json={"owner_read_through_sequence": json.loads(route.request.post_data)["through_sequence"]})
                return
            if tail == "":
                detail = conversation_detail(conversation_id)
                if review_mode:
                    detail["owner_read_through_sequence"] = 0
                route.fulfill(json=detail)
                return
            unexpected.append(path)
            route.fulfill(status=503, json={"error": {"code": "fixture", "message": path}})
            return
        key = parse_qs(query).get("key", [path])[0] if path == "/api/resource-body" else (
            path + ("?" + query if query else "")
        )
        body = values.get(key, values.get(path))
        if body is None:
            # Refused rather than faked, so a screen this case does not drive cannot
            # quietly depend on invented data.
            unexpected.append(key)
            route.fulfill(status=503, json={"error": {"code": "fixture", "message": key}})
            return
        route.fulfill(json={"key": key, "epoch": "fixture", "revision": "1", "body": body}
                      if path == "/api/resource-body" else body)

    context = browser.new_context(viewport={"width": 1400, "height": 820}, reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", respond)

    screenshots = os.environ.get("PANELS_ARTIFACT_SCREENSHOT_DIR")
    if screenshots:
        Path(screenshots).mkdir(parents=True, exist_ok=True)

    artifact = page.locator("[data-ticket-artifact]")

    def artifact_link(scope, owning_ticket):
        """The link the conversation drew for the artifact it named.

        A managed Ticket file named in Markdown is drawn by the shared preview, which
        offers it as an explicit `#/preview` address. That is the link the owner pressed
        and the one that used to leave the page. The preview also carries a name link for
        narrow screens, which this width does not draw, so only a drawn one is taken.
        """
        return scope.locator(f'[data-file-preview] a[href*="ticket={owning_ticket}"]:visible').first

    # ---------------------------------------------------------- Chief's pane first
    #
    # The Chief's pane already kept its conversation before this change, so these
    # are preservation assertions and they run first on purpose: a run against
    # unchanged product passes them and then stops at the Manager's conversation,
    # which is the assertion the defect owns.
    # -------------------------------------------------------------------- Chief's pane
    page.goto(url + "#/workspace/chief-of-staff", wait_until="domcontentloaded")
    chief = page.locator(".chief-workspace.manager-page")
    chief_conversation = chief.locator("[data-conversation-pane]")
    chief_transcript = chief_conversation.get_by_text(CHIEF_TRANSCRIPT, exact=False)
    chief_artifact = chief.locator("[data-ticket-artifact]")
    expect(chief_conversation).to_have_attribute("data-conversation-id", CHIEF_CONVERSATION)
    chief_conversation.locator("[data-conversation-expand]").click()
    expect(chief_transcript).to_be_visible()

    # 4b. The Chief's pane keeps its artifact in the Workspace address, so pressing the
    #     link writes it there and a reload still shows it.
    artifact_link(chief_conversation, "t_faraway").click()
    expect(chief_artifact).to_have_attribute("aria-label", "budget.md")
    expect(chief_artifact.get_by_role("heading", name="The budget note")).to_be_visible()
    expect(chief_conversation).to_have_attribute("data-conversation-id", CHIEF_CONVERSATION)
    rode_along = page.evaluate("location.hash")
    assert rode_along == "#/workspace/chief-of-staff?source=ticket&ticket=t_faraway&path=artifacts%2Fbudget.md", rode_along

    if screenshots:
        page.screenshot(path=str(Path(screenshots) / "chief-conversation-artifact.png"))

    # The Chief's conversation was kept too: the same words are still there, over the
    # still-open artifact.
    chief_conversation.locator("[data-conversation-expand]").click()
    expect(chief_transcript).to_be_visible()
    expect(chief_artifact).to_have_count(1)

    page.reload(wait_until="domcontentloaded")
    expect(chief_artifact).to_have_attribute("aria-label", "budget.md")
    expect(chief_artifact.get_by_role("heading", name="The budget note")).to_be_visible()
    assert page.evaluate("location.hash") == rode_along


    # ------------------------------------------------------------------ Manager's pane
    page.goto(url + f"#/workspace/manager/{MANAGER_ID}", wait_until="domcontentloaded")
    pane = page.locator(f'[data-manager-workspace="{MANAGER_ID}"]')
    conversation = pane.locator("[data-conversation-pane]")
    document_area = pane.locator("main.manager-doc")
    title = document_area.get_by_text(MANAGER_TITLE, exact=True)
    transcript = conversation.get_by_text(MANAGER_TRANSCRIPT, exact=False)
    try:
        expect(conversation).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)
    except AssertionError:
        print("ARTIFACT_ERRORS " + repr(errors) + " UNEXPECTED " + repr(unexpected), flush=True)
        raise
    expect(title).to_be_visible()

    def open_the_conversation():
        """The reader's own control. The transcript is only on the screen while the
        conversation is open, which is where a link to an artifact is read and pressed."""
        conversation.locator("[data-conversation-expand]").click()
        expect(conversation).to_have_attribute("data-conversation-state", "opened")
        expect(transcript).to_be_visible()

    def dock_the_conversation():
        conversation.locator("[data-conversation-collapse]").click()
        expect(conversation).to_have_attribute("data-conversation-state", "rest")

    open_the_conversation()
    link = artifact_link(conversation, "t_elsewhere")
    href = link.get_attribute("href")
    assert href.startswith("#/preview?"), href

    # The reading position the owner is at. The Manager's document fills its loading
    # region, so the scroll belongs to the body inside the document area rather than to
    # the document area itself; this is whichever of the two actually owns it.
    reading = page.evaluate("""() => {
      const pane = document.querySelector('[data-manager-workspace] main.manager-doc');
      for (const node of [pane, pane.querySelector(':scope > .manager-column > .skeleton-region > .skeleton-content')]) {
        if (node && node.scrollHeight > node.clientHeight) { window.__reading = node; return true; }
      }
      return false;
    }""")
    assert reading, "the Manager's document is not taller than its pane, so its position proves nothing"
    page.evaluate("window.__reading.scrollTop = 160")
    scrolled_to = page.evaluate("window.__reading.scrollTop")
    assert scrolled_to > 0, "the Manager's document did not scroll, so its position proves nothing"

    # Pressed from the keyboard. Enter on a link raises the same press a click does, and
    # the pane claims it with no key handling of its own.
    link.focus()
    assert page.evaluate("document.activeElement.getAttribute('href')") == href
    opened_at, hash_at = page.url, page.evaluate("location.hash")
    page.keyboard.press("Enter")

    # 1. The artifact draws in place and the page is not left. Before this, the browser
    #    followed the link to #/preview and the conversation went with it.
    expect(artifact).to_have_attribute("aria-label", "plan.md")
    expect(artifact.get_by_role("heading", name="The rollout plan")).to_be_visible()
    assert page.url == opened_at, f"opening the artifact left the page: {page.url}"
    # 4a. A Manager's pane keeps its own artifact, so the address is untouched.
    assert page.evaluate("location.hash") == hash_at, page.evaluate("location.hash")
    # The conversation underneath is the same conversation. A full one covers the pane, so
    # it steps back to its dock to let the artifact through, which takes the transcript off
    # the screen - so what proves the conversation was kept is opening it again and finding
    # the same words, over the still-open artifact.
    expect(conversation).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)
    expect(conversation).to_have_attribute("data-conversation-state", "rest")

    if screenshots:
        page.screenshot(path=str(Path(screenshots) / "manager-conversation-artifact.png"))

    open_the_conversation()
    expect(artifact).to_have_attribute("aria-label", "plan.md")
    dock_the_conversation()

    # 6 and 3. Close returns the same page: the same document at the position the reader
    #    left it, with the same conversation still under it and still readable.
    artifact.get_by_role("button", name="Close", exact=True).click()
    expect(artifact).to_have_count(0)
    expect(title).to_be_visible()
    expect(conversation).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)
    assert abs(page.evaluate("window.__reading.scrollTop") - scrolled_to) <= 1, (
        page.evaluate("window.__reading.scrollTop"), scrolled_to)
    open_the_conversation()
    expect(transcript).to_be_visible()

    # 2. The conversation it was named in stays usable: words typed while the artifact is
    #    open land in the real composer, and a draft survives opening and closing it.
    box = conversation.locator("[data-conversation-input]")
    box.click()
    box.type("Half a thought")
    link.click()
    expect(artifact).to_have_count(1)
    box.click()
    box.type(" and the rest of it.")
    assert box.input_value() == "Half a thought and the rest of it.", box.input_value()
    artifact.get_by_role("button", name="Close", exact=True).click()
    expect(artifact).to_have_count(0)
    assert box.input_value() == "Half a thought and the rest of it.", box.input_value()
    box.fill("")

    # 5a. The owner's own path, from the keyboard, end to end. The link is read and
    #     pressed inside the full conversation, which makes the reading layer inert while
    #     it covers it. The artifact is drawn into that inert layer, and the dock only
    #     clears inert on its next measurement, so the hand-off has to wait for the layer
    #     to let go rather than take the keyboard once and lose it.
    open_the_conversation()
    link.focus()
    page.keyboard.press("Enter")
    expect(artifact).to_have_attribute("aria-label", "plan.md")
    expect(conversation).to_have_attribute("data-conversation-state", "rest")
    page.wait_for_function(
        "() => document.activeElement?.hasAttribute('data-ticket-artifact')"
    )

    #     Escape hands the keyboard back to something the reader can see and use. The link
    #     it came from is in the transcript, which leaves the screen when the conversation
    #     returns to its bar, so the bar itself takes it: still the conversation the
    #     artifact was opened from, and one press from the words it was named in.
    rest_bar = conversation.locator("[data-conversation-rest-bar]")
    page.keyboard.press("Escape")
    expect(artifact).to_have_count(0)
    expect(rest_bar).to_be_focused()
    expect(conversation).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)
    open_the_conversation()
    expect(transcript).to_be_visible()
    dock_the_conversation()

    #     Close does the same as Escape.
    open_the_conversation()
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.activeElement?.hasAttribute('data-ticket-artifact')"
    )
    artifact.get_by_role("button", name="Close", exact=True).click()
    expect(artifact).to_have_count(0)
    expect(rest_bar).to_be_focused()

    # 5. Escape belongs to whatever is on top, and focus is lent and given back.
    #
    #    Pressed from the Manager's own Artifacts band this time. The link is in the
    #    document rather than the conversation, so it is still on the screen when the
    #    artifact closes and the keyboard goes back to the link itself. 5a above is the
    #    other half: a link that leaves the screen, where the conversation's bar takes it.
    document_area.locator("[data-artifacts-band] > summary").click()
    chip = document_area.locator('[data-artifact-chip="notes.md"]')
    expect(chip).to_be_visible()
    chip.focus()
    chip.press("Enter")
    expect(artifact).to_have_attribute("aria-label", "notes.md")
    # The artifact takes the keyboard, which is also what lets Escape reach this pane.
    assert page.evaluate("document.activeElement.hasAttribute('data-ticket-artifact')")
    open_the_conversation()
    page.keyboard.press("Escape")
    expect(conversation).to_have_attribute("data-conversation-state", "rest")
    expect(artifact).to_have_count(1)
    page.keyboard.press("Escape")
    expect(artifact).to_have_count(0)
    # Focus went back to the link that had it before the artifact opened.
    page.wait_for_function("() => document.activeElement?.dataset.artifactChip === 'notes.md'")
    expect(conversation).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)


    # Standalone links choose the file owner, even though this file was named by a Manager.
    writes.clear()
    page.goto(url + "#/preview?source=ticket&ticket=t_elsewhere&path=artifacts%2Fplan.md", wait_until="domcontentloaded")
    viewer = page.locator("[data-file-preview-route]")
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-id", FILE_CONVERSATION)
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-state", "rest")
    expect(viewer).to_contain_text("Artifact owner")
    expect(viewer.get_by_role("heading", name="The rollout plan")).to_be_visible()
    assert not writes, writes

    viewer.locator('[data-ticket-artifact-close]').click()
    page.wait_for_function("() => location.hash === '#/workspace'")
    assert not writes, writes

    page.goto(url + f"#/preview?source=manager&manager={MANAGER_ID}&path=notes.md", wait_until="domcontentloaded")
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)
    expect(viewer).to_contain_text(MANAGER_TITLE)
    assert not writes, writes

    page.goto(url + "#/preview?source=goal&goal=goal_fixture&path=notes.md", wait_until="domcontentloaded")
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-id", MANAGER_CONVERSATION)
    expect(viewer).to_contain_text(MANAGER_TITLE)
    assert not writes, writes

    # A cold direct goal-file visit reads its Chief holder's native pointer.
    page.goto("about:blank")
    page.goto(url + "#/preview?source=goal&goal=goal_chieffixture&path=notes.md", wait_until="domcontentloaded")
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-id", CHIEF_CONVERSATION)
    assert not writes, writes

    page.goto(url + "#/preview?source=ticket&ticket=t_missing&path=artifacts%2Fplan.md", wait_until="domcontentloaded")
    expect(viewer.locator("[data-conversation-input]")).to_have_count(0)
    expect(viewer).to_contain_text("unavailable")
    assert not writes, writes

    # An owner without a conversation is available: its native send creates one.
    values["/api/tickets?detail=full&id=t_elsewhere"]["conversation_id"] = None
    page.goto(url + "#/preview?source=ticket&ticket=t_elsewhere&path=artifacts%2Fplan.md", wait_until="domcontentloaded")
    expect(viewer.locator("[data-conversation-input]")).to_be_visible()
    viewer.locator("[data-conversation-input]").fill("Start with this artifact")
    with page.expect_request(lambda request: request.url.endswith("/api/tickets/t_elsewhere/conversation/send")):
        viewer.locator("[data-conversation-send]").click()
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-id", FILE_CONVERSATION)
    assert len(writes) == 1 and writes[0][1]["conversation_id"] is None, writes
    values["/api/tickets?detail=full&id=t_elsewhere"]["conversation_id"] = FILE_CONVERSATION
    writes.clear()

    # Review saves its skipped cohort and current draft across actual route changes.
    # The real shell owns selection and native history, which a card-only host cannot prove.
    review_mode = True
    review_board = json.loads(json.dumps(board))
    review_board["managers"][0]["awaiting_reply"] = True
    source = fixture["navigation_response"](review_board, {"chief_of_staff": {
        "label": "Chief", "conversation_id": CHIEF_CONVERSATION, "awaiting_reply": True}})
    page.goto(url + "#/review", wait_until="domcontentloaded")
    card = page.locator('[data-review-card][data-review-item-type="message"]:visible')
    # Whichever conversation is oldest, defer it and keep the other one current.
    expect(card).to_have_count(1)
    first_conversation = card.get_attribute("data-conversation-id")
    page.locator('[data-skip]:visible').first.click()
    expect(card).to_have_count(1)
    current_conversation = card.get_attribute("data-conversation-id")
    assert first_conversation != current_conversation
    reply = card.locator("[data-review-reply]")
    reply.fill("Keep this unfinished response")
    writes.clear()
    artifact_link(card, "t_elsewhere").click()
    expect(viewer).to_be_visible()
    expect(viewer.locator("[data-conversation-pane]")).to_have_attribute("data-conversation-id", FILE_CONVERSATION)
    expect(viewer).to_contain_text("Artifact owner")
    page.keyboard.press("s")
    page.keyboard.press("o")
    page.keyboard.press("Control+Enter")
    preview_hash = "#/preview?source=ticket&ticket=t_elsewhere&path=artifacts%2Fplan.md"
    assert page.evaluate("location.hash") == preview_hash
    expect(page.locator('[data-screen="review"][aria-current="page"]')).to_have_count(0)
    expect(page.locator('[data-review-card]')).to_have_count(0)
    assert not writes, writes
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(viewer).to_have_count(0)
    expect(reply).to_have_value("Keep this unfinished response")
    page.wait_for_function("() => location.hash === '#/review'")
    expect(page.locator('[data-screen="review"][aria-current="page"]:visible')).to_have_count(1)
    assert card.get_attribute("data-conversation-id") == current_conversation
    assert not writes, writes

    # Every way back restores the same unfinished reply and does not undo the skip.
    for return_way in ("review", "back", "escape"):
        artifact_link(card, "t_elsewhere").click()
        expect(viewer).to_be_visible()
        if return_way == "review":
            page.locator('[data-screen="review"]:visible').first.click()
        elif return_way == "back":
            page.go_back()
        else:
            page.keyboard.press("Escape")
        expect(viewer).to_have_count(0)
        expect(reply).to_have_value("Keep this unfinished response")
        assert card.get_attribute("data-conversation-id") == current_conversation
        assert not writes, writes

    artifact_link(card, "t_elsewhere").click()
    nested_conversation = viewer.locator("[data-conversation-pane]")
    nested_conversation.locator("[data-conversation-expand]").click()
    artifact_link(nested_conversation, "t_faraway").click()
    expect(viewer.locator("[data-ticket-artifact]")).to_have_attribute("aria-label", "budget.md")
    expect(nested_conversation).to_have_attribute("data-conversation-id", FILE_CONVERSATION)
    assert page.evaluate("location.hash") == preview_hash
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(reply).to_have_value("Keep this unfinished response")
    writes.clear()
    artifact_link(card, "t_elsewhere").click()
    expect(viewer.locator("[data-conversation-input]")).to_be_visible()
    viewer.locator("[data-conversation-input]").fill("This goes to the artifact owner")
    with page.expect_request(lambda request: request.url.endswith("/api/tickets/t_elsewhere/conversation/send")):
        viewer.locator("[data-conversation-send]").click()
    page.wait_for_function("() => !document.querySelector('[data-conversation-sending]')")
    assert len(writes) == 1, writes
    assert writes[0][0] == "/api/tickets/t_elsewhere/conversation/send", writes
    assert writes[0][1]["conversation_id"] == FILE_CONVERSATION, writes
    assert writes[0][1]["content"] == [{"piece": "text", "text": "This goes to the artifact owner"}], writes
    if screenshots:
        page.screenshot(path=str(Path(screenshots) / "review-artifact-owner.png"))
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(reply).to_have_value("Keep this unfinished response")
    if screenshots:
        page.screenshot(path=str(Path(screenshots) / "review-return-restored.png"))

    # Resolution elsewhere during a visit must win over the saved queue position.
    writes.clear()
    artifact_link(card, "t_elsewhere").click()
    expect(viewer).to_be_visible()
    source = fixture["navigation_response"](board, {"chief_of_staff": {
        "label": "Chief", "conversation_id": CHIEF_CONVERSATION}})
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(page.locator('[data-review-card]')).to_have_count(0)
    expect(page.locator('[data-review-empty]')).to_be_visible()
    assert not writes, writes

    # Proposal revision text has a different owner from a conversation reply. Reuse
    # this shell to prove that draft survives unmount, without a second browser host.
    proposal_board = json.loads(json.dumps(board))
    proposal_rows = [{
        "id": f"t_proposal{index:02}", "title": f"Proposal {index:02}",
        "worker_type": "coding", "stage": "needs_implementation", "ticket_status": "agent",
        "manager_id": MANAGER_ID, "created_at": index + 1, "awaiting_approval": True,
        "recap": f"Read the [artifact]({MANAGER_ARTIFACT}).",
    } for index in range(31)]
    proposal_board["managers"][0]["tickets"] = proposal_rows
    # Each Ticket carries its own saved workflow, which the review card reads.
    for item in proposal_rows:
        values[f"/api/tickets/{item['id']}/workspace"] = {
            "id": item["id"], "title": item["title"], "worker_type": "coding",
            "stage": "needs_implementation", "ceiling": "done",
            "ceiling_holder": {"kind": "owner", "id": "owner"},
            "ticket_status": "awaiting_approval", "conversation_id": MANAGER_CONVERSATION,
            "manager_id": MANAGER_ID, "manager_title": MANAGER_TITLE,
            "recap": "", "field_summaries": {},
            "pending_proposal": {"field": "implementation", "body": "A reviewed result", "revision": 1, "created_at": 1},
            "workflow": manifest["worker_types"][0],
        }
    source = fixture["navigation_response"](proposal_board, {"chief_of_staff": {
        "label": "Chief", "conversation_id": CHIEF_CONVERSATION}})
    page.goto(url + "#/review", wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    proposal = page.locator('[data-review-item-type="proposal"]')
    revision = proposal.locator('[data-review-revision-input]')
    proposal.locator('[data-send-back]').click()
    revision.fill("Keep this proposed revision")
    body_editor = proposal.locator('[data-edit]')
    body_editor.click()
    body_editor.fill("Keep this edited proposal body")
    body_editor.press("Tab")
    writes.clear()
    artifact_link(page.locator('[data-review-ticket-card]'), "t_elsewhere").click()
    expect(viewer).to_be_visible()
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(revision).to_have_value("Keep this proposed revision")
    expect(proposal.locator('[data-edit]')).to_have_text("Keep this edited proposal body")
    assert not writes, writes

    # A saved continuation can outlive the page it names. Exercise the real backend
    # projection's rebase rather than inventing a view payload or another host.
    for _ in range(29):
        previous = proposal.get_attribute("data-ticket-id")
        page.locator('[data-skip]:visible').first.click()
        page.wait_for_function("previous => document.querySelector('[data-review-item-type=proposal]')?.dataset.ticketId !== previous", arg=previous)
        expect(proposal).to_have_count(1)
    # An older ask can push the saved current card beyond the first bounded page.
    saved_id = proposal.get_attribute("data-ticket-id")
    artifact_link(page.locator('[data-review-ticket-card]'), "t_elsewhere").click()
    expect(viewer).to_be_visible()
    earlier = {**proposal_rows[0], "id": "t_earlier", "title": "Earlier ask"}
    proposal_board["managers"][0]["tickets"] = [earlier, *proposal_rows]
    source = fixture["navigation_response"](proposal_board, {"chief_of_staff": {
        "label": "Chief", "conversation_id": CHIEF_CONVERSATION}})
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(proposal).to_have_attribute("data-ticket-id", saved_id)
    page.locator('[data-skip]:visible').first.click()
    expect(proposal).not_to_have_attribute("data-ticket-id", saved_id)
    last_id = proposal.get_attribute("data-ticket-id")
    assert any(anchor is not None for anchor in review_anchors), review_anchors
    artifact_link(page.locator('[data-review-ticket-card]'), "t_elsewhere").click()
    expect(viewer).to_be_visible()
    proposal_board["managers"][0]["tickets"] = [item for item in proposal_rows if item["id"] not in {saved_id, last_id}]
    source = fixture["navigation_response"](proposal_board, {"chief_of_staff": {
        "label": "Chief", "conversation_id": CHIEF_CONVERSATION}})
    viewer.locator('[data-ticket-artifact-close]').click()
    expect(page.locator('[data-review-empty]')).to_be_visible()
    expect(page.locator('[data-review-card]')).to_have_count(0)
    assert None in rebased_review_anchors, rebased_review_anchors
    assert not writes, writes

    assert not unexpected, unexpected
    assert not errors, errors
    context.close()
