"""Ticket browser adapters through the real core and controlled backend.

A mocked pane cannot prove that browser writes reach canonical queue/answer
writers, or that a mounted Ticket pane reflects the canonical result.
Only the backend wire is scripted. No installed provider participates.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import closing

import httpx
from playwright.sync_api import BrowserContext, Page, expect
from tests.e2e.harness import WAIT_MS, JsonObject, ServerHandle, capture_success
from tests.e2e.test_conversation_signals import (  # noqa: F401
    _navigate_to_retained_history,
    _post,
    signal_server,
)
from tests.support.connected_rows import _bind


def _create_ticket_conversation(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    *,
    conversation_id: str,
    title: str,
) -> str:
    ticket_id: str = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", title,
    )["id"]
    _post(server, f"/api/test/signals/{conversation_id}/start")
    _bind(server, "ticket", ticket_id, conversation_id)
    return ticket_id


def _open_ticket_conversation(
    server: ServerHandle,
    ticket_id: str,
    open_page: Callable[..., Page],
    context: BrowserContext,
    ready_selector: str,
) -> Page:
    page = open_page(
        context, server, f"#/workspace/{ticket_id}",
        f'[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    _open_conversation_layer(page)
    page.wait_for_selector(ready_selector, timeout=WAIT_MS)
    return page


def _open_conversation_layer(page: Page) -> None:
    page.locator("[data-conversation-expand]").click(timeout=WAIT_MS)
    page.locator('[data-conversation-state="opened"]').wait_for(timeout=WAIT_MS)


def test_agent_questions_survive_reload_submit_as_one_map_and_replay_answers(
    signal_server: ServerHandle,  # noqa: F811 - imported pytest fixture
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Browser answers reach the backend sink and survive durable replay."""
    server = signal_server
    conversation_id = "e2e-ticket-agent-questions"
    ticket_id = _create_ticket_conversation(
        server, cli, conversation_id=conversation_id, title="Answer agent questions",
    )
    _post(server, f"/api/test/signals/{conversation_id}/questions")
    context = context_factory()
    page = _open_ticket_conversation(
        server, ticket_id, open_page, context, "[data-user-input-panel]",
    )
    assert "Approve" not in page.locator("[data-user-input-panel]").inner_text()
    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    page.wait_for_selector("[data-user-input-panel]", timeout=WAIT_MS)
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.locator('[data-user-input-option="Composer"]').click()
    page.locator('[data-user-input-option="Transcript"]').click()
    page.locator("[data-user-input-continue]").click()
    page.locator("[data-user-input-other]").fill("Recorded event replay")
    with page.expect_request(
        lambda request: request.url.endswith("/user-input-answers")
        and request.method == "POST",
    ) as submitted:
        with page.expect_response(
            lambda response: response.url.endswith("/user-input-answers")
            and response.request.method == "POST",
        ) as answered:
            page.locator("[data-user-input-continue]").click()
    expected_answers = {
        "scope": {"answers": ["Composer", "Transcript"]},
        "proof": {"answers": ["Recorded event replay"]},
    }
    assert submitted.value.post_data_json == {
        "request_id": "1:user_input:input-1", "answers": expected_answers,
    }
    assert answered.value.json() == {"landed": True}
    receipts = httpx.get(
        server.base + f"/api/test/signals/{conversation_id}/answers", timeout=10,
    )
    assert receipts.is_success, receipts.text
    assert receipts.json() == {"input-1": expected_answers}
    events = httpx.get(
        server.base + f"/api/conversation/conversations/{conversation_id}/events", timeout=10,
    ).json()["events"]
    assert [event["payload"] for event in events if event["kind"] == "user_input_answered"] == [
        {"request_id": "1:user_input:input-1", "answers": expected_answers},
    ]
    page.wait_for_selector('[data-conversation-user-input-state="answered"]', timeout=WAIT_MS)
    assert "Recorded event replay" in page.locator(
        "[data-conversation-user-input-answers]",
    ).inner_text()
    # A new document consumes the durable answered event, not local submitted state.
    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    page.locator('[data-conversation-lens-choice="full"]').click()
    page.wait_for_selector('[data-conversation-user-input-state="answered"]', timeout=WAIT_MS)
    assert "Recorded event replay" in page.locator(
        "[data-conversation-user-input-answers]",
    ).inner_text()
    expect(page.locator("[data-user-input-panel]")).to_have_count(0)


def test_canonical_queue_writes_refresh_the_mounted_pane(
    signal_server: ServerHandle,  # noqa: F811 - imported pytest fixture
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Actual held IDs reach real writers and refresh the mounted pane.

    The owner moved same-profile multi-tab transport coverage to its own Ticket.
    The original two-tab case and failure evidence remain runnable in that Ticket.
    """
    server = signal_server
    conversation_id = "e2e-ticket-canonical-queue"
    ticket_id = _create_ticket_conversation(
        server, cli, conversation_id=conversation_id, title="Manage held prompts",
    )
    path = f"/api/conversation/conversations/{conversation_id}"
    _post(server, f"/api/test/signals/{conversation_id}/queue")
    view = httpx.get(server.base + path, timeout=10).json()
    held = view["held_prompts"]
    assert [row["text"] for row in held] == ["queued message 1", "queued message 2"]
    discard_id, promote_id = [row["held_prompt_id"] for row in held]
    context = context_factory()
    page = _open_ticket_conversation(
        server, ticket_id, open_page, context, "[data-conversation-held-stack]",
    )
    expect(page.locator("[data-conversation-held-row]")).to_have_count(2)
    with page.expect_response(
        lambda response: response.url.endswith(f"/held-prompts/{discard_id}")
        and response.request.method == "DELETE",
    ) as discarded:
        page.locator(f'[data-conversation-held-discard="{discard_id}"]').click()
    assert discarded.value.json() == {"discarded": True}
    expect(page.locator("[data-conversation-held-row]")).to_have_count(1, timeout=WAIT_MS)
    assert httpx.get(server.base + path, timeout=10).json()["held_prompts"][0][
        "held_prompt_id"
    ] == promote_id
    with page.expect_response(
        lambda response: response.url.endswith(f"/held-prompts/{promote_id}/promote")
        and response.request.method == "POST",
    ) as promoted:
        page.locator(
            f'[data-conversation-held-promote="send_now"][data-held-prompt-id="{promote_id}"]',
        ).click()
    assert promoted.value.request.post_data_json == {"mode": "send_now"}
    assert promoted.value.json() == {"promoted": True, "fate": "started"}
    expect(page.locator("[data-conversation-held-row]")).to_have_count(0, timeout=WAIT_MS)
    assert httpx.get(server.base + path, timeout=10).json()["held_prompts"] == []
    events = httpx.get(server.base + path + "/events", timeout=10).json()["events"]
    assert any(
        event["kind"] == "prompt_discarded"
        and event["payload"].get("sender_message_id") == "sender-1"
        for event in events
    )
    assert any(
        event["kind"] == "prompt"
        and event["payload"].get("sender_message_id") == "sender-2"
        and event["payload"]["mode"] == "send_now"
        for event in events
    )


_LONG_REPORT = " ".join(
    f"line{index} of a worker report as long as a real one" for index in range(90)
)
# The thread's drawn rows, and the newest record sequence among them. Every read below
# needs both, so they are written once.
_THREAD_READS = r"""
  const thread = () => document.querySelector('[data-conversation-thread]');
  const nodes = () => {
    const root = thread();
    return root ? [...root.querySelectorAll('[data-conversation-view-key]')] : [];
  };
  const sequenceOf = (node) => {
    const found = /(?:^|:)e(\d+)$/.exec(node.dataset.conversationViewKey || '');
    return found ? Number.parseInt(found[1], 10) : null;
  };
  const newestRetained = () => {
    const found = nodes().map(sequenceOf).filter(value => value !== null);
    return found.length ? Math.max(...found) : null;
  };
  // Rows the record already held when the owner pressed Enter. One of these below the
  // owner's copy is the defect: their message placed inside history.
  const olderAfter = (all, node, target) => all.slice(all.indexOf(node) + 1)
    .filter(following => (sequenceOf(following) ?? Infinity) <= target)
    .map(following => following.dataset.conversationViewKey);
  const inFrame = (node) => {
    const frame = thread().getBoundingClientRect();
    const box = node.getBoundingClientRect();
    return box.bottom > frame.top + 1 && box.top < frame.bottom - 1;
  };
  const mineIn = (all, text) => all.filter(node => (node.textContent || '').includes(text));
"""
_NEWEST_RETAINED = "() => {" + _THREAD_READS + "  return newestRetained();\n}"
# Watch the owner's own copy from the send onward. Position only: which row it is,
# whether any row follows it, and whether it stays in the thread's frame once it has
# been seen there. A correct final screenshot cannot show a transient burial, so every
# animation frame is sampled and the first violations are kept.
_WATCH_OWN_COPY = """({ text, target }) => {""" + _THREAD_READS + """
  const state = {
    started: performance.now(), violations: [], seen: false, shown: false,
    firstSeenMs: null, firstShownMs: null, caughtUpMs: null,
    pastHistoryDuringCatchUp: 0, newestRetained: null, copies: 0, mark: null, trail: [],
  };
  window.__ownCopy = state;
  const since = () => Math.round(performance.now() - state.started);
  const fault = (why, extra) => {
    if (state.violations.length < 20)
      state.violations.push({ atMs: since(), why, ...extra });
  };
  const tick = () => {
    const all = nodes();
    const mine = mineIn(all, text);
    const newest = newestRetained();
    state.newestRetained = newest;
    state.copies = mine.length;
    if (newest !== null && newest >= target && state.caughtUpMs === null)
      state.caughtUpMs = since();
    if (mine.length > 1)
      fault('drawn more than once', { copies: mine.length });
    const node = mine[0];
    const sample = { mark: node ? node.dataset.conversationViewKey : null, newest,
                     trailing: all.slice(-2).map(n => n.dataset.conversationViewKey) };
    const previous = state.trail[state.trail.length - 1];
    if (state.trail.length < 40 && (!previous || previous.mark !== sample.mark
        || previous.newest !== sample.newest
        || String(previous.trailing) !== String(sample.trailing)))
      state.trail.push({ atMs: since(), ...sample });
    if (node) {
      state.mark = node.dataset.conversationViewKey ?? null;
      if (!state.seen) { state.seen = true; state.firstSeenMs = since(); }
      const older = olderAfter(all, node, target);
      if (older.length)
        fault('history follows the owner copy', { after: older.slice(0, 3) });
      else if (state.caughtUpMs === null)
        state.pastHistoryDuringCatchUp += 1;
      if (inFrame(node)) {
        if (!state.shown) { state.shown = true; state.firstShownMs = since(); }
      } else if (state.shown) {
        fault('the owner copy left the thread frame', {});
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}"""
_FINAL_PLACE = """({ text, target }) => {""" + _THREAD_READS + """
  const all = nodes();
  const mine = mineIn(all, text);
  return {
    copies: mine.length,
    historyAfter: mine.length === 1 ? olderAfter(all, mine[0], target) : null,
    inFrame: mine.length === 1 && inFrame(mine[0]),
    trailing: all.slice(-3).map(node => node.dataset.conversationViewKey),
  };
}"""


def _record_latest_sequence(server: ServerHandle, conversation_id: str) -> int:
    events = httpx.get(
        server.base + f"/api/conversation/conversations/{conversation_id}/events", timeout=20,
    ).json()["events"]
    return int(events[-1]["sequence"])


def _seed_long_worker_reports(
    server: ServerHandle, conversation_id: str, ticket_id: str, count: int,
) -> None:
    """Fill the conversation past the reader's retained bounds with unread work."""
    for index in range(count):
        _post(
            server,
            f"/api/test/signals/{conversation_id}/message",
            {
                "label": "Worker", "kind": "ticket", "id": ticket_id,
                "text": f"report {index}: {_LONG_REPORT}",
            },
        )


def test_owner_send_lands_at_the_end_of_the_conversation_not_the_retained_window(
    signal_server: ServerHandle,  # noqa: F811 - imported pytest fixture
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """The owner's own copy is the last row in the thread and stays in the frame.

    The risk is the owner's own message becoming invisible again. The symptom is
    composed of reader coverage, the retained window's bounds, entry placement and
    scroll together, so no unit test on the slot ledger can prove that the owner
    sees it.
    """
    server = signal_server
    conversation_id = "e2e-ticket-own-send-placement"
    ticket_id = _create_ticket_conversation(
        server, cli, conversation_id=conversation_id, title="Place an owner send",
    )
    _seed_long_worker_reports(server, conversation_id, ticket_id, 160)
    _post(server, f"/api/test/signals/{conversation_id}/finish")
    target = _record_latest_sequence(server, conversation_id)

    context = context_factory()
    page = _open_ticket_conversation(
        server, ticket_id, open_page, context,
        '[data-conversation-thread][data-conversation-placement-ready="true"]',
    )
    _navigate_to_retained_history(page, target)
    retained_at_entry = page.evaluate(_NEWEST_RETAINED)
    # Without this the test can pass for the wrong reason. A window that already holds
    # the newest row never places a send from the wrong end.
    assert retained_at_entry is not None
    assert retained_at_entry < target, (retained_at_entry, target)

    text = "OWN SEND placed at the end of the conversation"
    page.evaluate(_WATCH_OWN_COPY, {"text": text, "target": target})
    page.locator("[data-conversation-input]").fill(text)
    page.locator("[data-conversation-input]").press("Enter")
    # Wait for the window to reach the record, which is the page-by-page catch-up that
    # buried the copy, then keep sampling for a moment while the thread settles. The
    # wait covers catch-up only, so a broken product fails an assertion below rather
    # than this call.
    page.wait_for_function("() => window.__ownCopy.caughtUpMs !== null", timeout=WAIT_MS)
    page.wait_for_timeout(1000)
    watched = page.evaluate("() => window.__ownCopy")
    print("OWN_COPY_TRAIL " + json.dumps(watched["trail"]))
    assert watched["seen"] is True, watched
    assert watched["shown"] is True, watched
    assert watched["violations"] == [], watched
    assert watched["copies"] == 1, watched
    assert watched["pastHistoryDuringCatchUp"] > 0, watched

    prompts = [
        event for event in httpx.get(
            server.base + f"/api/conversation/conversations/{conversation_id}/events", timeout=20,
        ).json()["events"]
        if event["kind"] == "prompt" and text in str(event["payload"])
    ]
    assert len(prompts) == 1, prompts
    # The slot correlates its own record row, so the message is drawn once rather than
    # beside a second copy. It says nothing about placement: the drawn key is the row's
    # whatever order the slot ends up with.
    assert watched["mark"] == f"e{prompts[0]['sequence']}", watched
    final = page.evaluate(_FINAL_PLACE, {"text": text, "target": target})
    assert final["copies"] == 1, final
    assert final["historyAfter"] == [], final
    assert final["inFrame"] is True, final
    # Only the turn the send itself started is drawn below the owner's message. This is
    # what catches a slot that keeps its minted order: by the time the record row exists
    # the mint reads a sequence past it, and the owner's message sorts below its own
    # turn anchor.
    assert final["trailing"][-1] == f"turn:e{prompts[0]['sequence']}", final
    capture_success(page, "owner-send-placed-at-conversation-end.png")


def test_private_credential_save_survives_reload_and_reaches_the_actual_wire(
    signal_server: ServerHandle,  # noqa: F811 - imported pytest fixture
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Only browser → private owner API → encrypted store → live card proves this wiring.

    Reuse the existing Signal host and backend wire. Component tests cover the state
    matrix; this one case covers the connected save, durable replay and actual delivery.
    """
    from planner.core.db import connect
    from planner.credentials import data as credential_data

    server = signal_server
    conversation_id = "e2e-ticket-private-credential"
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Supply an API key",
    )["id"]
    _post(server, f"/api/test/signals/{conversation_id}/start", {"ticket_id": ticket_id})
    request_id = str(uuid.uuid4())
    requested_name = "Panels_provider_production"
    response = httpx.post(
        server.base + "/api/credentials/requests",
        headers={
            "X-Plan-Actor": "worker",
            "X-Plan-Ticket-ID": ticket_id,
            "X-Plan-Conversation-ID": conversation_id,
        },
        json={
            "request_id": request_id,
            "name": requested_name,
            "provider": "Provider",
            "use": "Read the authorized production measurement",
            "auth_kind": "bearer",
            "header_name": None,
            "help": "Create a read key in the provider settings.",
            "help_url": "https://example.com/api-keys",
            "replacement": None,
        },
        timeout=10,
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending"
    _post(server, f"/api/test/signals/{conversation_id}/finish")
    context = context_factory()
    card_selector = f'[data-credential-request="{request_id}"]'
    page = _open_ticket_conversation(
        server,
        ticket_id,
        open_page,
        context,
        card_selector,
    )
    card = page.locator(card_selector)
    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    expect(card).to_have_attribute("data-credential-state", "pending", timeout=WAIT_MS)
    # Three captures share this connected case; no repeated backend or state matrix.
    for label, width, height in (("desktop", 1280, 900), ("phone", 390, 844), ("short", 320, 568)):
        page.set_viewport_size({"width": width, "height": height})
        expect(card).to_be_visible(timeout=WAIT_MS)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        capture_success(page, f"credential-request-connected-{label}.png")
    page.set_viewport_size({"width": 1280, "height": 900})
    secret = "synthetic-browser-owner-only-" + uuid.uuid4().hex
    posted: list[tuple[str, str | None]] = []
    page.on("request", lambda request: posted.append((request.url, request.post_data)))
    card.get_by_role("textbox", name="Key name", exact=True).fill(requested_name + "_edited")
    card.get_by_label("API key", exact=True).fill(secret)
    with page.expect_response(
        lambda result: (
            result.url.endswith(f"/credentials/requests/{request_id}/save")
            and result.request.method == "POST"
        ),
    ) as saved_response:
        card.get_by_role("button", name="Save", exact=True).click()
    saved = saved_response.value
    assert saved.status == 200, saved.text()
    assert saved.request.post_data_json == {
        "name": requested_name + "_edited",
        "value": secret,
        "expected_revision": None,
    }
    body = saved.json()
    assert body["state"] == "saved"
    assert body["delivery"]["status"] == "started"
    assert [url for url, data in posted if data and secret in data] == [saved.url]
    expect(card).to_have_attribute("data-credential-state", "saved", timeout=WAIT_MS)
    expect(card.locator("input")).to_have_count(0)
    assert secret not in page.locator("[data-conversation-thread]").inner_text()
    with closing(connect(str(server.db_path))) as conn:
        metadata = credential_data.read_metadata(conn, body["credential"]["id"])
        assert metadata.reference.name == requested_name + "_edited"
        assert metadata.revision == 1
        assert secret.encode() not in credential_data.read_encrypted(conn, metadata.id).ciphertext
    events = httpx.get(
        server.base + f"/api/conversation/conversations/{conversation_id}/events",
        timeout=10,
    ).json()["events"]
    assert secret not in json.dumps(events)
    delivered = httpx.get(
        server.base + f"/api/test/signals/{conversation_id}/delivered",
        timeout=10,
    ).json()["texts"]
    assert len(delivered) == 2, delivered
    assert request_id in delivered[-1] and '"outcome":"saved"' in delivered[-1]
    assert requested_name + "_edited" in delivered[-1]
    assert secret not in "".join(delivered)
    _post(server, f"/api/test/signals/{conversation_id}/finish")
    page.reload()
    page.wait_for_selector("[data-conversation-rest-bar]", timeout=WAIT_MS)
    _open_conversation_layer(page)
    expect(card).to_have_attribute("data-credential-state", "saved", timeout=WAIT_MS)
    expect(card.locator("input")).to_have_count(0)
    expect(card.get_by_text("Saved", exact=True)).to_be_visible(timeout=WAIT_MS)
    capture_success(page, "credential-request-connected-saved.png")
