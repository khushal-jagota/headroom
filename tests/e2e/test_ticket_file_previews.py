"""Browser coverage for managed ticket-file previews."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    BrowserContext,
    FrameLocator,
    Locator,
    Page,
    Request,
    Route,
)
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

WAIT_MS = 10_000
INTERACTIVE_HTML_FIXTURE = (
    Path(__file__).with_name("fixtures") / "repro_interactive_workspace_ticket_rows.html"
)


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


def _write_managed_html_reference_files(server: ServerHandle, ticket_id: str) -> None:
    root = _ticket_files_dir(server, ticket_id)
    assets = root / "previews" / "assets"
    assets.mkdir(parents=True)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAW0lEQVR4nO3PQQ0AIBDAsAP/"
        "nuGNAvZoFSzZOjNnyNi1dwfgUQCeBOBJAI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJ"
        "AI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJAN4A2icCftL8UqQAAAAASUVORK5CYII="
    )
    (assets / "relative.png").write_bytes(png)
    (assets / "root.png").write_bytes(png)
    (assets / "relative.css").write_text(
        "#relative-styled { color: rgb(13, 71, 161); }",
        encoding="utf-8",
    )
    (assets / "root.css").write_text(
        "#root-styled { color: rgb(27, 94, 32); }",
        encoding="utf-8",
    )
    (root / "previews" / "index.html").write_text(
        "<!-- <head> -->"
        "<!doctype html>"
        "<html>"
        "<head>"
        '<link rel="stylesheet" href="assets/relative.css">'
        f'<link rel="stylesheet" href="/files/tickets/{ticket_id}/previews/assets/root.css">'
        "</head>"
        "<body>"
        '<p id="relative-styled">relative stylesheet</p>'
        '<p id="root-styled">root-relative stylesheet</p>'
        '<img id="relative-image" src="assets/relative.png" alt="relative image">'
        f'<img id="root-image" src="/files/tickets/{ticket_id}/previews/assets/root.png" '
        'alt="root-relative image">'
        "</body>"
        "</html>",
        encoding="utf-8",
    )


def _expected_hrefs(ticket_id: str) -> dict[str, str]:
    return {
        "Markdown": f"/files/tickets/{ticket_id}/notes/space%20name.md",
        "Image": f"/files/tickets/{ticket_id}/images/pic.png",
        "Video": f"/files/tickets/{ticket_id}/video/demo.mp4",
        "Audio": f"/files/tickets/{ticket_id}/audio/demo.mp3",
        "HTML": f"/files/tickets/{ticket_id}/page.html",
        "Binary": f"/files/tickets/{ticket_id}/archive.bin",
        "External": "https://example.com/outside",
    }


def _links(ticket_id: str) -> str:
    return "\n".join(f"[{label}]({href})" for label, href in _expected_hrefs(ticket_id).items())


def _set_fields(
    server: ServerHandle, ticket_id: str, fields: dict[str, Any], stage: str = "dropped"
) -> None:
    if "kickoff" not in fields:
        fields = {
            "kickoff": {"value": "", "proposal": None, "user_note": None},
            **fields,
        }
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET stage = ?, fields = ?, updated_at = 2 WHERE id = ?",
            (stage, json.dumps(fields), ticket_id),
        )


def _open_ticket_field(page: Page, field: str) -> None:
    section = page.locator(f'details[data-field="{field}"]').first
    section.evaluate(
        """(node) => {
          const settledStages = node.closest('details.stage-fold');
          if (settledStages) settledStages.open = true;
          node.open = true;
        }"""
    )
    page.locator(f'details[data-field="{field}"][open]').wait_for(state="attached", timeout=WAIT_MS)


def _assert_painted(locator: Locator) -> None:
    box = locator.bounding_box(timeout=WAIT_MS)
    assert box is not None
    assert box["width"] > 0
    assert box["height"] > 0


def _exercise_interactive_workspace_rows(frame: FrameLocator) -> None:
    initial_row = frame.locator("#stacked [data-rail] .ticket-row").first
    initial_row.wait_for(state="visible", timeout=WAIT_MS)
    _assert_painted(initial_row)

    anchored_tab = frame.locator('[role="tab"][data-target="anchored"]')
    anchored_tab.click(timeout=WAIT_MS)
    assert anchored_tab.get_attribute("aria-selected") == "true"
    frame.locator("#stacked").wait_for(state="hidden", timeout=WAIT_MS)

    anchored_row = frame.locator("#anchored.variant.active [data-rail] .ticket-row").first
    anchored_row.wait_for(state="visible", timeout=WAIT_MS)
    _assert_painted(anchored_row)


def _assert_managed_html_references_render(frame: FrameLocator) -> None:
    frame.locator("#relative-styled").wait_for(state="visible", timeout=WAIT_MS)
    frame.locator("#root-styled").wait_for(state="visible", timeout=WAIT_MS)
    assert frame.locator("#relative-styled").evaluate(
        "node => getComputedStyle(node).color"
    ) == "rgb(13, 71, 161)"
    assert frame.locator("#root-styled").evaluate(
        "node => getComputedStyle(node).color"
    ) == "rgb(27, 94, 32)"
    for image_id in ("relative-image", "root-image"):
        metrics = frame.locator(f"#{image_id}").evaluate(
            """image => new Promise((resolve) => {
                const done = () => resolve({
                    complete: image.complete,
                    naturalWidth: image.naturalWidth,
                    naturalHeight: image.naturalHeight,
                    width: image.getBoundingClientRect().width,
                    height: image.getBoundingClientRect().height,
                });
                if (image.complete) {
                    done();
                } else {
                    image.addEventListener("load", done, { once: true });
                    image.addEventListener("error", done, { once: true });
                }
            })"""
        )
        assert metrics["complete"] is True
        assert metrics["naturalWidth"] == metrics["naturalHeight"] == 64
        assert metrics["width"] > 0
        assert metrics["height"] > 0


def _assert_full_page_markdown_document(page: Page, heading: str) -> None:
    page.wait_for_function(
        "heading => document.querySelector("
        "'[data-file-preview-route] [data-file-preview-markdown] h1'"
        ")?.textContent === heading",
        arg=heading,
        timeout=WAIT_MS,
    )
    assert page.locator("[data-file-preview-route].file-preview-route--document").count() == 1
    assert page.inner_text("[data-file-preview-route] h1") == heading
    assert page.locator("[data-file-preview-route] > [data-file-preview]").count() == 0
    assert page.locator("[data-file-preview-route] > .file-preview-document").count() == 0
    geometry = page.evaluate(
        """() => {
            const shellContent = document.querySelector('.shell-content');
            const shell = shellContent.getBoundingClientRect();
            const route = document.querySelector(
                '[data-file-preview-route]'
            ).getBoundingClientRect();
            const doc = document.querySelector(
                '[data-file-preview-markdown]'
            ).getBoundingClientRect();
            return {
                viewportHeight: window.innerHeight,
                shellWidth: shell.width,
                routeWidth: route.width,
                routeHeight: route.height,
                docWidth: doc.width,
                docHeight: doc.height,
                shellPaddingTop: getComputedStyle(shellContent).paddingTop,
            };
        }"""
    )
    assert geometry["shellPaddingTop"] == "0px"
    assert geometry["routeWidth"] >= geometry["shellWidth"] - 2
    assert geometry["docWidth"] >= geometry["shellWidth"] - 2
    assert geometry["routeHeight"] >= 0.75 * geometry["viewportHeight"]
    assert geometry["docHeight"] > 0


def _wait_for_field_text(
    api: ApiHelper, server: ServerHandle, ticket_id: str, field: str, expected_fragment: str
) -> str:
    deadline = time.monotonic() + WAIT_MS / 1000
    while time.monotonic() < deadline:
        ticket = api.get(server, f"/api/tickets/{ticket_id}")
        value: str = ticket["fields"][field]["value"]
        if expected_fragment in value:
            return value
        time.sleep(0.1)
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    value = ticket["fields"][field]["value"]
    assert expected_fragment in value
    return value


def test_preview_hash_route_renders_markdown_and_sandboxes_html(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "File preview route",
    )["id"]
    _write_ticket_files(server, ticket_id)
    markdown_path = _ticket_files_dir(server, ticket_id) / "notes" / "space name.md"
    markdown_path.write_text(
        markdown_path.read_text(encoding="utf-8")
        + f"\n\n[Image](/files/tickets/{ticket_id}/images/pic.png)",
        encoding="utf-8",
    )
    fields = {
        "success": {
            "value": (
                f"[Markdown](/files/tickets/{ticket_id}/notes/space%20name.md)\n\n"
                f"[HTML](/files/tickets/{ticket_id}/page.html)"
            ),
            "proposal": None,
            "user_note": None,
        },
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields)
    context = context_factory()

    ticket_page = open_page(
        context,
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_ticket_field(ticket_page, "success")
    embedded_markdown = ticket_page.locator('[data-file-preview-kind="markdown"]').first
    embedded_markdown.locator("h1", has_text="File Notes").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert embedded_markdown.locator(".file-preview-document-body").first.evaluate(
        "node => getComputedStyle(node).backgroundColor"
    ) == "rgb(20, 18, 16)"
    assert (
        embedded_markdown.locator("a.file-preview-link:visible").first.get_attribute("href")
        == f"#/preview?source=ticket&ticket={ticket_id}&path=notes%2Fspace%20name.md"
    )
    expected_markdown_url = (
        f"{server.base}/#/preview?source=ticket&ticket={ticket_id}"
        "&path=notes%2Fspace%20name.md"
    )
    # The preview address is the way in from outside a Ticket screen: a shared link or a
    # stored notification. It opens here rather than in a new tab, so arriving is a hash
    # navigation in this same page. This marker is set before the move and read back
    # after it, because the arrival assertions below would read the same either way if
    # the app had reloaded. A click on the same link inside the Ticket does not come
    # here at all — see test_ticket_artifact_opens_in_place_over_the_ticket.
    ticket_page.evaluate("window.__previewRouteNavigationMarker = 'kept'")
    ticket_page.evaluate("url => { window.location.href = url; }", expected_markdown_url)
    ticket_page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_markdown_url,
        timeout=WAIT_MS,
    )
    assert ticket_page.evaluate("window.__previewRouteNavigationMarker") == "kept"
    page = ticket_page
    assert page.url == expected_markdown_url
    _assert_full_page_markdown_document(page, "File Notes")
    assert (
        page.locator('[data-file-preview-markdown] [data-file-preview-kind="markdown"] h1')
        .filter(has_text="Other Notes")
        .count()
        == 1
    )
    self_link_line = page.locator(
        "[data-file-preview-markdown] "
        '.file-preview--inline[data-file-preview-kind="markdown"] a.file-preview-link:visible'
    )
    assert self_link_line.count() == 1
    assert self_link_line.inner_text() == "Open space name.md"
    managed_image = page.locator(
        '[data-file-preview-markdown] [data-file-preview-kind="image"] img'
    )
    managed_image.wait_for(state="visible", timeout=WAIT_MS)
    _assert_painted(managed_image)

    page.evaluate("window.__previewHashNavigationMarker = 'kept'")
    page.evaluate(
        "ticket => { window.location.hash = "
        "'#/preview?source=ticket&ticket=' + ticket + '&path=notes%2Fother.md'; }",
        ticket_id,
    )
    _assert_full_page_markdown_document(page, "Other Notes")
    assert page.evaluate("window.__previewHashNavigationMarker") == "kept"

    # Back is the way out of a preview. Two hops back — the nested markdown, then the
    # markdown that was opened from the field — return this page to the ticket.
    page.go_back()
    page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_markdown_url,
        timeout=WAIT_MS,
    )
    page.go_back()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )

    ticket_page.evaluate("window.__htmlPreviewNavigationMarker = 'kept'")
    _open_ticket_field(ticket_page, "success")
    embedded_preview = ticket_page.locator('[data-file-preview-kind="html"]').first
    embedded_preview.locator("iframe").wait_for(state="visible", timeout=WAIT_MS)
    expected_html_url = f"{server.base}/#/preview?source=ticket&ticket={ticket_id}&path=page.html"
    ticket_page.evaluate("url => { window.location.href = url; }", expected_html_url)
    ticket_page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_html_url,
        timeout=WAIT_MS,
    )
    assert ticket_page.url == expected_html_url

    full_html_iframe = ticket_page.locator(
        "[data-file-preview-route] iframe[data-file-preview-html]"
    )
    full_html_iframe.wait_for(state="visible", timeout=WAIT_MS)
    html_frame = ticket_page.frame_locator(
        "[data-file-preview-route] iframe[data-file-preview-html]"
    )
    assert full_html_iframe.get_attribute("sandbox") == "allow-scripts"
    assert full_html_iframe.get_attribute("allow") is None
    assert html_frame.locator("h1").inner_text(timeout=WAIT_MS) == "HTML File"
    heading_box = html_frame.locator("h1").bounding_box(timeout=WAIT_MS)
    assert heading_box is not None
    assert heading_box["width"] > 0
    assert heading_box["height"] > 0
    assert ticket_page.evaluate("window.__ticketFileScriptRan === true") is False
    assert ticket_page.locator("[data-file-preview-route] > .file-preview-document").count() == 0
    html_geometry = ticket_page.evaluate(
        """() => {
            const shell = document.querySelector('.shell-content').getBoundingClientRect();
            const route = document.querySelector(
                '[data-file-preview-route]'
            ).getBoundingClientRect();
            const frame = document.querySelector(
                '[data-file-preview-route] [data-file-preview-html]'
            ).getBoundingClientRect();
            return {
                shellWidth: shell.width,
                routeWidth: route.width,
                routeHeight: route.height,
                frameWidth: frame.width,
                frameHeight: frame.height,
                frameBottomGap: window.innerHeight - frame.bottom,
            };
        }"""
    )
    assert html_geometry["frameWidth"] >= html_geometry["shellWidth"] - 2
    assert html_geometry["frameHeight"] >= 0.75 * html_geometry["routeHeight"]
    assert html_geometry["frameBottomGap"] <= 24
    ticket_page.go_back()
    ticket_page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    assert ticket_page.evaluate("window.__htmlPreviewNavigationMarker") == "kept"


def _store_conversation_saying(
    server: ServerHandle, ticket_id: str, conversation_id: str, text: str
) -> None:
    """A Ticket conversation holding one agent message, so its transcript has a link."""
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "INSERT INTO conversations (conversation_id, backend_key, model, "
            "workspace_folder, access, latest_sequence, created_at) "
            "VALUES (?, 'codex', 'gpt-5.6-sol', '/tmp/artifact-workspace', 'full', 2, ?)",
            (conversation_id, 1_700_000_000),
        )
        conn.executemany(
            "INSERT INTO conversation_events "
            "(conversation_id, sequence, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            [
                (conversation_id, 1, "agent_message", json.dumps({"text": text}), 1_700_000_001),
                (
                    conversation_id,
                    2,
                    "turn_ended",
                    json.dumps({"ending": "completed", "error_summary": None}),
                    1_700_000_002,
                ),
            ],
        )
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket_id),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?", (conversation_id, ticket_id)
        )


def test_ticket_artifact_opens_in_place_over_the_ticket(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """An artifact opens on the Ticket screen it was clicked from.

    This needs a real browser and a live server together: the click is caught by the
    screen rather than by any one component, Panels serves the file, and what is proved
    is where the artifact lands — over the Ticket, with the conversation still on the
    page beside it. No component or unit test can produce that.
    """
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Artifact in place",
    )["id"]
    _write_ticket_files(server, ticket_id)
    _set_fields(
        server,
        ticket_id,
        {
            "success": {
                # Enough text above the link that reaching it scrolls the ticket, which
                # is what makes "closing gives back the page you left" a real claim.
                "value": (
                    "Filler paragraph.\n\n" * 40
                    + f"[Markdown](/files/tickets/{ticket_id}/notes/space%20name.md)"
                ),
                "proposal": None,
                "user_note": None,
            },
            "approach": {"value": None, "proposal": None, "user_note": None},
            "plan": {"value": None, "proposal": None, "user_note": None},
            "implementation": {"value": None, "proposal": None, "user_note": None},
            "closeout": {"value": None, "proposal": None, "user_note": None},
        },
    )
    # The worker also posted the artifact in the conversation, which is where a person
    # most often meets one.
    _store_conversation_saying(
        server,
        ticket_id,
        "conv_artifact_in_place",
        f"Here it is: [Markdown](/files/tickets/{ticket_id}/notes/space%20name.md)",
    )
    context = context_factory()
    page = open_page(
        context,
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_ticket_field(page, "success")
    ticket_address = f"{server.base}/#/workspace/{ticket_id}"
    artifact_address = (
        f"{ticket_address}?source=ticket&ticket={ticket_id}&path=notes%2Fspace%20name.md"
    )
    open_link = page.locator("a.file-preview-link:visible", has_text="Open space name.md").first

    # Reaching the link scrolls the ticket away from its top, so closing can be shown to
    # give back the page the reader left rather than a fresh one.
    open_link.scroll_into_view_if_needed(timeout=WAIT_MS)
    scrolled_to = page.locator(".ticket-doc").evaluate("node => node.scrollTop")
    assert scrolled_to > 0

    page.evaluate("window.__artifactInPlaceMarker = 'kept'")
    open_link.click()

    artifact = page.locator("[data-ticket-artifact]")
    artifact.wait_for(state="visible", timeout=WAIT_MS)
    page.wait_for_function(
        "expected => window.location.href === expected",
        arg=artifact_address,
        timeout=WAIT_MS,
    )
    # The app never reloaded, and the Ticket screen is still the screen.
    assert page.evaluate("window.__artifactInPlaceMarker") == "kept"
    assert (
        page.locator(f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]').count() == 1
    )
    assert page.locator("[data-file-preview-route]").count() == 0
    artifact.locator("[data-file-preview-markdown] h1").first.wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert artifact.locator("[data-ticket-artifact-close]").inner_text() == "Close"

    # The artifact covers the ticket's own reading area and nothing else. The
    # conversation keeps its place at the bottom of the page, so the reader can talk to
    # the worker about what they are looking at.
    geometry = page.evaluate(
        """() => {
            const box = (selector) =>
                document.querySelector(selector).getBoundingClientRect();
            const artifact = box('[data-ticket-artifact]');
            const reading = box('.ticket-reading');
            const conversation = box('[data-conversation-pane]');
            return {
                artifactTop: artifact.top,
                artifactBottom: artifact.bottom,
                artifactWidth: artifact.width,
                artifactHeight: artifact.height,
                readingTop: reading.top,
                readingBottom: reading.bottom,
                readingWidth: reading.width,
                conversationTop: conversation.top,
                conversationHeight: conversation.height,
                viewportHeight: window.innerHeight,
            };
        }"""
    )
    assert abs(geometry["artifactTop"] - geometry["readingTop"]) <= 1
    assert abs(geometry["artifactBottom"] - geometry["readingBottom"]) <= 1
    assert abs(geometry["artifactWidth"] - geometry["readingWidth"]) <= 1
    assert geometry["artifactHeight"] > 0.3 * geometry["viewportHeight"]
    assert geometry["conversationTop"] >= geometry["artifactBottom"] - 1
    assert geometry["conversationHeight"] > 0

    # The ticket is still mounted underneath, holding the place the reader had it at.
    assert page.locator(".ticket-doc").evaluate("node => node.scrollTop") == scrolled_to

    # Escape closes the artifact and gives the ticket back, at the same place.
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-ticket-artifact]", state="detached", timeout=WAIT_MS)
    page.wait_for_function(
        "expected => window.location.href === expected",
        arg=ticket_address,
        timeout=WAIT_MS,
    )
    assert page.locator(".ticket-doc").evaluate("node => node.scrollTop") == scrolled_to

    # An artifact posted in the conversation opens the same way. An opened conversation
    # is the whole page, and an artifact opened behind it would be one nobody can see, so
    # opening it steps the conversation back to peeked.
    page.locator("[data-conversation-rest-bar]").click(timeout=WAIT_MS)
    page.wait_for_selector('[data-conversation-state="peeked"]', timeout=WAIT_MS)
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.wait_for_selector('[data-conversation-state="opened"]', timeout=WAIT_MS)
    page.locator(
        "[data-conversation-pane] a.file-preview-link:visible", has_text="Open space name.md"
    ).first.click(timeout=WAIT_MS)
    artifact.wait_for(state="visible", timeout=WAIT_MS)
    page.wait_for_selector('[data-conversation-state="peeked"]', timeout=WAIT_MS)
    assert page.locator("[data-ticket-artifact] [data-file-preview-markdown]").count() == 1
    page.wait_for_function(
        "expected => window.location.href === expected",
        arg=artifact_address,
        timeout=WAIT_MS,
    )
    page.keyboard.press("Escape")
    page.wait_for_selector("[data-ticket-artifact]", state="detached", timeout=WAIT_MS)

    # The artifact is in the address, so it survives a reload, and Back is a way out of
    # what the reader opened.
    page.goto(artifact_address, wait_until="domcontentloaded")
    page.locator("[data-ticket-artifact] [data-file-preview-markdown] h1").first.wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.locator("[data-ticket-artifact-close]").click()
    page.wait_for_selector("[data-ticket-artifact]", state="detached", timeout=WAIT_MS)
    page.go_back()
    page.locator("[data-ticket-artifact] [data-file-preview-markdown] h1").first.wait_for(
        state="visible", timeout=WAIT_MS
    )


def test_mobile_embedded_managed_files_use_preview_links(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Mobile file preview links",
    )["id"]
    _write_ticket_files(server, ticket_id)
    _set_fields(
        server,
        ticket_id,
        {
            "success": {"value": _links(ticket_id), "proposal": None, "user_note": None},
            "approach": {"value": None, "proposal": None, "user_note": None},
            "plan": {"value": None, "proposal": None, "user_note": None},
            "implementation": {"value": None, "proposal": None, "user_note": None},
            "closeout": {"value": None, "proposal": None, "user_note": None},
        },
    )

    context = context_factory()
    page = open_page(
        context,
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    page.set_viewport_size({"width": 390, "height": 844})
    _open_ticket_field(page, "success")

    for kind in ("markdown", "html", "image", "video", "audio"):
        preview = page.locator(f'[data-file-preview-kind="{kind}"]').first
        mobile_link = preview.locator("a.file-preview-mobile-link:visible").first
        mobile_link.wait_for(state="visible", timeout=WAIT_MS)
        assert mobile_link.get_attribute("href") == (
            f"#/preview?source=ticket&ticket={ticket_id}&path="
            + {
                "markdown": "notes%2Fspace%20name.md",
                "html": "page.html",
                "image": "images%2Fpic.png",
                "video": "video%2Fdemo.mp4",
                "audio": "audio%2Fdemo.mp3",
            }[kind]
        )

    download = page.locator('[data-file-preview-kind="download"]').first
    assert download.locator("a.file-preview-mobile-link").count() == 0
    download.locator('a.file-preview-link[download]').wait_for(state="visible", timeout=WAIT_MS)


def test_interactive_html_preview_paints_and_switches_variants_in_both_surfaces(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Interactive HTML preview",
    )["id"]
    root = _ticket_files_dir(server, ticket_id)
    root.mkdir(parents=True)
    (root / "interactive.html").write_text(
        INTERACTIVE_HTML_FIXTURE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    fields = {
        "success": {
            "value": f"[Interactive HTML](/files/tickets/{ticket_id}/interactive.html)",
            "proposal": None,
            "user_note": None,
        },
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields)
    context = context_factory()
    ticket_page = open_page(
        context,
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_ticket_field(ticket_page, "success")

    embedded_preview = ticket_page.locator('[data-file-preview-kind="html"]').first
    embedded_iframe = embedded_preview.locator("iframe[data-file-preview-html]")
    embedded_iframe.wait_for(state="visible", timeout=WAIT_MS)
    assert embedded_iframe.get_attribute("sandbox") == "allow-scripts"
    assert embedded_iframe.get_attribute("allow") is None
    _exercise_interactive_workspace_rows(
        embedded_preview.frame_locator("iframe[data-file-preview-html]")
    )

    # The second surface is the artifact opened in place, on this same Ticket screen.
    embedded_preview.locator(
        "a.file-preview-link:visible", has_text="Open interactive.html"
    ).click()
    expected_artifact_url = (
        f"{server.base}/#/workspace/{ticket_id}"
        f"?source=ticket&ticket={ticket_id}&path=interactive.html"
    )
    ticket_page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_artifact_url,
        timeout=WAIT_MS,
    )
    full_iframe = ticket_page.locator("[data-ticket-artifact] iframe[data-file-preview-html]")
    full_iframe.wait_for(state="visible", timeout=WAIT_MS)
    assert full_iframe.get_attribute("sandbox") == "allow-scripts"
    assert full_iframe.get_attribute("allow") is None
    _exercise_interactive_workspace_rows(
        ticket_page.frame_locator("[data-ticket-artifact] iframe[data-file-preview-html]")
    )


def test_managed_html_preview_loads_sibling_stylesheets_and_images_in_both_surfaces(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Managed HTML references",
    )["id"]
    _write_managed_html_reference_files(server, ticket_id)
    fields = {
        "success": {
            "value": f"[Managed HTML](/files/tickets/{ticket_id}/previews/index.html)",
            "proposal": None,
            "user_note": None,
        },
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields)
    context = context_factory()
    ticket_page = open_page(
        context,
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_ticket_field(ticket_page, "success")

    embedded_preview = ticket_page.locator('[data-file-preview-kind="html"]').first
    embedded_iframe = embedded_preview.locator("iframe[data-file-preview-html]")
    embedded_iframe.wait_for(state="visible", timeout=WAIT_MS)
    assert embedded_iframe.get_attribute("sandbox") == "allow-scripts"
    assert embedded_iframe.get_attribute("allow") is None
    _assert_managed_html_references_render(
        embedded_preview.frame_locator("iframe[data-file-preview-html]")
    )

    # The second surface is the artifact opened in place, on this same Ticket screen.
    embedded_preview.locator("a.file-preview-link:visible", has_text="Open index.html").click()
    expected_artifact_url = (
        f"{server.base}/#/workspace/{ticket_id}"
        f"?source=ticket&ticket={ticket_id}&path=previews%2Findex.html"
    )
    ticket_page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_artifact_url,
        timeout=WAIT_MS,
    )
    full_iframe = ticket_page.locator("[data-ticket-artifact] iframe[data-file-preview-html]")
    full_iframe.wait_for(state="visible", timeout=WAIT_MS)
    assert full_iframe.get_attribute("sandbox") == "allow-scripts"
    assert full_iframe.get_attribute("allow") is None
    _assert_managed_html_references_render(
        ticket_page.frame_locator("[data-ticket-artifact] iframe[data-file-preview-html]")
    )


def test_editable_markdown_atomic_preview_adjacent_edits_and_selected_deletion(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Atomic preview editing",
    )["id"]
    _write_ticket_files(server, ticket_id)
    markdown_token = f"[Markdown](/files/tickets/{ticket_id}/notes/space%20name.md)"
    image_token = f"[Image](/files/tickets/{ticket_id}/images/pic.png)"
    binary_token = f"[Binary](/files/tickets/{ticket_id}/archive.bin)"
    entity_token = "[**Entity & label**](https://example.org/path?x=1&y=2#part)"
    body = (
        "Intro\n\n"
        f"{markdown_token}\n\n"
        f"{image_token}\n\n"
        f"{binary_token}\n\n"
        f"{entity_token}\n\n"
        "Outro"
    )
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, stage="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )

    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-markdown-atomic-slot='true']").first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator(editable).focus()
    markdown_slot = page.locator(
        f"{editable} [data-markdown-source-token='{markdown_token}']"
    ).first
    before_guard = markdown_slot.locator(
        "xpath=preceding-sibling::*[@data-markdown-caret-guard='before'][1]"
    )
    after_guard = markdown_slot.locator(
        "xpath=following-sibling::*[@data-markdown-caret-guard='after'][1]"
    )
    before_guard.evaluate(
        """(guard) => {
            const range = document.createRange();
            range.selectNodeContents(guard);
            range.collapse(false);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.keyboard.type("Before preview ")
    after_guard.evaluate(
        """(guard) => {
            const range = document.createRange();
            range.selectNodeContents(guard);
            range.collapse(false);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.keyboard.type(" after preview")

    image_slot = page.locator(
        f"{editable} [data-markdown-source-token='{image_token}']"
    ).first
    image_slot.locator(
        "xpath=following-sibling::*[@data-markdown-caret-guard='after'][1]"
    ).evaluate(
        """(guard) => {
            const range = document.createRange();
            range.selectNodeContents(guard);
            range.collapse(false);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            const data = new DataTransfer();
            data.setData("text/plain", " pasted safely");
            guard.dispatchEvent(
                new ClipboardEvent("paste", { bubbles: true, clipboardData: data })
            );
        }"""
    )
    page.locator(editable).blur()
    stored_body = _wait_for_field_text(api, server, ticket_id, "success", "Before preview")
    assert "Before preview [Markdown](/files/tickets/" in stored_body
    assert "space%20name.md) after preview" in stored_body
    assert image_token in stored_body
    assert "pasted safely" in stored_body
    assert binary_token in stored_body
    assert entity_token in stored_body
    assert "data-file-preview" not in stored_body
    assert "<iframe" not in stored_body

    page.reload()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    markdown_slot = page.locator(
        f"{editable} [data-markdown-source-token='{markdown_token}']"
    ).first
    page.locator(editable).focus()
    markdown_slot.locator(
        "xpath=preceding-sibling::*[@data-markdown-caret-guard='before'][1]"
    ).evaluate(
        """(guard) => {
            const selection = getSelection();
            const range = document.createRange();
            range.selectNodeContents(guard);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.keyboard.press("Delete")
    page.locator(editable).blur()
    page.wait_for_function("f0 => window.__plannerDebug.flushes > f0", arg=f0, timeout=WAIT_MS)
    stored_after_delete = api.get(server, f"/api/tickets/{ticket_id}")["fields"]["success"]["value"]
    assert markdown_token not in stored_after_delete
    assert image_token in stored_after_delete

    page.reload()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    image_slot = page.locator(
        f"{editable} [data-markdown-source-token='{image_token}']"
    ).first
    page.locator(editable).focus()
    image_slot.locator(
        "xpath=following-sibling::*[@data-markdown-caret-guard='after'][1]"
    ).evaluate(
        """(guard) => {
            const selection = getSelection();
            const range = document.createRange();
            range.selectNodeContents(guard);
            range.collapse(true);
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.keyboard.press("Backspace")
    page.locator(editable).blur()
    page.wait_for_function("f0 => window.__plannerDebug.flushes > f0", arg=f0, timeout=WAIT_MS)
    stored_after_backspace = api.get(server, f"/api/tickets/{ticket_id}")["fields"]["success"][
        "value"
    ]
    assert image_token not in stored_after_backspace
    assert binary_token in stored_after_backspace

    page.reload()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    binary_slot = page.locator(
        f"{editable} [data-markdown-source-token='{binary_token}']"
    ).first
    page.locator(editable).focus()
    binary_slot.evaluate(
        """(slot) => {
            const selection = getSelection();
            const range = document.createRange();
            range.selectNode(slot);
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.keyboard.press("Delete")
    page.locator(editable).blur()
    page.wait_for_function("f0 => window.__plannerDebug.flushes > f0", arg=f0, timeout=WAIT_MS)
    stored_after_selected_delete = api.get(server, f"/api/tickets/{ticket_id}")["fields"][
        "success"
    ]["value"]
    assert binary_token not in stored_after_selected_delete


def test_failed_markdown_save_retries_exact_pending_source_without_more_input(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Managed Markdown failed-save retry",
    )["id"]
    _write_ticket_files(server, ticket_id)
    image_token = f"[Image](/files/tickets/{ticket_id}/images/pic.png)"
    body = f"Before\n\n{image_token}"
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, stage="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-file-preview-kind='image'] img").wait_for(
        state="visible", timeout=WAIT_MS
    )

    attempts: list[str] = []

    def fail_first_save(route: Route) -> None:
        post_data_json = route.request.post_data_json
        assert post_data_json is not None
        attempts.append(post_data_json["body"])
        if len(attempts) == 1:
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"error": {"code": "test", "message": "save failed"}}),
            )
            return
        route.continue_()

    page.route(f"**/api/tickets/{ticket_id}/value/success", fail_first_save)
    page.locator(editable).focus()
    page.locator(f"{editable} [data-markdown-caret-guard='after']").last.evaluate(
        """guard => {
            const range = document.createRange();
            range.selectNodeContents(guard);
            range.collapse(false);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.keyboard.type("Retry me exactly.")
    page.locator(editable).blur()
    page.locator(".error-line", has_text="save failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => resolve()))")
    assert len(attempts) == 1
    attempted_source = attempts[0]
    assert attempted_source.endswith("Retry me exactly.")
    assert image_token in attempted_source

    # The failed-save repaint clears surface dirtiness. Focus and blur again without
    # another input; product retry state must resubmit the exact attempted source.
    page.locator(editable).focus()
    page.locator(editable).blur()
    _wait_for_field_text(api, server, ticket_id, "success", "Retry me exactly.")
    assert attempts == [attempted_source, attempted_source]

    page.reload()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-file-preview-kind='image'] img").wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert api.get(server, f"/api/tickets/{ticket_id}")["fields"]["success"][
        "value"
    ] == attempted_source


def test_loaded_preview_proposal_approves_without_edited_body(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Preview proposal approval",
    )["id"]
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Preview proposal ready.",
        ticket_id=ticket_id,
        stdin=body,
    )
    card = f'[data-review-card][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, "#/review", card)
    page.locator(f"{card} [data-file-preview-kind='markdown'] h1").first.wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.wait_for_function(
        "sel => document.querySelector(sel)?.srcdoc.includes('HTML File')",
        arg=f"{card} [data-file-preview-kind='html'] iframe",
        timeout=WAIT_MS,
    )
    approval_payloads: list[JsonObject] = []

    def capture_accept(request: Request) -> None:
        if request.method == "POST" and f"/api/tickets/{ticket_id}/accept/success" in request.url:
            post_data_json = request.post_data_json
            assert post_data_json is not None
            approval_payloads.append(post_data_json)

    page.on("request", capture_accept)
    page.wait_for_function(
        "sel => { const button = document.querySelector(sel); return button && !button.disabled; }",
        arg=f"{card} [data-accept]",
        timeout=WAIT_MS,
    )
    page.click(f"{card} [data-accept]")
    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)
    assert len(approval_payloads) == 1
    assert "edited_body" not in approval_payloads[0]
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    assert ticket["fields"]["success"]["value"] == body
