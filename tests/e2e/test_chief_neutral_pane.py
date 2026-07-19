"""Flag-ON Chief neutral pane e2e (S2b plan §7.1). Drives the REAL neutral pane over
/api/relay/neutral against the STATEFUL scripted child (test-mode compose). No real Hermes.

Every scenario opens against `relay_chief_server` (PLAN_RELAY_BACKEND_ENABLED=1 in test mode)
and waits on the pane's `data-neutral-ready` marker before asserting, so it never races the
pre-history mount. These are intentionally unanchored (they do not affect the verify scorer),
mirroring test_chief_of_staff.py.
"""

from __future__ import annotations

import struct
import zlib

from playwright.sync_api import Page

WAIT_MS = 10_000
NEUTRAL_READY = '[data-chief-neutral-pane][data-neutral-ready="true"]'


def _valid_png_bytes() -> bytes:
    """A minimal 1x1 grayscale PNG that passes the server's strict image sniffer
    (files/chat_images.sniff_image_extension)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")  # one scanline: filter byte + one grayscale pixel
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _open_neutral(open_page, context_factory, server, route: str) -> Page:
    page = open_page(context_factory(), server, route, NEUTRAL_READY, settled=False)
    return page


def _wait_chat_text(page: Page, who: str, text: str) -> None:
    page.wait_for_function(
        "({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg=\"${who}\"]`))"
        ".some(el => el.textContent.includes(text))",
        arg={"who": who, "text": text},
        timeout=WAIT_MS,
    )


def _send(page: Page, text: str) -> None:
    page.fill("[data-chat-input]", text)
    page.click("[data-chat-send]")


# --- re-anchored existing scenarios ----------------------------------------------------------


def test_neutral_chief_send_and_render(open_page, context_factory, relay_chief_server) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _send(page, "triage the workspace")
    _wait_chat_text(page, "you", "triage the workspace")
    _wait_chat_text(page, "planner", "echo: triage the workspace")


def test_neutral_chief_history_on_load(open_page, context_factory, relay_chief_server) -> None:
    # The scripted child's seeded durable key returns prior messages on attach; the pane
    # renders them from the HistorySnapshotEvent (history from the durable session, NOT
    # Panels DB).
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _wait_chat_text(page, "you", "earlier question")
    _wait_chat_text(page, "planner", "earlier answer")


def test_neutral_chief_refresh_mid_conversation(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _send(page, "remember this")
    _wait_chat_text(page, "planner", "echo: remember this")
    page.reload()
    page.wait_for_selector(NEUTRAL_READY, timeout=WAIT_MS)
    # After re-attach the transcript is restored from the fresh HistorySnapshotEvent.
    _wait_chat_text(page, "you", "remember this")
    _wait_chat_text(page, "planner", "echo: remember this")


def test_neutral_chief_images(open_page, context_factory, relay_chief_server) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    # Attach an image via the existing upload flow; the scripted child VALIDATES the resolved
    # absolute path (an ACK-anything fake would pass falsely).
    page.set_input_files(
        "[data-chat-image-input]",
        files=[
            {
                "name": "shot.png",
                "mimeType": "image/png",
                "buffer": _valid_png_bytes(),
            }
        ],
    )
    page.wait_for_selector("[data-chat-image-preview]", timeout=WAIT_MS)
    _send(page, "look at this")
    # The turn completes -> the child accepted image.attach with an absolute openable path.
    _wait_chat_text(page, "planner", "echo: look at this")


def test_neutral_chief_interrupt(open_page, context_factory, relay_chief_server) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    # The HOLD cue keeps the turn RUNNING (streaming, not finished) until interrupt arrives, so
    # interrupt acts on a genuinely running turn.
    _send(page, "hold open this reply")
    # Wait until the turn is actually streaming (a delta rendered) before interrupting.
    page.wait_for_selector("[data-neutral-streaming]", timeout=WAIT_MS)
    _send(page, "/interrupt")  # interrupt is a slash action in the "/" menu
    # The named behavior: the RUNNING turn CLOSES as interrupted (the interrupted line renders
    # as the turn's assistant slot, and the live streaming element is gone).
    _wait_chat_text(page, "planner", "(interrupted)")
    page.wait_for_selector("[data-neutral-streaming]", state="detached", timeout=WAIT_MS)
    # AND the composer never busy-locks: with text present, a fresh send stays enabled (the
    # empty-composer disable is not a turn-busy disable).
    page.fill("[data-chat-input]", "still here")
    assert page.is_enabled("[data-chat-input]")
    assert page.is_enabled("[data-chat-send]")


# --- new scenarios ---------------------------------------------------------------------------


def test_neutral_chief_streamed_delta_token_by_token(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    # Pre-install a MutationObserver that records every streaming-text value so a partial
    # prefix is proven to have existed even though the full string later lands.
    page.evaluate(
        """
        () => {
          window.__streamSamples = [];
          const record = () => {
            const el = document.querySelector('[data-neutral-streaming]');
            if (el) window.__streamSamples.push(el.textContent);
          };
          const observer = new MutationObserver(record);
          observer.observe(document.body, { childList: true, subtree: true, characterData: true });
          window.__streamObserver = observer;
        }
        """
    )
    _send(page, "stream me a multi token reply")
    _wait_chat_text(page, "planner", "echo: stream me a multi token reply")
    samples = page.evaluate("() => window.__streamSamples || []")
    # At least one sample must be a strict, non-empty PREFIX of the final text (a partial).
    final = "echo: stream me a multi token reply"
    partials = [s for s in samples if s and final.startswith(s) and s != final]
    assert partials, f"no partial prefix observed; samples={samples}"


def test_neutral_chief_thinking_indication(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _send(page, "think about it")
    # The activity indicator (progressive disclosure, collapsed) shows during the turn, its label
    # driven by the agent's status; the reasoning stream lives behind the disclosure.
    page.wait_for_selector("[data-neutral-activity-toggle]", timeout=WAIT_MS)
    _wait_chat_text(page, "planner", "echo: think about it")


def test_neutral_chief_clarify_answered_inline(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _send(page, "please ask me a question")
    # The clarify cue holds the turn; the composer's inline question + choices render.
    page.wait_for_selector("[data-chat-clarification-question]", timeout=WAIT_MS)
    page.wait_for_selector("[data-chat-clarification-choice]", timeout=WAIT_MS)
    # Select a choice, then Send answers it; the HELD turn completes AFTER clarify.respond.
    page.click("[data-chat-clarification-choice]")
    page.click("[data-chat-send]")
    _wait_chat_text(page, "planner", "echo: please ask me a question")


def test_neutral_chief_compact(open_page, context_factory, relay_chief_server) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _send(page, "/compact")  # compact is a slash action in the "/" menu
    # Absence BARRIER (F14): S2a emits NOTHING on a successful compact ACK. Drive a following
    # observable action (a send that streams) and assert THAT lands with no failure line in
    # between — so "absence of failure" is checked AFTER the compact ACK is processed.
    _send(page, "after compact")
    _wait_chat_text(page, "planner", "echo: after compact")
    # No system/failure line was produced by the compact.
    failures = page.evaluate(
        "() => Array.from(document.querySelectorAll('[data-chat-msg=\"system\"]'))"
        ".map(el => el.textContent)"
    )
    assert all("busy" not in (text or "").lower() for text in failures), failures


def test_neutral_chief_compact_4009_surfaces_failure(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    # A send carrying the compact-fail cue FLAGS this session so a later session.compress on it
    # returns 4009. Send it and wait for the turn to complete (the flag is now set).
    _send(page, "compact-fails please")
    # markdown renders __compact_fails__ as bold, so assert on the rendered text.
    _wait_chat_text(page, "planner", "echo: compact-fails please")
    # Now Compact -> the child rejects with 4009 -> the pane surfaces a TurnFailed failure
    # line (busy_already_running); the native rejection is NOT masked.
    _send(page, "/compact")
    _wait_chat_text(page, "system", "busy")


def test_neutral_chief_skill_picker_inserts_composer_text(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    # OPEN the "/" menu; it lists session actions + skills. Pick a skill.
    page.click("[data-chat-slash]")
    page.wait_for_selector("[data-chat-skill]", timeout=WAIT_MS)
    skill_name = page.get_attribute("[data-chat-skill]", "data-chat-cmd")
    page.click("[data-chat-skill]")
    # The skill's trigger text is INSERTED into the composer draft (no auto-send).
    value = page.input_value("[data-chat-input]")
    assert value.strip() != "", "skill pick did not insert composer text"
    # Nothing was sent: no assistant echo of the skill trigger appeared (the seeded history's
    # prior planner line is unrelated; we assert no echo of THIS trigger).
    echoed = page.evaluate(
        "() => Array.from(document.querySelectorAll('[data-chat-msg=\"planner\"]'))"
        ".map(el => el.textContent)"
    )
    assert all("echo:" not in (text or "") or skill_name not in (text or "") for text in echoed), (
        f"skill trigger appears to have been auto-sent: {echoed}"
    )


def test_neutral_chief_child_reset_reattach_recovery(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    # A completed turn whose messages persist to the durable (respawn-surviving) session store.
    _send(page, "before reset")
    _wait_chat_text(page, "planner", "echo: before reset")
    # Send the RESET cue: the child ACKs then DIES, so the relay synthesizes child_reset. The
    # send first paints an OPTIMISTIC human row (the barrier below proves the reset actually
    # fired: that optimistic row must DISAPPEAR when the post-reattach history snapshot — which
    # never received the never-completed reset-cue turn — REPLACES the transcript).
    _send(page, "reset-child now")
    # (1) The optimistic reset-cue human row appears (the send landed). markdown renders
    # __reset_child__ as bold, so the visible text is "reset_child now".
    _wait_chat_text(page, "you", "reset-child")
    # (2) The RECOVERY BARRIER: after child death -> child_reset -> auto re-attach, the fresh
    # history snapshot REPLACES the transcript, so the optimistic reset-cue row is GONE. This can
    # only happen via the child_reset -> re-attach path (the durable store never got that turn).
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('[data-chat-msg=\"you\"]'))"
        ".some(el => el.textContent.includes('reset-child'))",
        timeout=WAIT_MS,
    )
    # AND the durable history is intact after re-attach (the persisted "before reset" turn), with
    # no assistant echo of the never-completed reset cue.
    _wait_chat_text(page, "you", "before reset")
    _wait_chat_text(page, "planner", "echo: before reset")
    assert not any(
        "reset-child" in (text or "")
        for text in page.evaluate(
            "() => Array.from(document.querySelectorAll('[data-chat-msg=\"planner\"]'))"
            ".map(el => el.textContent)"
        )
    )
    # The pane is functional after recovery: a fresh send streams normally.
    _send(page, "after reset")
    _wait_chat_text(page, "planner", "echo: after reset")


def test_neutral_chief_new_conversation(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _wait_chat_text(page, "you", "earlier question")  # seeded history present
    _send(page, "/new")  # new-conversation is a slash action in the "/" menu
    # After the pool closes+rebinds, the pane shows EMPTY history (the seeded messages are gone).
    page.wait_for_function(
        "() => document.querySelectorAll('[data-chat-msg=\"you\"]').length === 0",
        timeout=WAIT_MS,
    )


def test_neutral_chief_board_route_mount(
    open_page, context_factory, relay_chief_server
) -> None:
    # The BoardRoute right-pane chief mount also uses the neutral pane.
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/workspace")
    _send(page, "from the board")
    _wait_chat_text(page, "planner", "echo: from the board")


def test_neutral_chief_composer_never_disabled_mid_turn(
    open_page, context_factory, relay_chief_server
) -> None:
    page = _open_neutral(open_page, context_factory, relay_chief_server, "#/chief")
    _send(page, "first streaming message")
    # While the first turn streams, the composer stays enabled and a second send is accepted.
    assert page.is_enabled("[data-chat-input]")
    _send(page, "second while streaming")
    _wait_chat_text(page, "you", "second while streaming")
