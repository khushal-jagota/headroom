"""One browser proof of automatic transfer, early Send, and native Range playback.

HTTP tests cannot prove that a browser's media element consumes streamed files.
Reuses the signal server and browser context; only the existing backend is fake.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, Response, Route, expect
from tests.e2e.harness import REPO_ROOT, WAIT_MS, JsonObject, ServerHandle
from tests.e2e.test_ticket_conversation_shared_behaviors import (
    _create_ticket_conversation,
    _open_ticket_conversation,
)
from tests.support.server_fixtures import signal_server as signal_server


def test_video_upload_early_send_and_native_range_playback(
    signal_server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    video = tmp_path / "screen-recording.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x96:rate=10",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(video),
        ],
        check=True,
        timeout=10,
    )
    server = signal_server
    conversation_id = "e2e-video-upload"
    ticket_id = _create_ticket_conversation(
        server,
        cli,
        conversation_id=conversation_id,
        title="Automatic video attachment",
    )
    response = httpx.post(server.base + f"/api/test/signals/{conversation_id}/finish", timeout=10)
    assert response.is_success, response.text
    context = context_factory()
    page = _open_ticket_conversation(
        server,
        ticket_id,
        open_page,
        context,
        "[data-conversation-input]",
    )
    page.locator('[data-conversation-lens-choice="full"]').click()
    browser_ranges: list[Response] = []

    def record_range(response: Response) -> None:
        if "/files/" in response.url and response.request.headers.get("range"):
            browser_ranges.append(response)

    page.on("response", record_range)
    held_transfers: list[Route] = []
    page.route("**/video-uploads/*/bytes", lambda route: held_transfers.append(route))
    page.locator("[data-conversation-file-input]").set_input_files(video)
    expect(page.locator("[data-conversation-attachments]")).to_contain_text(
        video.name, timeout=WAIT_MS
    )
    editor = page.locator("[data-conversation-input]")
    expect(editor).to_be_enabled(timeout=WAIT_MS)
    editor.fill("Show this recording to the agent")
    expect(page.locator("[data-conversation-send]")).to_be_enabled(timeout=WAIT_MS)
    page.locator("[data-conversation-send]").click()
    pending = page.locator("[data-local-video-send]")
    expect(pending).to_contain_text("Show this recording to the agent", timeout=WAIT_MS)
    expect(pending).to_contain_text("not sent yet", timeout=WAIT_MS)
    assert len(held_transfers) == 1
    evidence = Path(
        os.environ.get("PANELS_UI_PROOF_DIR", str(REPO_ROOT / "data/verification/video-upload"))
    )
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / "video-early-send-pending.png"), full_page=True)
    with page.expect_response(
        lambda result: "/video-uploads/" in result.url and result.request.method == "PUT",
        timeout=WAIT_MS,
    ) as transfer:
        held_transfers.pop().continue_()
    assert transfer.value.status == 200, transfer.value.text()
    expect(pending).to_have_count(0, timeout=WAIT_MS)
    player = page.locator("video").last
    expect(player).to_be_visible(timeout=WAIT_MS)
    page.wait_for_function(
        '() => { const v = document.querySelector("video");'
        'return v && v.readyState >= 1 && v.duration >= 3.9; }',
        timeout=WAIT_MS,
    )
    href = player.get_attribute("src")
    assert href
    assert any(response.status == 206 for response in browser_ranges)
    head = httpx.head(server.base + href, timeout=10)
    assert head.status_code == 200
    assert int(head.headers["content-length"]) == video.stat().st_size
    ranged = httpx.get(server.base + href, headers={"Range": "bytes=0-31"}, timeout=10)
    assert ranged.status_code == 206
    assert ranged.content == video.read_bytes()[:32]
    player.evaluate("(video) => { video.currentTime = 2; }")
    page.wait_for_function(
        '() => { const v = document.querySelector("video");'
        'return v && !v.seeking && v.currentTime >= 1.9; }',
        timeout=WAIT_MS,
    )
    events = httpx.get(
        server.base + f"/api/conversation/conversations/{conversation_id}/events",
        timeout=10,
    ).json()["events"]
    prompts = [event["payload"] for event in events if event["kind"] == "prompt"]
    files = [
        piece
        for prompt in prompts
        for piece in prompt.get("content", [])
        if piece["piece"] == "file"
    ]
    assert len(files) == 1
    assert files[0]["file_name"] == video.name
    assert files[0]["media_type"] == "video/mp4"
    page.screenshot(path=str(evidence / "video-playback.png"), full_page=True)
    print(f"Video upload browser boundary: {time.monotonic() - started:.2f}s")
