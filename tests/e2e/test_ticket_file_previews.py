"""Browser coverage for managed ticket-file previews."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Page,
    expect,
)
from tests.e2e.harness import REPO_ROOT, JsonObject, ServerHandle
from tests.support.connected_rows import _bind, _message, _post, _propose
from tests.support.server_fixtures import rail_server as rail_server

from planner.core.contracts import CHIEF_PRINCIPAL

WAIT_MS = 10_000


def _prove_dock_artifact_retention(page: Page, label: str, evidence: str | None) -> None:
    """Production hosts must retain sandbox state while conversation covers it."""
    artifact = page.locator("[data-ticket-artifact]")
    iframe = artifact.locator("iframe[data-file-preview-html]")
    frame = artifact.frame_locator("iframe[data-file-preview-html]")
    pane = page.locator("[data-conversation-pane]")
    draft = pane.locator("[data-conversation-input]")
    settled_dock = page.locator(
        '[data-conversation-dock-state="rest"]:not([data-conversation-dock-transition])'
    ).filter(has=pane)
    if pane.get_attribute("data-conversation-state") == "opened":
        page.locator("[data-conversation-collapse]").click(timeout=WAIT_MS)
    expect(pane).to_have_attribute("data-conversation-state", "rest", timeout=WAIT_MS)
    # The state changes before the dock motion releases the retained reader height.
    expect(settled_dock).to_have_count(1, timeout=WAIT_MS)
    draft.fill("Keep this draft through full conversation")
    expect(pane).to_have_attribute("data-conversation-state", "rest")
    frame.locator("[data-retained-form]").fill("Keep this artifact value")
    frame.locator("[data-bottom-control]").click(timeout=WAIT_MS)
    expect(frame.locator("[data-bottom-control]")).to_have_text("Bottom control works")
    frame.locator("[data-retained-selection]").evaluate(
        "node => { const range = document.createRange(); range.selectNodeContents(node); "
        "const selection = getSelection(); selection.removeAllRanges(); "
        "selection.addRange(range); }"
    )
    before = frame.locator("body").evaluate(
        "node => ({scroll: document.scrollingElement.scrollTop, "
        "selection: getSelection().toString()})"
    )
    assert before["scroll"] > 0
    iframe.evaluate("node => window.__retainedArtifactFrame = node")
    source = iframe.get_attribute("src")
    reader_bounds = artifact.bounding_box()
    assert reader_bounds is not None
    reader_height = reader_bounds["height"]
    bounds = page.locator(".conversation-layer").bounding_box()
    assert bounds is not None
    assert reader_bounds["y"] + reader_height <= bounds["y"] + 1
    if evidence:
        page.screenshot(path=str(Path(evidence) / f"{label}-dock.png"), full_page=True)
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    expect(pane).to_have_attribute("data-conversation-state", "opened", timeout=WAIT_MS)
    if evidence:
        page.screenshot(path=str(Path(evidence) / f"{label}-full.png"), full_page=True)
    page.locator("[data-conversation-collapse]").click(timeout=WAIT_MS)
    expect(pane).to_have_attribute("data-conversation-state", "rest", timeout=WAIT_MS)
    expect(settled_dock).to_have_count(1, timeout=WAIT_MS)
    expect(draft).to_have_value("Keep this draft through full conversation")
    assert iframe.evaluate("node => node === window.__retainedArtifactFrame")
    assert iframe.get_attribute("src") == source
    expect(frame.locator("[data-retained-form]")).to_have_value("Keep this artifact value")
    expect(frame.locator("[data-bottom-control]")).to_have_text("Bottom control works")
    after = frame.locator("body").evaluate(
        "node => ({scroll: document.scrollingElement.scrollTop, "
        "selection: getSelection().toString()})"
    )
    assert after == before
    returned_bounds = artifact.bounding_box()
    assert returned_bounds is not None
    assert returned_bounds["height"] == reader_height
    draft.fill("")


def _ticket_files_dir(server: ServerHandle, ticket_id: str) -> Path:
    return server.db_path.parent / "files" / "tickets" / ticket_id


def _write_ticket_files(server: ServerHandle, ticket_id: str) -> None:
    root = _ticket_files_dir(server, ticket_id)
    (root / "notes").mkdir(parents=True)
    (root / "images").mkdir(parents=True)
    (root / "video").mkdir(parents=True)
    (root / "audio").mkdir(parents=True)
    (root / "notes" / "space name.md").write_text(
        "# File Notes\n\nRendered **here**.\n\n"
        f"[Nested](/files/tickets/{ticket_id}/notes/other.md)\n\n"
        f"[Self](/files/tickets/{ticket_id}/notes/space%20name.md)",
        encoding="utf-8",
    )
    (root / "notes" / "other.md").write_text(
        "# Other Notes\n\nFresh route content.", encoding="utf-8"
    )
    (root / "notes" / "slow.md").write_text("# Slow preview", encoding="utf-8")
    (root / "page.html").write_text(
        "<h1>HTML File</h1><script>window.parent.__ticketFileScriptRan = true;</script>",
        encoding="utf-8",
    )
    (root / "images" / "pic.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAW0lEQVR4nO3PQQ0AIBDAsAP/"
            "nuGNAvZoFSzZOjNnyNi1dwfgUQCeBOBJAI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJ"
            "AI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJAN4A2icCftL8UqQAAAAASUVORK5CYII="
        )
    )
    (root / "video" / "demo.mp4").write_bytes(b"not a real mp4")
    (root / "audio" / "demo.mp3").write_bytes(b"not a real mp3")
    (root / "archive.bin").write_bytes(b"download me")


def _settle_what_changes(server: ServerHandle, ticket_id: str, body: str) -> None:
    """Seed saved Markdown through the same proposal/accept writers as ordinary work."""
    _propose(server, ticket_id, body)
    ticket = _post(server, f"/api/tickets/{ticket_id}/accept/what_changes", {
        "next_ceiling": "none", "next_holder": {"kind": "owner", "id": "owner"},
    })
    assert ticket["stage"] == "needs_implementation"
    assert ticket["field_values"]["what_changes"] == body
    assert ticket["pending_proposal"] is None


def _open_ticket_field(page: Page, field: str) -> None:
    page.locator("details[data-stage-fold]").evaluate("node => { node.open = true; }")
    section = page.locator(f'details[data-field="{field}"]').first
    section.evaluate(
        """(node) => {
          const settledStages = node.closest('details[data-stage-fold]');
          if (settledStages) settledStages.open = true;
          node.open = true;
        }"""
    )
    page.locator(f'details[data-field="{field}"][open]').wait_for(state="attached", timeout=WAIT_MS)
    section.scroll_into_view_if_needed(timeout=WAIT_MS)


def test_html_artifact_interacts_loads_sibling_assets_and_refreshes_in_place(
    rail_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """The real sandbox resolves managed assets and refreshes over its Ticket.

    Mock DOM preparation and API bytes cannot prove browser sandbox execution,
    sibling URL resolution, child navigation, or changed content in the open frame.
    The same browser and server exercise both hosts against the shipped bundle.
    """
    server = rail_server
    started_at = time.monotonic()
    ticket_id = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "HTML artifact"
    )["id"]
    _write_ticket_files(server, ticket_id)
    root = _ticket_files_dir(server, ticket_id)
    preview_directory = root / "previews"
    preview_directory.mkdir()
    (preview_directory / "style.css").write_text(
        "h1 { color: rgb(13, 71, 161); }", encoding="utf-8"
    )
    nested = preview_directory / "nested"
    nested.mkdir()
    child = nested / "child.html"
    child.write_text(
        '<h1>Child artifact</h1><a href="#detail">Jump to detail</a>'
        '<a href="../../page.html">Outside directory</a>'
        '<a href="../escape.html">Symlink escape</a>'
        '<div style="height: 1400px"></div><h2 id="detail">Child detail</h2>'
        '<script>try { window.parent.__artifactCompromised = true; '
        'document.body.dataset.parentAccess = "allowed"; } '
        'catch { document.body.dataset.parentAccess = "denied"; }</script>',
        encoding="utf-8",
    )
    outside = root / "outside.html"
    outside.write_text("<h1>Secret symlink bytes</h1>", encoding="utf-8")
    (preview_directory / "escape.html").symlink_to(outside)
    document = preview_directory / "index.html"
    document.write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head>'
        '<body><h1>Initial artifact</h1><img src="../images/pic.png" alt="Sibling image">'
        "<button onclick=\"this.textContent='Clicked'\">Try interaction</button>"
        '<a href="nested/child.html">Open child</a>'
        '<a data-artifact-action="empty" href="" onclick="localAction(event)">Empty action</a>'
        '<a data-artifact-action="hash" href="#" onclick="localAction(event)">Hash action</a>'
        '<a data-artifact-action="none" onclick="localAction(event)">No href action</a>'
        '<output data-artifact-action-count>0</output>'
        '<script>let actionCount = 0; function localAction(event) { event.preventDefault(); '
        'document.querySelector("[data-artifact-action-count]").textContent = ++actionCount; }'
        '</script><input data-retained-form aria-label="Artifact form">'
        '<div style="height: 1600px"></div>'
        '<p data-retained-selection>Retain this selection</p>'
        '<button data-bottom-control onclick="this.textContent=\'Bottom control works\'">'
        'Bottom control</button>'
        '</body></html>',
        encoding="utf-8",
    )
    retained_document = document.read_text(encoding="utf-8")
    _settle_what_changes(
        server,
        ticket_id,
        f"[HTML](/files/tickets/{ticket_id}/previews/index.html)",
    )
    ticket_selector = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    context = context_factory()
    page = open_page(context, server, f"#/workspace/{ticket_id}", ticket_selector)
    # Prove this browser executed the module named by the committed production index.
    dist = REPO_ROOT / "web" / "dist"
    module = re.search(
        r'<script\s+type="module"[^>]*\ssrc="([^"]+)"',
        (dist / "index.html").read_text(encoding="utf-8"),
    )
    assert module is not None
    module_src = module.group(1)
    assert page.locator('script[type="module"]').get_attribute("src") == module_src
    module_url = server.base + module_src
    assert page.evaluate(
        "url => performance.getEntriesByType('resource').some(entry => entry.name === url)",
        module_url,
    )
    module_response = context.request.get(module_url)
    assert module_response.status == 200
    module_bytes = module_response.body()
    assert b"Message the agent" in module_bytes
    for retired_literal in (b"Steer the work", b"Everything", b"Newsreader"):
        assert retired_literal not in module_bytes
    served_sha256 = hashlib.sha256(module_bytes).hexdigest()
    assert served_sha256 == hashlib.sha256(
        (dist / module_src.removeprefix("/_app/")).read_bytes()
    ).hexdigest()
    evidence = os.environ.get("PANELS_TICKET_EVIDENCE_DIR")
    if evidence:
        Path(evidence).mkdir(parents=True, exist_ok=True)
        (Path(evidence) / "production-browser-asset.json").write_text(
            json.dumps({"module_url": module_url, "sha256": served_sha256}, indent=2) + "\n",
            encoding="utf-8",
        )
    # Check the combined staging UI through rendered controls, never source strings.
    for retired_text in ("Steer the work", "Everything", "Newsreader"):
        expect(page.get_by_text(retired_text, exact=True)).to_have_count(0)
    _open_ticket_field(page, "what_changes")
    page.evaluate("window.__artifactInPlaceMarker = 'kept'")
    html_requests: list[str] = []
    page.on(
        "request",
        lambda request: html_requests.append(request.url)
        if f"/files/tickets/{ticket_id}/" in request.url and request.url.endswith(".html")
        else None,
    )
    page.locator("a.pv-name:visible").filter(has_text="index.html").first.click()
    artifact_address = (
        f"{server.base}/#/workspace/{ticket_id}"
        f"?source=ticket&ticket={ticket_id}&path=previews%2Findex.html"
    )
    page.wait_for_url(artifact_address, timeout=WAIT_MS)
    artifact = page.locator("[data-ticket-artifact]")
    iframe = artifact.locator("iframe[data-file-preview-html]")
    frame = artifact.frame_locator("iframe[data-file-preview-html]")
    frame.get_by_role("heading", name="Initial artifact").wait_for(timeout=WAIT_MS)
    assert iframe.get_attribute("sandbox") == "allow-scripts"
    assert (
        frame.locator("h1").evaluate("node => getComputedStyle(node).color") == "rgb(13, 71, 161)"
    )
    assert (
        frame.get_by_alt_text("Sibling image").evaluate(
            "image => new Promise(resolve => {"
            "const done = () => resolve(image.naturalWidth);"
            "if (image.complete) done();"
            "else { image.addEventListener('load', done, {once: true});"
            "image.addEventListener('error', done, {once: true}); } })"
        )
        == 64
    )
    frame.get_by_role("button", name="Try interaction").click(timeout=WAIT_MS)
    frame.get_by_role("button", name="Clicked").wait_for(timeout=WAIT_MS)

    # The browser, production route, served file and sandbox must agree on identity.
    # A fixture-only DOM check cannot prove this connected retention boundary.
    for width, height, layout in ((1440, 1000, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        _prove_dock_artifact_retention(page, f"ticket-{layout}", evidence)
    page.set_viewport_size({"width": 1440, "height": 1000})

    # Authored action anchors keep their handlers and never enter viewer navigation.
    original_frame_src = iframe.get_attribute("src")
    requests_before_actions = list(html_requests)
    for action_count, action in enumerate(("empty", "hash", "none"), start=1):
        frame.locator(f'[data-artifact-action="{action}"]').click(timeout=WAIT_MS)
        expect(frame.locator("[data-artifact-action-count]")).to_have_text(str(action_count))
        assert iframe.get_attribute("src") == original_frame_src
        assert artifact.locator("[data-html-back]").count() == 0
        assert artifact.locator("[data-html-navigation-notice]").count() == 0
        assert page.url == artifact_address
    assert html_requests == requests_before_actions

    # These headers still force raw HTML to download, including navigable children.
    child_response = context.request.get(
        f"{server.base}/files/tickets/{ticket_id}/previews/nested/child.html"
    )
    assert child_response.status == 200
    assert child_response.headers["content-disposition"].startswith("attachment;")
    assert child_response.headers["x-content-type-options"] == "nosniff"

    frame.get_by_role("link", name="Open child").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Child artifact", exact=True).wait_for(timeout=WAIT_MS)
    expect(frame.locator("body")).to_have_attribute("data-parent-access", "denied", timeout=WAIT_MS)
    assert page.evaluate("window.__artifactCompromised") is None
    assert iframe.get_attribute("sandbox") == "allow-scripts"
    assert page.url == artifact_address
    if evidence:
        page.screenshot(path=str(Path(evidence) / "artifact-child.png"), full_page=True)

    frame.get_by_role("link", name="Jump to detail").click(timeout=WAIT_MS)
    expect(frame.locator("#detail")).to_be_in_viewport(timeout=WAIT_MS)
    artifact.locator("[data-html-back]").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Child artifact", exact=True).wait_for(timeout=WAIT_MS)
    artifact.locator("[data-html-back]").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Initial artifact").wait_for(timeout=WAIT_MS)
    frame.get_by_role("link", name="Open child").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Child artifact", exact=True).wait_for(timeout=WAIT_MS)

    successful_child_src = iframe.get_attribute("src")
    for link, expected_notice in (
        ("Outside directory", "This link cannot be opened in the artifact viewer."),
        ("Symlink escape", "file fetch failed: 404"),
    ):
        requests_before_rejection = list(html_requests)
        frame.get_by_role("link", name=link).click(timeout=WAIT_MS)
        notice = artifact.locator("[data-html-navigation-notice]")
        expect(notice).to_have_text(expected_notice, timeout=WAIT_MS)
        if link == "Outside directory":
            assert html_requests == requests_before_rejection
        else:
            assert html_requests == requests_before_rejection + [
                f"{server.base}/files/tickets/{ticket_id}/previews/escape.html"
            ]
        assert iframe.get_attribute("src") == successful_child_src
        assert artifact.locator("[data-visible-content-failed]").count() == 0
        assert frame.locator("[data-html-navigation-notice]").count() == 0
        assert frame.get_by_role("heading", name="Child artifact", exact=True).is_visible()
        assert frame.get_by_role("heading", name="Secret symlink bytes").count() == 0
        assert page.url == artifact_address
    if evidence:
        page.screenshot(path=str(Path(evidence) / "artifact-parent-rejection.png"), full_page=True)

    confined_headers = {
        "X-Panels-Artifact-Root": f"/files/tickets/{ticket_id}/previews/index.html"
    }
    rejected = context.request.get(
        f"{server.base}/files/tickets/{ticket_id}/previews/escape.html", headers=confined_headers
    )
    missing = context.request.get(
        f"{server.base}/files/tickets/{ticket_id}/previews/missing.html", headers=confined_headers
    )
    assert rejected.status == missing.status == 404
    assert rejected.body() == missing.body()
    assert b"Secret symlink bytes" not in rejected.body()
    assert str(outside).encode() not in rejected.body()

    child.write_text("<h1>Refreshed child</h1>", encoding="utf-8")
    artifact.locator("[data-ticket-artifact-refresh]").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Refreshed child").wait_for(timeout=WAIT_MS)
    assert page.url == artifact_address
    artifact.locator("[data-html-back]").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Initial artifact").wait_for(timeout=WAIT_MS)

    document.write_text("<h1>Refreshed artifact</h1>", encoding="utf-8")
    artifact.locator("[data-ticket-artifact-refresh]").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Refreshed artifact").wait_for(timeout=WAIT_MS)
    assert page.url == artifact_address
    assert page.evaluate("window.__artifactInPlaceMarker") == "kept"
    assert page.locator(ticket_selector).count() == 1
    assert artifact.is_visible()

    # A shared preview address uses the same navigation in the same browser context.
    document.write_text(
        '<h1>Standalone index</h1><a href="nested/child.html">Open child</a>', encoding="utf-8"
    )
    page.goto(
        f"{server.base}/#/preview?source=ticket&ticket={ticket_id}&path=previews%2Findex.html"
    )
    standalone = page.locator("[data-file-preview-route]")
    standalone_frame = standalone.frame_locator("iframe[data-file-preview-html]")
    standalone_frame.get_by_role("heading", name="Standalone index").wait_for(timeout=WAIT_MS)
    standalone_frame.get_by_role("link", name="Open child").click(timeout=WAIT_MS)
    standalone_frame.get_by_role("heading", name="Refreshed child").wait_for(timeout=WAIT_MS)
    standalone.locator("[data-html-back]").click(timeout=WAIT_MS)
    standalone_frame.get_by_role("heading", name="Standalone index").wait_for(timeout=WAIT_MS)
    assert standalone.locator("iframe").get_attribute("sandbox") == "allow-scripts"

    page.goto(f"{server.base}/#/review")
    page.locator('[data-screen="review"] [data-review-empty]').wait_for(timeout=WAIT_MS)
    for retired_text in ("Everything", "Newsreader"):
        expect(page.get_by_text(retired_text, exact=True)).to_have_count(0)
    if evidence:
        page.screenshot(path=str(Path(evidence) / "combined-review-ui.png"), full_page=True)

    # An empty Chief thread uses its first-message prompt. The lower-level default
    # agent placeholder is checked in the executed production module above.
    page.goto(f"{server.base}/#/workspace/chief-of-staff")
    expect(page.get_by_placeholder(
        "Send the first message to start it...", exact=True
    )).to_be_visible(
        timeout=WAIT_MS
    )
    for retired_text in ("Steer the work", "Everything", "Newsreader"):
        expect(page.get_by_text(retired_text, exact=True)).to_have_count(0)
    if evidence:
        page.screenshot(path=str(Path(evidence) / "combined-chief-ui.png"), full_page=True)

    # Reuse this server and browser for the other hosts. No new provider run occurs.
    document.write_text(retained_document, encoding="utf-8")
    artifact_link = f"[Dock artifact](/files/tickets/{ticket_id}/previews/index.html)"
    project = _post(server, "/api/projects", {"name": "Dock artifact proof"})
    manager = _post(server, "/api/managers", {
        "title": "Dock manager", "project_id": project["id"],
    })
    response = context.request.patch(
        server.base + f"/api/managers/{manager['id']}", data={"goal": artifact_link}
    )
    assert response.ok, response.text()
    page.goto(f"{server.base}/#/workspace/manager/{manager['id']}")
    page.locator("[data-goal-band]").wait_for(timeout=WAIT_MS)
    page.locator("[data-goal-band]").evaluate("node => { node.open = true; }")
    page.locator("a.pv-name:visible").filter(has_text="index.html").first.click()
    for width, height, layout in ((1440, 1000, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        page.locator("[data-ticket-artifact]").frame_locator("iframe").get_by_role(
            "heading", name="Initial artifact"
        ).wait_for(timeout=WAIT_MS)
        _prove_dock_artifact_retention(page, f"manager-{layout}", evidence)

    chief_conversation = "dock-chief-artifact-proof"
    _post(server, f"/api/test/signals/{chief_conversation}/start", {})
    _bind(server, "agent", "chief_of_staff", chief_conversation)
    _message(
        server, chief_conversation, CHIEF_PRINCIPAL,
        "# The artifact is ready\n\n" + artifact_link
        + "\n\n- Inspect the bottom control\n- Keep the form value",
    )
    page.goto(f"{server.base}/#/workspace/chief-of-staff")
    expect(page.locator("[data-conversation-unread-preview]")).to_contain_text(
        "The artifact is ready", timeout=WAIT_MS
    )
    expect(page.locator("[data-conversation-rest-line]")).to_have_text("Working", timeout=WAIT_MS)
    if evidence:
        page.screenshot(path=str(Path(evidence) / "chief-working-unread.png"), full_page=True)
    _post(server, f"/api/test/signals/{chief_conversation}/finish", {})
    expect(page.locator("[data-conversation-rest-line]")).to_have_text(
        "Chief idle", timeout=WAIT_MS
    )
    expect(page.locator("[data-conversation-unread-preview]")).to_contain_text("Dock artifact")
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.locator("a.pv-name:visible").filter(has_text="index.html").first.click()
    for width, height, layout in ((1440, 1000, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        page.locator("[data-ticket-artifact]").frame_locator("iframe").get_by_role(
            "heading", name="Initial artifact"
        ).wait_for(timeout=WAIT_MS)
        _prove_dock_artifact_retention(page, f"chief-{layout}", evidence)
    if evidence:
        (Path(evidence) / "dock-connected-proof.json").write_text(json.dumps({
            "hosts": ["Ticket", "Manager", "Chief"],
            "viewports": ["1440x1000", "390x844"],
            "retained": [
                "iframe identity", "iframe source", "form value", "scroll", "selection", "draft",
            ],
            "bottom_control_accessible": True,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        }, indent=2) + "\n", encoding="utf-8")


def test_a_media_preview_costs_nothing_until_it_is_played_and_then_it_plays(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """A real browser and a real route, because nothing smaller can prove this.

    The risk is that a media preview which downloads nothing up front cannot then play
    or seek. Playing and seeking are the browser's own media stack talking to the
    route's range handling, over a real socket. A component test has no range server and
    a unit test has no media stack, so neither can tell a working preview from one that
    shows a player and then refuses.
    """
    ticket_id = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Media preview"
    )["id"]
    _write_ticket_files(server, ticket_id)
    root = _ticket_files_dir(server, ticket_id)
    clip = Path(__file__).parent / "fixtures" / "playable_clip.mp4"
    (root / "video" / "clip.mp4").write_bytes(clip.read_bytes())
    _settle_what_changes(
        server,
        ticket_id,
        f"Take a look.\n\n[clip.mp4](/files/tickets/{ticket_id}/video/clip.mp4)",
    )

    context = context_factory()
    page = context.new_page()
    asked_for_the_clip: list[str] = []
    page.on(
        "request",
        lambda request: asked_for_the_clip.append(request.url)
        if "video/clip.mp4" in request.url
        else None,
    )
    page.goto(server.base + f"/#/workspace/{ticket_id}")
    page.wait_for_selector(
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "what_changes")
    play = page.locator('[data-file-preview-kind="video"] [data-media-play]').first
    play.wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(1500)
    assert page.locator("video").count() == 0

    # Nothing has been asked for: the reader has not asked for it.
    assert asked_for_the_clip == [], asked_for_the_clip
    # The reader can still see which file the player is.
    name = page.locator('[data-file-preview-kind="video"] .file-preview-name-link').first
    assert name.is_visible(), "a player with no frame must still name its file"
    assert "clip.mp4" in name.inner_text()

    # It plays on the first action.
    play.click()
    video = page.locator("video").first
    video.evaluate("node => { node.muted = true; return node.play(); }")
    page.wait_for_function(
        "() => { const v = document.querySelector('video');"
        " return v !== null && v.readyState >= 3; }",
        timeout=WAIT_MS,
    )
    assert asked_for_the_clip, "playing must fetch the clip"

    # And it seeks.
    video.evaluate("node => { node.currentTime = 2.0; }")
    page.wait_for_function(
        "() => { const v = document.querySelector('video');"
        " return v !== null && v.currentTime >= 1.9 && v.readyState >= 3; }",
        timeout=WAIT_MS,
    )
    played = video.evaluate("node => [node.currentTime, node.duration, node.readyState]")
    assert played[0] >= 1.9, played
    assert played[1] > 2.5, played
