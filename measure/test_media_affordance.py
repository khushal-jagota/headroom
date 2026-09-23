"""With preload=none, is the preview still visibly a playable thing, and does it play?"""
from __future__ import annotations
import shutil, sqlite3, subprocess
from collections.abc import Callable
from pathlib import Path
from playwright.sync_api import BrowserContext
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle
from measure.test_baseline import _seed, _seed_thread, _open, VIDEO

SHOT = Path(__file__).parent / "shots"


def test_media(server: ServerHandle, context_factory: Callable[[], BrowserContext],
               cli: Callable[..., JsonObject]) -> None:
    ticket_id: str = cli(server, "ticket", "create", "--worker-type", "coding",
                         "--title", "Media affordance")["id"]
    paths = _seed(server, ticket_id)
    # An audio file too: the supervisor asked for audio playback, not only video.
    root = server.db_path.parent / "files" / "tickets" / ticket_id
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    str(root / "audio.mp3")], check=True, capture_output=True)
    with sqlite3.connect(server.db_path) as conn:
        _seed_thread(conn, ticket_id, paths)
        conn.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, created_at)"
            " VALUES ('conv_base', 999, 'message_to_owner', ?, 1700009999)",
            ('{"text": "And the sound.\\n\\n[audio.mp3](/files/tickets/%s/audio.mp3)",'
             ' "sender_label": "Coding worker", "sender": {"kind": "ticket", "id": "%s"},'
             ' "recipient": {"kind": "owner", "id": "owner"}}' % (ticket_id, ticket_id),),
        )
        conn.execute("UPDATE conversations SET latest_sequence = 999 "
                     "WHERE conversation_id = 'conv_base'")

    page = context_factory().new_page()
    page.set_viewport_size({"width": 900, "height": 1000})
    _open(page, server, ticket_id)
    page.locator("[data-conversation-expand]").click()
    page.wait_for_timeout(3000)

    video = page.locator("video").first
    video.scroll_into_view_if_needed()
    page.wait_for_timeout(600)
    box = video.bounding_box()
    print(f"\nvideo preview box: {box}")
    print("controls attribute:", video.get_attribute("controls"))
    print("preload attribute :", video.get_attribute("preload"))
    card = page.locator('[data-file-preview][data-file-preview-kind="video"]').first
    print("what the reader sees around it:", repr(card.inner_text()[:120]))
    print("mobile open link present:",
          card.locator(".file-preview-mobile-link").count() > 0)

    SHOT.mkdir(exist_ok=True)
    card.screenshot(path=str(SHOT / "video-preload-none.png"))
    print("screenshot:", SHOT / "video-preload-none.png")

    # Plays on the first action.
    video.evaluate("v => { v.muted = true; return v.play(); }")
    page.wait_for_timeout(2500)
    state = video.evaluate("v => [v.currentTime, v.duration, v.readyState, v.paused]")
    print(f"after one play: t={state[0]:.2f}s of {state[1]}s readyState={state[2]} paused={state[3]}")
    video.evaluate("v => { v.currentTime = v.duration * 0.7; return v.play(); }")
    page.wait_for_timeout(2000)
    sought = video.evaluate("v => [v.currentTime, v.readyState, v.paused]")
    print(f"after seeking to 70%: t={sought[0]:.2f}s readyState={sought[1]} paused={sought[2]}")

    audio = page.locator("audio").first
    audio.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    print("\naudio preload attribute:", audio.get_attribute("preload"))
    audio.evaluate("a => { a.muted = true; return a.play(); }")
    page.wait_for_timeout(2000)
    astate = audio.evaluate("a => [a.currentTime, a.duration, a.readyState, a.paused]")
    print(f"audio after one play: t={astate[0]:.2f}s of {astate[1]}s "
          f"readyState={astate[2]} paused={astate[3]}")
    acard = page.locator('[data-file-preview][data-file-preview-kind="audio"]').first
    acard.screenshot(path=str(SHOT / "audio-preload-none.png"))
    print("screenshot:", SHOT / "audio-preload-none.png")
