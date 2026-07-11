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
    page.wait_for_selector(
        '[data-file-preview-route] [data-file-preview-kind="markdown"] h1', timeout=WAIT_MS
    )
    assert page.inner_text("[data-file-preview-route] h1") == "File Notes"

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
    assert page.inner_text("[data-file-preview-route] h1") == "Other Notes"
    assert page.evaluate("window.__previewHashNavigationMarker") == "kept"

    page.evaluate(
        "ticket => { window.location.hash = "
        "'#/preview?source=ticket&ticket=' + ticket + '&path=page.html'; }",
        ticket_id,
    )
    page.wait_for_selector(
        "[data-file-preview-route] iframe[data-file-preview-html]", timeout=WAIT_MS
    )
    html_frame = page.frame_locator("[data-file-preview-route] iframe[data-file-preview-html]")
    full_html_iframe = page.locator("[data-file-preview-route] iframe[data-file-preview-html]")
    assert full_html_iframe.get_attribute("sandbox") == ""
    assert full_html_iframe.get_attribute("allow") is None
    assert html_frame.locator("h1").inner_text(timeout=WAIT_MS) == "HTML File"
    assert page.evaluate("window.__ticketFileScriptRan === true") is False
    assert page.evaluate("window.__previewHashNavigationMarker") == "kept"


def test_markdown_file_preview_has_component_owned_max_height(
    server, context_factory, open_page, cli
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Bounded Markdown preview")["id"]
    root = _ticket_files_dir(server, ticket_id) / "notes"
    root.mkdir(parents=True)
    (root / "short.md").write_text("# Short\n\nOne paragraph.", encoding="utf-8")
    (root / "long.md").write_text(
        "# Long\n\n" + "\n\n".join(f"Paragraph {index}" for index in range(200)),
        encoding="utf-8",
    )
    fields = {
        "success": {
            "value": (
                f"[Short](/files/tickets/{ticket_id}/notes/short.md)\n\n"
                f"[Long](/files/tickets/{ticket_id}/notes/long.md)"
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
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )
    _open_ticket_field(page, "success")

    short_preview = page.locator('[data-file-preview-kind="markdown"]', has_text="Short").first
    long_preview = page.locator('[data-file-preview-kind="markdown"]', has_text="Long").first
    short_preview.locator("h1", has_text="Short").wait_for(state="visible", timeout=WAIT_MS)
    long_preview.locator("h1", has_text="Long").wait_for(state="visible", timeout=WAIT_MS)

    short_metrics = short_preview.evaluate(
        """node => ({
            clientHeight: node.clientHeight,
            scrollHeight: node.scrollHeight,
            maxHeight: getComputedStyle(node).maxHeight,
            overflowY: getComputedStyle(node).overflowY,
        })"""
    )
    long_metrics = long_preview.evaluate(
        """node => ({
            clientHeight: node.clientHeight,
            scrollHeight: node.scrollHeight,
            maxHeight: getComputedStyle(node).maxHeight,
            overflowY: getComputedStyle(node).overflowY,
        })"""
    )

    assert short_metrics["scrollHeight"] <= short_metrics["clientHeight"] + 1
    assert long_metrics["scrollHeight"] > long_metrics["clientHeight"]
    assert long_metrics["maxHeight"] != "none"
    assert long_metrics["overflowY"] == "auto"


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
        "implementation": {"value": body, "proposal": None, "user_note": None},
        "closeout": {"value": body, "proposal": None, "user_note": None},
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
    assert (
        success.locator('[data-file-preview-kind="markdown"] h1').first.inner_text() == "File Notes"
    )
    nested_heading = success.locator(
        '[data-file-preview-kind="markdown"] h1', has_text="Other Notes"
    )
    nested_heading.wait_for(state="visible", timeout=WAIT_MS)
    assert nested_heading.count() == 1
    assert success.locator('[data-file-preview-kind="html"] iframe').count() == 1
    embedded_html_iframe = success.locator('[data-file-preview-kind="html"] iframe').first
    assert embedded_html_iframe.get_attribute("sandbox") == ""
    assert embedded_html_iframe.get_attribute("allow") is None
    html_action = success.locator('[data-file-preview-kind="html"] a.button').first
    assert html_action.get_attribute("target") == "_blank"
    assert html_action.get_attribute("rel") == "noopener noreferrer"
    assert (
        html_action.get_attribute("href")
        == f"#/preview?source=ticket&ticket={ticket_id}&path=page.html"
    )
    assert success.locator('[data-file-preview-kind="download"] a[download]').count() == 1
    assert (
        success.locator('[data-file-preview-kind="external"]', has_text="example.com").count() == 1
    )
    self_link_card = success.locator(
        '[data-file-preview-kind="markdown"] article.file-preview-card'
    )
    assert self_link_card.count() == 1
    assert "space name.md" in self_link_card.inner_text()
    assert self_link_card.locator("h1").count() == 0
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
    page.wait_for_selector(
        '[data-field="approach"] [data-file-preview-kind="markdown"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "implementation")
    page.wait_for_selector(
        '[data-field="implementation"] [data-file-preview-kind="video"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "closeout")
    page.wait_for_selector(
        '[data-field="closeout"] [data-file-preview-kind="html"]', timeout=WAIT_MS
    )
    for who in ("you", "planner", "system", "worker"):
        page.wait_for_selector(
            f'[data-chat-msg="{who}"] [data-file-preview-kind="markdown"]', timeout=WAIT_MS
        )


def test_normal_editable_ticket_field_renders_file_previews_at_rest(
    server, context_factory, open_page, cli
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Normal field previews")["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    fields = {
        "success": {"value": body, "proposal": None, "user_note": body},
        "approach": {
            "value": None,
            "proposal": {"body": body, "proposed_by": "agent", "created_at": 1},
            "user_note": None,
        },
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {
            "value": None,
            "proposal": {"body": body, "proposed_by": "agent", "created_at": 2},
            "user_note": None,
        },
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, state="needs_approach")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET user_note = ?, recap = ? WHERE id = ?",
            (body, body, ticket_id),
        )
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )

    _open_ticket_field(page, "success")
    field = page.locator('[data-field="success"] .ticket-field-value').first
    field.locator('[data-file-preview-kind="markdown"]').first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    direct_anchor = f'a[href="/files/tickets/{ticket_id}/notes/space%20name.md"]'
    assert field.locator(direct_anchor).count() == 0
    # The user note is a collapsed stage row by default; open it to render its body.
    page.click("[data-user-note] .disclosure-summary")
    page.wait_for_selector("[data-user-note] details[open]", timeout=WAIT_MS)
    page.locator('[data-user-note] [data-file-preview-kind="image"]').first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator('[data-recap] [data-file-preview-kind="html"]').first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator(
        '[data-field="approach"] .approval-draft [data-file-preview-kind="external"]'
    ).first.wait_for(state="visible", timeout=WAIT_MS)
    page.locator(
        '[data-field="success"] [data-content-section="note"] [data-file-preview-kind="image"]'
    ).first.wait_for(state="visible", timeout=WAIT_MS)
    _open_ticket_field(page, "implementation")
    result_proposal = page.locator(
        '[data-field="implementation"] [data-approval-block][data-mode="proposal"]'
    ).first
    result_proposal.locator('[data-file-preview-kind="image"]').first.wait_for(
        state="visible", timeout=WAIT_MS
    )
    assert result_proposal.locator("textarea").count() == 0
    assert result_proposal.locator("[data-markdown-edit]").count() == 0
    assert (
        result_proposal.locator("[data-markdown-inline-edit]").get_attribute("contenteditable")
        == "true"
    )


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
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, state="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )

    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-file-preview-kind='markdown']").first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    assert page.locator(f"{editable}").get_attribute("contenteditable") == "true"
    assert page.locator(f"{editable} [data-markdown-edit]").count() == 0
    assert page.locator(f"{editable} [data-markdown-source-editor]").count() == 0

    edited_text = "Edited during preview regression."
    page.locator(editable).focus()
    assert page.locator(f"{editable} [data-file-preview-kind='markdown']").count() > 0
    page.locator(f"{editable} [data-markdown-caret-guard='after']").last.evaluate(
        """(guard) => {
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(guard);
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
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-file-preview-kind='markdown']").first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    assert page.locator(f"{editable}").get_attribute("contenteditable") == "true"
    assert page.locator(f"{editable} [data-markdown-edit]").count() == 0
    assert page.locator(f"{editable} [data-markdown-source-editor]").count() == 0
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    assert ticket["fields"]["success"]["value"] == stored_body


def test_editable_markdown_preview_focus_noop_and_actions_do_not_persist_generated_dom(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Editable preview actions")["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, state="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )

    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-file-preview-kind='markdown'] h1").first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator(f"{editable} [data-file-preview-kind='html'] iframe").first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    writes: list[str] = []
    page.on(
        "request",
        lambda request: (
            writes.append(f"{request.method} {request.url}")
            if request.method in {"PATCH", "POST", "PUT"}
            else None
        ),
    )

    page.locator(editable).focus()
    html_action = page.locator(f"{editable} [data-file-preview-kind='html'] a.button").first
    with page.expect_popup() as popup_info:
        html_action.click()
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded", timeout=WAIT_MS)
    assert f"#/preview?source=ticket&ticket={ticket_id}&path=page.html" in popup.url
    popup.close()

    external_action = page.locator(f"{editable} [data-file-preview-kind='external'] a.button").first
    with page.expect_popup() as external_popup_info:
        external_action.click()
    external_popup = external_popup_info.value
    external_popup.wait_for_timeout(100)
    assert external_popup.url.startswith("https://example.com/outside")
    external_popup.close()

    with page.expect_download() as download_info:
        page.locator(f"{editable} [data-file-preview-kind='download'] a[download]").first.click()
    assert download_info.value.suggested_filename == "archive.bin"

    page.locator(editable).blur()
    assert writes == []
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    assert ticket["fields"]["success"]["value"] == body


def test_editable_markdown_atomic_preview_adjacent_edits_and_selected_deletion(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Atomic preview editing")["id"]
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
    _set_fields(server, ticket_id, fields, state="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
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


def test_editable_preview_deletion_unmounts_pending_fetch_and_clears_iframe(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Preview cleanup")["id"]
    _write_ticket_files(server, ticket_id)
    slow_token = f"[Slow](/files/tickets/{ticket_id}/notes/slow.md)"
    html_token = f"[HTML](/files/tickets/{ticket_id}/page.html)"
    body = f"{slow_token}\n\n{html_token}"
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, state="needs_approach")
    context = context_factory()
    context.add_init_script(
        """(() => {
            const originalFetch = window.fetch.bind(window);
            window.__previewAborts = [];
            window.fetch = (input, init = {}) => {
                const url = typeof input === "string" ? input : input.url;
                if (!url.includes("/notes/slow.md")) return originalFetch(input, init);
                return new Promise((resolve, reject) => {
                    const abort = () => {
                        window.__previewAborts.push(url);
                        reject(new DOMException("Aborted", "AbortError"));
                    };
                    if (init.signal?.aborted) abort();
                    else init.signal?.addEventListener("abort", abort, { once: true });
                });
            };
        })()"""
    )
    page = open_page(
        context,
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )
    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    _open_ticket_field(page, "success")
    slow_slot = page.locator(f"{editable} [data-markdown-source-token='{slow_token}']").first
    slow_slot.locator("text=Loading preview...").wait_for(state="visible", timeout=WAIT_MS)
    html_slot = page.locator(f"{editable} [data-markdown-source-token='{html_token}']").first
    html_frame = html_slot.locator("iframe")
    html_frame.wait_for(state="visible", timeout=WAIT_MS)
    page.wait_for_function(
        "sel => document.querySelector(sel)?.srcdoc.includes('HTML File')",
        arg=f"{editable} [data-file-preview-kind='html'] iframe",
        timeout=WAIT_MS,
    )
    page.evaluate(
        "sel => { window.__removedPreviewFrame = document.querySelector(sel); }",
        f"{editable} [data-file-preview-kind='html'] iframe",
    )

    page.locator(editable).focus()
    slow_slot.evaluate(
        """slot => {
            const range = document.createRange();
            range.selectNode(slot);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.keyboard.press("Delete")
    page.wait_for_function("() => window.__previewAborts.length === 1", timeout=WAIT_MS)

    html_slot.evaluate(
        """slot => {
            const range = document.createRange();
            range.selectNode(slot);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.keyboard.press("Delete")
    page.wait_for_function(
        "() => !window.__removedPreviewFrame.isConnected "
        "&& window.__removedPreviewFrame.srcdoc === ''",
        timeout=WAIT_MS,
    )
    assert page.locator(f"{editable} [data-markdown-atomic-slot='true']").count() == 0
    assert page.evaluate("() => window.__previewAborts.length") == 1
    assert page.evaluate("() => window.__removedPreviewFrame.srcdoc") == ""


def test_editing_that_moves_atomic_slot_keeps_preview_mounted(
    server, context_factory, open_page, cli
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Moving atomic preview")["id"]
    _write_ticket_files(server, ticket_id)
    image_token = f"[Image](/files/tickets/{ticket_id}/images/pic.png)"
    body = f"Before {image_token} after"
    fields = {
        "success": {"value": body, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, state="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
        settled=False,
    )
    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    _open_ticket_field(page, "success")
    slot = page.locator(f"{editable} [data-markdown-source-token='{image_token}']").first
    image = slot.locator('[data-file-preview-kind="image"] img')
    image.wait_for(state="visible", timeout=WAIT_MS)
    page.evaluate(
        "sel => { window.__atomicPreviewBeforeEdit = document.querySelector(sel); }",
        f"{editable} [data-markdown-source-token='{image_token}']",
    )

    page.locator(editable).focus()
    for _ in range(3):
        slot.evaluate(
            """preview => {
                const range = document.createRange();
                range.setStartBefore(preview);
                range.collapse(true);
                const selection = getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
            }"""
        )
        page.keyboard.press("Enter")
        page.wait_for_timeout(50)

        slot_state = page.evaluate(
            """sel => {
                const current = document.querySelector(sel);
                return {
                    sameElement: current === window.__atomicPreviewBeforeEdit,
                    connected: !!current?.isConnected,
                    stillInsideEditor: !!current?.closest('[data-markdown-inline-edit]'),
                    previewCount: current?.querySelectorAll('[data-file-preview]').length ?? -1,
                };
            }""",
            f"{editable} [data-markdown-source-token='{image_token}']",
        )
        assert slot.count() == 1
        assert image.count() == 1, slot_state
        assert image.is_visible()

    page.locator(editable).click(position={"x": 8, "y": 8})
    page.wait_for_timeout(50)
    assert image.is_visible()


def test_loaded_preview_proposal_approves_without_edited_body(
    server, context_factory, open_page, cli, api
) -> None:
    ticket_id = cli(server, "ticket", "create", "--title", "Preview proposal approval")["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Preview proposal ready.",
        ticket_id=ticket_id,
        stdin=body,
    )
    card = f'[data-review-card][data-entity-id="{ticket_id}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)
    page.locator(f"{card} [data-file-preview-kind='markdown'] h1").first.wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.wait_for_function(
        "sel => document.querySelector(sel)?.srcdoc.includes('HTML File')",
        arg=f"{card} [data-file-preview-kind='html'] iframe",
        timeout=WAIT_MS,
    )
    approval_payloads: list[dict] = []

    def capture_accept(request) -> None:
        if request.method == "POST" and f"/api/tickets/{ticket_id}/accept/success" in request.url:
            approval_payloads.append(request.post_data_json)

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
