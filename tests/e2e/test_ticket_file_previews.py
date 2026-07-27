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
    section.evaluate("(node) => { node.open = true; }")
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
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_ticket_field(ticket_page, "success")
    embedded_markdown = ticket_page.locator('[data-file-preview-kind="markdown"]').first
    embedded_markdown.locator("h1", has_text="File Notes").wait_for(
        state="visible", timeout=WAIT_MS
    )
    markdown_action = embedded_markdown.locator("a.file-preview-link").first
    expected_markdown_url = (
        f"{server.base}/#/preview?source=ticket&ticket={ticket_id}"
        "&path=notes%2Fspace%20name.md"
    )
    # A preview opens here rather than in a new tab, so following it is a hash navigation
    # in this same page: the page that was showing the ticket now shows the preview. This
    # marker is set on the ticket before the click and read back after it, because the
    # arrival assertions below would read the same either way if the app had reloaded.
    ticket_page.evaluate("window.__previewRouteNavigationMarker = 'kept'")
    markdown_action.click()
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
        '.file-preview--inline[data-file-preview-kind="markdown"] a.file-preview-link'
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
    html_action = embedded_preview.locator("a.file-preview-link", has_text="Open page.html")
    expected_html_url = f"{server.base}/#/preview?source=ticket&ticket={ticket_id}&path=page.html"
    html_action.click()
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
        f"#/ticket/{ticket_id}",
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

    # The second surface is the preview route, which opens here rather than in a new tab.
    embedded_preview.locator("a.file-preview-link", has_text="Open interactive.html").click()
    expected_preview_url = (
        f"{server.base}/#/preview?source=ticket&ticket={ticket_id}&path=interactive.html"
    )
    ticket_page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_preview_url,
        timeout=WAIT_MS,
    )
    full_iframe = ticket_page.locator(
        "[data-file-preview-route] iframe[data-file-preview-html]"
    )
    full_iframe.wait_for(state="visible", timeout=WAIT_MS)
    assert full_iframe.get_attribute("sandbox") == "allow-scripts"
    assert full_iframe.get_attribute("allow") is None
    _exercise_interactive_workspace_rows(
        ticket_page.frame_locator("[data-file-preview-route] iframe[data-file-preview-html]")
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
        f"#/ticket/{ticket_id}",
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

    # The second surface is the preview route, which opens here rather than in a new tab.
    embedded_preview.locator("a.file-preview-link", has_text="Open index.html").click()
    expected_preview_url = (
        f"{server.base}/#/preview?source=ticket&ticket={ticket_id}&path=previews%2Findex.html"
    )
    ticket_page.wait_for_function(
        "expected => window.location.href === expected",
        arg=expected_preview_url,
        timeout=WAIT_MS,
    )
    full_iframe = ticket_page.locator(
        "[data-file-preview-route] iframe[data-file-preview-html]"
    )
    full_iframe.wait_for(state="visible", timeout=WAIT_MS)
    assert full_iframe.get_attribute("sandbox") == "allow-scripts"
    assert full_iframe.get_attribute("allow") is None
    _assert_managed_html_references_render(
        ticket_page.frame_locator("[data-file-preview-route] iframe[data-file-preview-html]")
    )


def test_markdown_file_preview_has_component_owned_max_height(
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
        "Bounded Markdown preview",
    )["id"]
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
    )
    _open_ticket_field(page, "success")

    short_preview = page.locator('[data-file-preview-kind="markdown"]', has_text="Short").first
    long_preview = page.locator('[data-file-preview-kind="markdown"]', has_text="Long").first
    short_preview.locator("h1", has_text="Short").wait_for(state="visible", timeout=WAIT_MS)
    long_preview.locator("h1", has_text="Long").wait_for(state="visible", timeout=WAIT_MS)

    metrics_script = """node => ({
            clientHeight: node.clientHeight,
            scrollHeight: node.scrollHeight,
            maxHeight: getComputedStyle(node).maxHeight,
            overflowY: getComputedStyle(node).overflowY,
        })"""
    short_metrics = short_preview.locator(".file-preview-document-body").evaluate(metrics_script)
    long_metrics = long_preview.locator(".file-preview-document-body").evaluate(metrics_script)

    assert short_metrics["scrollHeight"] <= short_metrics["clientHeight"] + 1
    assert long_metrics["scrollHeight"] > long_metrics["clientHeight"]
    assert long_metrics["maxHeight"] != "none"
    assert long_metrics["overflowY"] == "auto"


def test_read_only_ticket_surfaces_share_file_preview(
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
        "Read-only file previews",
    )["id"]
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
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )

    _open_ticket_field(page, "success")
    success = page.locator('details[data-field="success"]').first
    for kind in ("markdown", "image", "video", "html", "download"):
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
    assert embedded_html_iframe.get_attribute("sandbox") == "allow-scripts"
    assert embedded_html_iframe.get_attribute("allow") is None
    html_action = success.locator('[data-file-preview-kind="html"] a.file-preview-link').first
    # Opened here, not in a new tab: one thing at a time, and back is the way out.
    assert html_action.get_attribute("target") is None
    assert html_action.get_attribute("rel") == "noopener noreferrer"
    assert (
        html_action.get_attribute("href")
        == f"#/preview?source=ticket&ticket={ticket_id}&path=page.html"
    )
    assert success.locator('[data-file-preview-kind="download"] a[download]').count() == 1
    # An off-site link names no managed file, so the preview system leaves it alone.
    assert success.locator('[data-file-preview-kind="external"]').count() == 0
    outside_link = success.locator('a[href="https://example.com/outside"]')
    assert outside_link.count() == 1
    assert outside_link.evaluate("anchor => anchor.closest('[data-file-preview]') === null")
    self_link_line = success.locator(
        '.file-preview--inline[data-file-preview-kind="markdown"] a.file-preview-link'
    )
    assert self_link_line.count() == 1
    assert self_link_line.inner_text() == "Open space name.md"
    assert self_link_line.locator("h1").count() == 0
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
def test_normal_editable_ticket_field_renders_file_previews_at_rest(
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
        "Normal field previews",
    )["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
    fields = {
        "kickoff": {"value": body, "proposal": None, "user_note": None},
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
    _set_fields(server, ticket_id, fields, stage="needs_approach")
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET recap = ? WHERE id = ?",
            (body, ticket_id),
        )
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
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
    page.click('details[data-field="kickoff"] .disclosure-summary')
    page.wait_for_selector('details[data-field="kickoff"][open]', timeout=WAIT_MS)
    page.locator(
        'details[data-field="kickoff"] [data-file-preview-kind="image"]'
    ).first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator('[data-recap] [data-file-preview-kind="html"]').first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator(
        '[data-field="approach"] .approval-draft [data-file-preview-kind="download"]'
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
        "Editable file links",
    )["id"]
    _write_ticket_files(server, ticket_id)
    reference_token = "[Reference notes][notes-ref]"
    reference_definition = (
        f"[notes-ref]: /files/tickets/{ticket_id}/notes/space%20name.md \"Reference title\""
    )
    body = f"{_links(ticket_id)}\n\n{reference_token}\n\n{reference_definition}"
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
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
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
    assert reference_token in stored_body
    assert reference_definition in stored_body
    assert stored_body.rstrip().endswith(reference_definition)
    assert edited_text in stored_body
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
        "Editable preview actions",
    )["id"]
    _write_ticket_files(server, ticket_id)
    body = _links(ticket_id)
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
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
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
    html_action = page.locator(
        f"{editable} [data-file-preview-kind='html'] a.file-preview-link"
    ).first
    # The preview opens here, so this action takes the focused editor off screen and back
    # again — the whole round trip must still write nothing.
    html_action.click()
    preview_hash = f"#/preview?source=ticket&ticket={ticket_id}&path=page.html"
    page.wait_for_function(
        "expected => window.location.hash === expected",
        arg=preview_hash,
        timeout=WAIT_MS,
    )
    page.locator("[data-file-preview-route] iframe[data-file-preview-html]").wait_for(
        state="visible", timeout=WAIT_MS
    )
    page.go_back()
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
    )
    _open_ticket_field(page, "success")
    page.locator(f"{editable} [data-file-preview-kind='html'] iframe").first.wait_for(
        state="visible",
        timeout=WAIT_MS,
    )
    page.locator(editable).focus()

    # The off-site link was never claimed, so it is ordinary editable text, not a preview.
    outside_link = page.locator(f'{editable} a[href="https://example.com/outside"]')
    assert outside_link.count() == 1
    assert outside_link.evaluate("anchor => anchor.closest('[data-file-preview]') === null")

    with page.expect_download() as download_info:
        page.locator(f"{editable} [data-file-preview-kind='download'] a[download]").first.click()
    assert download_info.value.suggested_filename == "archive.bin"

    page.locator(editable).blur()
    assert writes == []
    ticket = api.get(server, f"/api/tickets/{ticket_id}")
    assert ticket["fields"]["success"]["value"] == body


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
        f"#/ticket/{ticket_id}",
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


def test_editable_preview_deletion_unmounts_pending_fetch_and_clears_iframe(
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
        "Preview cleanup",
    )["id"]
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
    _set_fields(server, ticket_id, fields, stage="needs_approach")
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
    # This test's coverage IS the loading/fetch-abort lifecycle: the intercepted slow.md
    # fetch stays pending as "Loading preview...", and deleting its slot must abort that
    # in-flight fetch. Open on a NEUTRAL route first so the change stream opens — and runs
    # its one reconciling round of refetches — while nothing is showing the ticket. Then
    # navigate to the ticket by hash: its slow preview mounts exactly once and stays
    # pending until the Delete below, so every __previewAborts entry is this test's doing.
    page = open_page(
        context,
        server,
        "#/day",
        "[data-day-overview]",
    )
    page.evaluate("id => { window.location.hash = '#/ticket/' + id; }", ticket_id)
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]', timeout=WAIT_MS
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

    # Capture the HTML iframe immediately before deleting it — after the slow-slot deletion
    # and only now that it is confirmed connected with its loaded srcdoc — so the second
    # Delete is provably the operation that disconnects and clears this exact frame.
    page.wait_for_function(
        "sel => document.querySelector(sel)?.srcdoc.includes('HTML File')",
        arg=f"{editable} [data-file-preview-kind='html'] iframe",
        timeout=WAIT_MS,
    )
    page.evaluate(
        "sel => { window.__removedPreviewFrame = document.querySelector(sel); }",
        f"{editable} [data-file-preview-kind='html'] iframe",
    )

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
        "Moving atomic preview",
    )["id"]
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
    _set_fields(server, ticket_id, fields, stage="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
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


def test_editable_same_source_owner_update_preserves_pristine_preview_and_resets_dirty_dom(
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
        "Managed Markdown same-source reset",
    )["id"]
    _write_ticket_files(server, ticket_id)
    image_token = f"[Image](/files/tickets/{ticket_id}/images/pic.png)"
    fields = {
        "success": {"value": image_token, "proposal": None, "user_note": None},
        "approach": {"value": None, "proposal": None, "user_note": None},
        "plan": {"value": None, "proposal": None, "user_note": None},
        "implementation": {"value": None, "proposal": None, "user_note": None},
        "closeout": {"value": None, "proposal": None, "user_note": None},
    }
    _set_fields(server, ticket_id, fields, stage="needs_approach")
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    editable = '[data-field="success"] .ticket-field-value [data-markdown-inline-edit]'
    slot_selector = f"{editable} [data-markdown-source-token='{image_token}']"
    _open_ticket_field(page, "success")
    page.locator(f"{slot_selector} img").wait_for(state="visible", timeout=WAIT_MS)
    page.evaluate(
        "sel => { window.__managedMarkdownStableSlot = document.querySelector(sel); }",
        slot_selector,
    )

    # Escape asks the owner to update from the same source. With no input, that is an
    # identity-preserving no-op.
    page.locator(editable).focus()
    page.keyboard.press("Escape")
    page.wait_for_timeout(50)
    assert page.evaluate(
        "sel => document.querySelector(sel) === window.__managedMarkdownStableSlot",
        slot_selector,
    )

    # A browser edit that is then restored to source-equivalent DOM is still dirty.
    # The next explicit owner update must repaint canonical generated DOM.
    page.locator(editable).focus()
    page.locator(editable).evaluate(
        """node => {
            const temporary = document.createTextNode("temporary");
            node.appendChild(temporary);
            node.dispatchEvent(new InputEvent("input", { bubbles: true }));
            temporary.remove();
            node.dispatchEvent(new InputEvent("input", { bubbles: true }));
        }"""
    )
    page.keyboard.press("Escape")
    page.locator(f"{slot_selector} img").wait_for(state="visible", timeout=WAIT_MS)
    assert page.evaluate(
        "sel => document.querySelector(sel) !== window.__managedMarkdownStableSlot "
        "&& !window.__managedMarkdownStableSlot.isConnected",
        slot_selector,
    )


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
        f"#/ticket/{ticket_id}",
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
        "--body-file",
        "-",
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
