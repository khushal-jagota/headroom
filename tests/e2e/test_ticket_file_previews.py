"""Browser coverage for managed ticket-file previews."""

from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    Page,
)
from tests.e2e.harness import JsonObject, ServerHandle
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal

from planner.core.db import connect
from planner.tickets import data as tickets_data

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


def _settle_success(server: ServerHandle, ticket_id: str, body: str) -> None:
    """Seed saved Markdown through the same proposal/accept writers as ordinary work."""
    with closing(connect(str(server.db_path))) as conn:
        ticket = tickets_data.file_current_proposal(
            conn,
            ticket_id,
            body=body,
            principal=ticket_principal(ticket_id),
            now=2,
        )
        if ticket.pending_proposal is not None:
            ticket = tickets_data.accept_proposal(
                conn,
                ticket_id,
                field="success_condition",
                principal=OWNER_PRINCIPAL,
                now=2,
                next_ceiling="none",
                next_holder=OWNER_PRINCIPAL,
            )
        assert ticket.stage == "needs_what_changes"
        assert ticket.field_values["success_condition"] == body
        assert ticket.pending_proposal is None


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
    )
    ticket_selector = f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
    page = open_page(context_factory(), server, f"#/workspace/{ticket_id}", ticket_selector)
    _open_ticket_field(page, "success_condition")
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
    _settle_success(
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
    _open_ticket_field(page, "success_condition")
    video = page.locator("video").first
    video.wait_for(timeout=WAIT_MS)
    page.wait_for_timeout(1500)

    # Nothing has been asked for: the reader has not asked for it.
    assert asked_for_the_clip == [], asked_for_the_clip
    # The reader can still see which file the player is.
    name = page.locator('[data-file-preview-kind="video"] .file-preview-name-link').first
    assert name.is_visible(), "a player with no frame must still name its file"
    assert "clip.mp4" in name.inner_text()

    # It plays on the first action.
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
