"""Browser coverage for managed ticket-file previews."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from pathlib import Path

WAIT_MS = 10_000


def _ticket_files_dir(server, ticket_id: str) -> Path:
    return server.db_path.parent / "files" / "tickets" / ticket_id


def _write_ticket_files(server, ticket_id: str) -> None:
    root = _ticket_files_dir(server, ticket_id)
    (root / "notes").mkdir(parents=True)
    (root / "images").mkdir(parents=True)
    (root / "video").mkdir(parents=True)
    (root / "notes" / "space name.md").write_text(
        "# File Notes\n\nRendered **here**.", encoding="utf-8"
    )
    (root / "notes" / "other.md").write_text(
        "# Other Notes\n\nFresh route content.", encoding="utf-8"
    )
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
    (root / "archive.bin").write_bytes(b"download me")


def _expected_hrefs(ticket_id: str) -> dict[str, str]:
    return {
        "Markdown": f"/files/tickets/{ticket_id}/notes/space%20name.md",
        "Image": f"/files/tickets/{ticket_id}/images/pic.png",
        "Video": f"/files/tickets/{ticket_id}/video/demo.mp4",
        "HTML": f"/files/tickets/{ticket_id}/page.html",
        "Binary": f"/files/tickets/{ticket_id}/archive.bin",
        "External": "https://example.com/outside",
    }


def _links(ticket_id: str) -> str:
    return "\n".join(f"[{label}]({href})" for label, href in _expected_hrefs(ticket_id).items())


def _set_fields(server, ticket_id: str, fields: dict, state: str = "dropped") -> None:
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET state = ?, fields = ?, updated_at = 2 WHERE id = ?",
            (state, json.dumps(fields), ticket_id),
        )


def _open_ticket_field(page, field: str) -> None:
    section = page.locator(f'details[data-field="{field}"]').first
    section.evaluate("(node) => { node.open = true; }")
    page.locator(f'details[data-field="{field}"][open]').wait_for(state="attached", timeout=WAIT_MS)


def _wait_for_field_text(api, server, ticket_id: str, field: str, expected_fragment: str) -> str:
    deadline = time.monotonic() + WAIT_MS / 1000
    while time.monotonic() < deadline:
        ticket = api.get(server, f"/api/tickets/{ticket_id}")
        value = ticket["fields"][field]["value"]
        if expected_fragment in value:
            return value
        time.sleep(0.1)
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    value = ticket["fields"][field]["value"]
    assert expected_fragment in value
    return value


def test_preview_hash_route_renders_markdown_and_sandboxes_html(
    server, context_factory, cli
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "File preview route")["id"]
    _write_ticket_files(server, ticket_id)
    page = context_factory().new_page()

    page.goto(
        f"{server.base}/#/preview?source=ticket&ticket={ticket_id}&path=notes%2Fspace%20name.md"
    )
    page.wait_for_selector('[data-file-preview-route] [data-file-preview-kind="markdown"] h1',
                           timeout=WAIT_MS)
    assert page.inner_text('[data-file-preview-route] h1') == "File Notes"

    page.evaluate("window.__previewHashNavigationMarker = 'kept'")
    page.evaluate(
        "ticket => { window.location.hash = "
        "'#/preview?source=ticket&ticket=' + ticket + '&path=notes%2Fother.md'; }",
        ticket_id,
    )
    page.wait_for_function(
        "() => document.querySelector('[data-file-preview-route] h1')"
        "?.textContent === 'Other Notes'",
        timeout=WAIT_MS,
    )
    assert page.inner_text('[data-file-preview-route] h1') == "Other Notes"
    assert page.evaluate("window.__previewHashNavigationMarker") == "kept"

    page.evaluate(
        "ticket => { window.location.hash = "
        "'#/preview?source=ticket&ticket=' + ticket + '&path=page.html'; }",
        ticket_id,
    )
    page.wait_for_selector('[data-file-preview-route] iframe[data-file-preview-html]',
                           timeout=WAIT_MS)
    html_frame = page.frame_locator('[data-file-preview-route] iframe[data-file-preview-html]')
    assert html_frame.locator("h1").inner_text(timeout=WAIT_MS) == "HTML File"
    assert page.evaluate("window.__ticketFileScriptRan === true") is False
    assert page.evaluate("window.__previewHashNavigationMarker") == "kept"


def test_read_only_ticket_and_chat_surfaces_share_file_preview(
    server, context_factory, open_page, cli
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Read-only file previews")["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {
            "value": None,
            "proposal": {"body": body, "proposed_by": "agent", "created_at": 1},
            "user_note": None,
        },
        "plan": {"value": None, "proposal": None, "user_note": None},
        "result": {"value": body, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields)
    with sqlite3.connect(server.db_path) as conn:
        for index, role in enumerate(("human", "assistant", "system", "worker"), start=1):
            conn.execute(
                "INSERT INTO chat_messages (entity_id, turn_id, role, text, created_at) "
                "VALUES (?, NULL, ?, ?, ?)",
                (ticket_id, role, f"{role}: {body}", index),
            )

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )

    _open_ticket_field(page, "success")
    success = page.locator('details[data-field="success"]').first
    for kind in ("markdown", "image", "video", "html", "download", "external"):
        success.locator(f'[data-file-preview-kind="{kind}"]').first.wait_for(
            state="visible",
            timeout=WAIT_MS,
        )
    assert (
        success.locator('[data-file-preview-kind="markdown"] a').first.get_attribute("href")
        == f"#/preview?source=ticket&ticket={ticket_id}&path=notes%2Fspace%20name.md"
    )
    assert (
        success.locator('[data-file-preview-kind="html"] a').first.get_attribute("href")
        == f"#/preview?source=ticket&ticket={ticket_id}&path=page.html"
    )
    assert success.locator('[data-file-preview-kind="markdown"] h1').count() == 0
    assert success.locator('[data-file-preview-kind="html"] iframe').count() == 0
    image = success.locator('[data-file-preview-kind="image"] img')
    image.wait_for(state="visible", timeout=WAIT_MS)
    image_metrics = image.evaluate(
        "(image) => ({ naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight, "
        "width: image.getBoundingClientRect().width, "
        "height: image.getBoundingClientRect().height })"
    )
    assert image_metrics["naturalWidth"] == image_metrics["naturalHeight"] == 64
    assert abs(image_metrics["width"] / image_metrics["height"] - 1) < 0.01
    video = success.locator('[data-file-preview-kind="video"] video')
    assert video.get_attribute("controls") is not None
    assert f"/files/tickets/{ticket_id}/video/demo.mp4" in (video.get_attribute("src") or "")

    _open_ticket_field(page, "approach")
    page.wait_for_selector('[data-field="approach"] [data-file-preview-kind="markdown"]',
                           timeout=WAIT_MS)
    _open_ticket_field(page, "result")
    page.wait_for_selector('[data-field="result"] [data-file-preview-kind="video"]',
                           timeout=WAIT_MS)
    for who in ("you", "planner", "system", "worker"):
        page.wait_for_selector(f'[data-chat-msg="{who}"] [data-file-preview-kind="markdown"]',
                               timeout=WAIT_MS)


def test_editable_markdown_file_links_round_trip_as_raw_markdown(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Editable file links")["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "result": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, state="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )

    editable = '[data-field="success"] .ticket-field-value > .ed'
    _open_ticket_field(page, "success")
    page.locator(f"{editable} a").first.wait_for(state="visible", timeout=WAIT_MS)
    assert page.locator(f"{editable} [data-file-preview]").count() == 0
    for label, href in _expected_hrefs(ticket_id).items():
        assert page.locator(f"{editable} a", has_text=label).first.get_attribute("href") == href

    edited_text = "Edited during preview regression."
    page.locator(editable).focus()
    page.locator(editable).evaluate(
        """(node) => {
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(node);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.keyboard.type(edited_text)
    page.locator(editable).blur()
    stored_body = _wait_for_field_text(api, server, ticket_id, "success", edited_text)
    for label, href in _expected_hrefs(ticket_id).items():
        assert f"[{label}]({href})" in stored_body
    assert stored_body.rstrip().endswith(edited_text)
    assert "data-file-preview" not in stored_body
    assert "<iframe" not in stored_body
    assert "<video" not in stored_body
    assert "<img" not in stored_body

    page.reload()
    page.wait_for_selector(f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
                           timeout=WAIT_MS)
    _open_ticket_field(page, "success")
    page.locator(f"{editable} a").first.wait_for(state="visible", timeout=WAIT_MS)
    assert page.locator(f"{editable} [data-file-preview]").count() == 0
    for label, href in _expected_hrefs(ticket_id).items():
        assert page.locator(f"{editable} a", has_text=label).first.get_attribute("href") == href
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    assert ticket["fields"]["success"]["value"] == stored_body
