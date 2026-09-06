"""Browser coverage for managed ticket-file previews."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Page,
    Request,
    Route,
)
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

from planner.core.db import connect
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap

WAIT_MS = 10_000


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


def _settle_success(
    server: ServerHandle, ticket_id: str, body: str, *, dropped: bool = False
) -> None:
    """Seed saved Markdown through the same proposal/accept writers as ordinary work."""
    with closing(connect(str(server.db_path))) as conn:
        ticket = tickets_data.file_current_proposal_with_recap(
            conn, ticket_id, body=body, recap="Preview content ready.", actor="worker", now=2
        )
        if ticket.pending_proposal is not None:
            ticket = tickets_data.accept_proposal(
                conn,
                ticket_id,
                field="success",
                actor="human",
                now=2,
                next_ceiling="none",
                at_cap=AtCap.propose,
            )
        assert ticket.stage == "needs_approach"
        assert ticket.field_values["success"] == body
        assert ticket.pending_proposal is None
        if dropped:
            tickets_data.drop_ticket(conn, ticket_id, actor="human", now=2)


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


def _wait_for_field_text(
    api: ApiHelper, server: ServerHandle, ticket_id: str, field: str, expected_fragment: str
) -> str:
    deadline = time.monotonic() + WAIT_MS / 1000
    while time.monotonic() < deadline:
        ticket = api.get(server, f"/api/tickets/{ticket_id}")
        value: str = ticket["field_values"].get(field)
        if expected_fragment in value:
            return value
        time.sleep(0.1)
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    value = ticket["field_values"].get(field)
    assert expected_fragment in value
    return value


def test_html_artifact_interacts_loads_sibling_assets_and_refreshes_in_place(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """The real sandbox resolves managed assets and refreshes over its Ticket.

    Mock DOM preparation and API bytes cannot prove browser sandbox execution,
    sibling URL resolution, or that changed managed content reaches the open frame.
    """
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
    document = preview_directory / "index.html"
    document.write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head>'
        '<body><h1>Initial artifact</h1><img src="../images/pic.png" alt="Sibling image">'
        "<button onclick=\"this.textContent='Clicked'\">Try interaction</button>"
        "</body></html>",
        encoding="utf-8",
    )
    _settle_success(
        server,
        ticket_id,
        f"[HTML](/files/tickets/{ticket_id}/previews/index.html)",
        dropped=True,
    )
    ticket_selector = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/workspace/{ticket_id}", ticket_selector)
    _open_ticket_field(page, "success")
    page.evaluate("window.__artifactInPlaceMarker = 'kept'")
    page.locator("a.file-preview-link:visible", has_text="Open index.html").first.click()
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

    document.write_text("<h1>Refreshed artifact</h1>", encoding="utf-8")
    artifact.locator("[data-ticket-artifact-refresh]").click(timeout=WAIT_MS)
    frame.get_by_role("heading", name="Refreshed artifact").wait_for(timeout=WAIT_MS)
    assert page.url == artifact_address
    assert page.evaluate("window.__artifactInPlaceMarker") == "kept"
    assert page.locator(ticket_selector).count() == 1
    assert artifact.is_visible()


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
        f"Intro\n\n{markdown_token}\n\n{image_token}\n\n{binary_token}\n\n{entity_token}\n\nOutro"
    )
    _settle_success(server, ticket_id, body)
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

    image_slot = page.locator(f"{editable} [data-markdown-source-token='{image_token}']").first
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
    stored_after_delete = api.get(server, f"/api/tickets/{ticket_id}")["field_values"].get(
        "success"
    )
    assert markdown_token not in stored_after_delete
    assert image_token in stored_after_delete

    page.reload()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    image_slot = page.locator(f"{editable} [data-markdown-source-token='{image_token}']").first
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
    stored_after_backspace = api.get(server, f"/api/tickets/{ticket_id}")["field_values"].get(
        "success"
    )
    assert image_token not in stored_after_backspace
    assert binary_token in stored_after_backspace

    page.reload()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    binary_slot = page.locator(f"{editable} [data-markdown-source-token='{binary_token}']").first
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
    stored_after_selected_delete = api.get(server, f"/api/tickets/{ticket_id}")["field_values"].get(
        "success"
    )
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
    _settle_success(server, ticket_id, body)
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
    page.locator(".error-line", has_text="save failed").wait_for(state="visible", timeout=WAIT_MS)
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
    assert (
        api.get(server, f"/api/tickets/{ticket_id}")["field_values"].get("success")
        == attempted_source
    )


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
    assert ticket["field_values"].get("success") == body
    assert ticket["pending_proposal"] is None
