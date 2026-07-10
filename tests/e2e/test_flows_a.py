"""E2E flows A — SPEC §18.3 items 22-27 (Playwright, chromium, two contexts where
stated).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item may exist across the whole e2e
suite, so there is no parametrize and every shared helper below has a non-``test_``
name. Assertions use the SPEC's exact values (states, ceilings, scope pairs, the
minted session key, rendered markdown structure) — never weakened approximations.
"""

from __future__ import annotations

import sqlite3

from playwright.sync_api import Page

WAIT_MS = 10_000

MD_BODY = (
    "# Success criteria\n"
    "\n"
    "Ship the T18 harness with **exact** assertions.\n"
    "\n"
    "- six anchored tests\n"
    "- two browser contexts\n"
    "\n"
    "1. boot server\n"
    "2. drive UI\n"
    "\n"
    "Done when verify flips items 22-27."
)
E24_BODY = "Agent-drafted success criteria."
E25_ORIG = "# Original proposal\n\n- old structure"
E25_EDIT = "# Original proposal v2\n\n- kept structure\n- serialized from DOM"
NOOP_MARKDOWN_BODY = "# Raw forms\n\n* star bullet\n\n1) ordered paren\n\n_line italic_"
SELECT_NODE_CONTENTS = (
    "node => { const r = document.createRange(); r.selectNodeContents(node);"
    " const s = getSelection(); s.removeAllRanges(); s.addRange(r); }"
)
E27_SUCCESS = "Success body for the chain."
E27_APPROACH = "Approach body for the chain."
E27_PLAN = "Plan body for the chain."
TALL_CHAT_MESSAGE = "\n".join(f"history line {index}" for index in range(24))


def _wait_enabled(page: Page, selector: str) -> None:
    page.wait_for_function(
        "sel => { const b = document.querySelector(sel); return !!b && !b.disabled; }",
        arg=selector,
        timeout=WAIT_MS,
    )


def _wait_present(page: Page, selector: str) -> None:
    page.wait_for_function(
        "sel => document.querySelector(sel) !== null",
        arg=selector,
        timeout=WAIT_MS,
    )


def _wait_chat_text(page: Page, who: str, text: str) -> None:
    page.wait_for_function(
        '({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg="${who}"]`))'
        ".some(el => el.textContent.includes(text))",
        arg={"who": who, "text": text},
        timeout=WAIT_MS,
    )


def test_e22_cli_create_live_board(server, context_factory, open_page, cli, api):
    board = 'section[data-screen="workspace"]'
    ctx_a = context_factory()
    ctx_b = context_factory()
    page_a = open_page(ctx_a, server, "#/workspace", board, settled=False)
    page_b = open_page(ctx_b, server, "#/workspace", board, settled=False)

    for page in (page_a, page_b):
        count = page.eval_on_selector_all(
            '[data-card][data-ticket-state="needs_success"]', "els => els.length"
        )
        assert count == 0, count
    flushes_b = page_b.evaluate("window.__plannerDebug.flushes")

    created = cli(server, "ticket", "create", "--title", "T18 board ticket")
    tid = created["id"]
    assert created["state"] == "needs_success", created

    card = f'[data-card][data-ticket-state="needs_success"][data-ticket-id="{tid}"]'
    page_b.wait_for_function(
        "f => window.__plannerDebug.flushes > f", arg=flushes_b, timeout=WAIT_MS
    )
    assert page_b.query_selector(card) is None
    assert page_a.query_selector(card) is None

    flushes_b = page_b.evaluate("window.__plannerDebug.flushes")
    api.direct_post(server, "/api/day/today/tickets", {"ticket_id": tid})

    # No reload, no goto: the card can only arrive via a WS-flush re-render after
    # the ticket is explicitly added to today's board.
    _wait_present(page_b, card)
    assert "T18 board ticket" in page_b.inner_text(card)
    assert page_b.evaluate("window.__plannerDebug.flushes") > flushes_b

    _wait_present(page_a, card)
    assert "T18 board ticket" in page_a.inner_text(card)


def test_e23_env_pinned_propose(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 propose ticket")["id"]
    # PLAN_TICKET_ID resolves the ticket (no positional id); stdin carries the body.
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success criteria proposed.",
        ticket_id=tid,
        stdin=MD_BODY,
    )

    ready = f'section[data-screen="ticket"][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, f"#/ticket/{tid}", ready, settled=True)

    # Rendered: the proposal's markdown structure, with exact texts.
    b = "[data-approval-block] .approval-draft .markdown-block"
    assert page.inner_text(f"{b} h1") == "Success criteria"
    assert page.eval_on_selector_all(f"{b} ul li", "els => els.map(e => e.textContent)") == [
        "six anchored tests",
        "two browser contexts",
    ]
    assert page.eval_on_selector_all(f"{b} ol li", "els => els.map(e => e.textContent)") == [
        "boot server",
        "drive UI",
    ]
    assert page.inner_text(f"{b} strong") == "exact"
    paras = page.eval_on_selector_all(f"{b} p", "els => els.map(e => e.textContent)")
    assert len(paras) == 2, paras
    assert paras[-1] == "Done when verify flips items 22-27."

    # Focusing keeps the same continuously editable rendered surface in place.
    page.focus("[data-approval-block] [data-edit]")
    assert (
        page.locator("[data-approval-block] [data-edit]").get_attribute("contenteditable") == "true"
    )
    assert page.locator("[data-approval-block] [data-approval-draft-edit]").count() == 0
    assert page.locator("[data-approval-block] [data-markdown-source-editor]").count() == 0
    assert page.inner_text(f"{b} h1") == "Success criteria"
    assert page.eval_on_selector_all(f"{b} ul li", "els => els.map(e => e.textContent)") == [
        "six anchored tests",
        "two browser contexts",
    ]
    assert page.eval_on_selector_all(f"{b} ol li", "els => els.map(e => e.textContent)") == [
        "boot server",
        "drive UI",
    ]
    assert page.inner_text(f"{b} strong") == "exact"

    # Did not advance: still needs_success, and the value is still unset.
    assert page.get_attribute(ready, "data-state") == "needs_success"
    assert api.get(server, f"/api/tickets/{tid}")["fields"]["success"]["value"] is None


def test_e24_accept_in_review(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 review ticket")["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success ready for review.",
        ticket_id=tid,
        stdin=E24_BODY,
    )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page_a = open_page(context_factory(), server, "#/review", card, settled=True)
    assert page_a.get_attribute(card, "data-kind") == "success"

    # Second context watches the ticket page for the flip.
    page_b = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"][data-state="needs_success"]',
        settled=True,
    )

    # Scope defaults to the next stage and then propose.
    assert page_a.locator(f"{card} [data-scope-ceiling]").input_value() == "needs_approach"
    assert page_a.locator(f"{card} [data-scope-atcap] select").input_value() == "propose"
    _wait_enabled(page_a, f"{card} [data-accept]")
    page_a.click(f"{card} [data-accept]")

    # Queue departure (only entry on a fresh DB).
    page_a.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)
    assert api.get(server, "/api/queues")["approvals"] == []

    # Second context updates without reload — the flip arrives via the WS flush.
    page_b.wait_for_function(
        "() => { const s = document.querySelector('section[data-screen=\"ticket\"]');"
        " return !!s && s.getAttribute('data-state') === 'needs_approach'; }",
        timeout=WAIT_MS,
    )

    d = api.get(server, f"/api/tickets/{tid}")
    assert d["state"] == "needs_approach", d
    assert d["ceiling"] == "needs_approach", d  # default approval scope is next stage
    assert d["at_cap"] == "propose", d
    assert d["fields"]["success"]["value"] == E24_BODY
    assert d["fields"]["success"]["proposal"] is None


def test_review_return_for_revision_starts_agent_without_chat_copy(
    server, context_factory, open_page, cli, api
):
    tid = cli(server, "ticket", "create", "--title", "Revision review ticket")["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Needs revision.",
        ticket_id=tid,
        stdin="Too much detail.",
    )
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET chat_session_key = ? WHERE id = ?",
            ("existing-worker-session", tid),
        )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)
    page.fill(f"{card} [data-review-revision-input]", "Make it shorter.")
    page.click(f"{card} [data-review-revision-send]")
    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    ticket = api.get(server, f"/api/tickets/{tid}")
    assert ticket["state"] == "needs_success"
    assert ticket["ticket_status"] == "agent_running_step"
    assert ticket["fields"]["success"]["value"] is None
    assert ticket["fields"]["success"]["proposal"] is None
    assert api.get(server, "/api/queues")["approvals"] == []
    chat = api.get(server, f"/api/chat/{tid}/state")
    assert chat["messages"] == []


def test_markdown_approval_focus_noop_keeps_raw_source(
    server, context_factory, open_page, cli, api
):
    tid = cli(server, "ticket", "create", "--title", "Markdown noop ticket")["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Raw forms ready.",
        ticket_id=tid,
        stdin=NOOP_MARKDOWN_BODY,
    )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)

    # Untouched focus/blur keeps the rendered structure in place and must not
    # serialize the DOM back to canonical markdown forms.
    page.focus(f"{card} [data-edit]")
    assert page.locator(f"{card} [data-edit]").get_attribute("contenteditable") == "true"
    assert page.locator(f"{card} [data-approval-draft-edit]").count() == 0
    assert page.locator(f"{card} [data-markdown-source-editor]").count() == 0
    page.locator(f"{card} [data-edit]").blur()
    assert page.inner_text(f"{card} .approval-draft h1") == "Raw forms"
    assert page.eval_on_selector_all(
        f"{card} .approval-draft ul li", "els => els.map(e => e.textContent)"
    ) == ["star bullet"]
    assert page.eval_on_selector_all(
        f"{card} .approval-draft ol li", "els => els.map(e => e.textContent)"
    ) == ["ordered paren"]
    assert page.inner_text(f"{card} .approval-draft em") == "line italic"

    _wait_enabled(page, f"{card} [data-accept]")
    page.click(f"{card} [data-accept]")
    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    d = api.get(server, f"/api/tickets/{tid}")
    assert d["fields"]["success"]["value"] == NOOP_MARKDOWN_BODY
    assert d["fields"]["success"]["proposal"] is None
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT context_key FROM pending_worker_context WHERE worker_entity_id = ?",
            (tid,),
        ).fetchall() == []


def test_e25_edit_accept_in_review(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 edit ticket")["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success draft for edit review.",
        ticket_id=tid,
        stdin=E25_ORIG,
    )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)

    # The proposal remains one contenteditable surface. Escape discards an active
    # keyboard edit, and approving after a real keyboard edit sends exact Markdown.
    editor = page.locator(f"{card} [data-edit]")
    assert editor.get_attribute("contenteditable") == "true"
    assert page.locator(f"{card} [data-approval-draft-edit]").count() == 0
    assert page.locator(f"{card} [data-markdown-source-editor]").count() == 0
    editor.focus()
    editor.locator("h1").evaluate(SELECT_NODE_CONTENTS)
    page.keyboard.type("Saved local draft")
    editor.blur()
    assert page.inner_text(f"{card} .approval-draft h1") == "Saved local draft"
    editor.focus()
    editor.locator("h1").evaluate(SELECT_NODE_CONTENTS)
    page.keyboard.type("Discard this edit.")
    page.keyboard.press("Escape")
    assert page.inner_text(f"{card} .approval-draft h1") == "Original proposal"
    page.select_option(f"{card} [data-scope-ceiling]", "needs_plan")
    page.select_option(f"{card} [data-scope-atcap] select", "propose")
    editor.focus()
    editor.locator("h1").evaluate(SELECT_NODE_CONTENTS)
    page.keyboard.type("Original proposal v2")
    editor.locator("li").evaluate(SELECT_NODE_CONTENTS)
    page.keyboard.type("kept structure")
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.keyboard.type("serialized from DOM")
    _wait_enabled(page, f"{card} [data-accept]")
    page.click(f"{card} [data-accept]")

    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    d = api.get(server, f"/api/tickets/{tid}")
    # Exact edited Markdown source was approved.
    assert d["fields"]["success"]["value"] == E25_EDIT, repr(d["fields"]["success"]["value"])
    assert d["ceiling"] == "needs_plan", d
    assert d["at_cap"] == "propose", d
    assert d["state"] == "needs_approach", d
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT context_key, revision FROM pending_worker_context "
            "WHERE worker_entity_id = ?",
            (tid,),
        ).fetchall() == [("ticket_changed", 1)]

    # Renders as the field value on Ticket.
    ticket_page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"][data-state="needs_approach"]',
        settled=True,
    )
    assert (
        ticket_page.text_content('[data-field="success"] .markdown-block h1')
        == "Original proposal v2"
    )
    assert ticket_page.eval_on_selector_all(
        '[data-field="success"] .markdown-block ul li',
        "els => els.map(e => e.textContent)",
    ) == ["kept structure", "serialized from DOM"]


def test_review_keyboard_shortcuts(server, context_factory, open_page, cli, api):
    # The review chamber's global shortcuts (s skip, o open, cmd/ctrl+enter approve)
    # must never fire from inside an editable. Two queued tickets so a skip leaves a
    # card behind, and so cmd/ctrl+enter inside the editor is proven not to approve.
    first = cli(server, "ticket", "create", "--title", "Shortcut ticket one")["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Recap one.",
        ticket_id=first,
        stdin="# Proposal one\n\n- a",
    )
    second = cli(server, "ticket", "create", "--title", "Shortcut ticket two")["id"]
    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Recap two.",
        ticket_id=second,
        stdin="# Proposal two\n\n- b",
    )

    card = "[data-review-card]"
    page = open_page(context_factory(), server, "#/review", card, settled=True)
    entity_before = page.get_attribute(card, "data-entity-id")

    # Track every accept/approve POST so the negative assertion is deterministic —
    # no sleep. If the in-editor shortcut approved, a request would appear here.
    approve_requests: list[str] = []
    page.on(
        "request",
        lambda request: (
            approve_requests.append(request.url)
            if request.method == "POST"
            and ("/accept/" in request.url or request.url.endswith("/approve"))
            else None
        ),
    )

    # cmd/ctrl+enter with focus INSIDE the proposal editor must not approve: the
    # editor's own handler prevents it. The handler runs synchronously on keydown;
    # a subsequent real round-trip (fetching the ticket) is the flush boundary that
    # guarantees any approve request would already have been observed.
    editor = page.locator(f"{card} [data-edit]")
    editor.focus()
    page.keyboard.press("Meta+Enter")
    # Flush the browser's network: this awaited round-trip resolves only after any
    # request the (synchronous) keydown handler dispatched has already fired its
    # request event, so approve_requests is authoritative without a sleep.
    page.evaluate("() => fetch('/api/queues').then(r => r.text())")
    page.wait_for_load_state("networkidle")
    assert approve_requests == []
    assert api.get(server, f"/api/tickets/{first}")["fields"]["success"]["proposal"] is not None
    assert api.get(server, f"/api/tickets/{second}")["fields"]["success"]["proposal"] is not None
    assert page.locator("[data-review-empty]").count() == 0
    assert page.get_attribute(card, "data-entity-id") == entity_before

    # 'o' typed inside the editor is a keystroke, not open-ticket: still on /review.
    editor.focus()
    page.keyboard.type("o")
    assert page.url.endswith("#/review")

    # A focused scope <select> is also an editable target: 's'/'o' must not fire.
    page.locator(f"{card} [data-scope-ceiling]").focus()
    page.keyboard.press("s")
    page.keyboard.press("o")
    assert page.url.endswith("#/review")
    assert page.get_attribute(card, "data-entity-id") == entity_before

    # With focus outside any editable, 's' skips to the next card (entity changes).
    page.locator(".review-keys").click()
    page.keyboard.press("s")
    page.wait_for_function(
        "(prev) => { const el = document.querySelector('[data-review-card]');"
        " return !!el && el.getAttribute('data-entity-id') !== prev; }",
        arg=entity_before,
        timeout=WAIT_MS,
    )

    # cmd/ctrl+enter outside an editable approves via the same path as the button.
    _wait_enabled(page, f"{card} [data-accept]")
    approved_entity = page.get_attribute(card, "data-entity-id")
    page.locator(".review-keys").click()
    page.keyboard.press("Meta+Enter")
    page.wait_for_function(
        "(prev) => { const el = document.querySelector('[data-review-card]');"
        " return !el || el.getAttribute('data-entity-id') !== prev; }",
        arg=approved_entity,
        timeout=WAIT_MS,
    )
    approved = api.get(server, f"/api/tickets/{approved_entity}")
    assert approved["fields"]["success"]["proposal"] is None


def test_e26_chat_panel_echo_and_offline(
    server, server_factory, context_factory, open_page, cli, api
):
    pending_server = server_factory(gateway="slow_fake")
    pending_tid = cli(pending_server, "ticket", "create", "--title", "T18 pending chat ticket")[
        "id"
    ]
    pending_page = open_page(
        context_factory(),
        pending_server,
        f"#/ticket/{pending_tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    assert pending_page.inner_text('[data-ticket-status="empty"]').strip() == "empty"
    pending_page.fill("[data-chat] [data-chat-input]", "hold before first token")
    pending_page.click("[data-chat] [data-chat-send]")
    pending_page.wait_for_selector("[data-chat] [data-chat-pending]", timeout=WAIT_MS)
    pending_state = api.get(pending_server, f"/api/chat/{pending_tid}/state")
    assert pending_state["active_turn"]["status"] == "running"
    assert [msg["text"] for msg in pending_state["messages"]] == ["hold before first token"]
    assert "(none)" not in pending_page.inner_text("[data-chat] [data-chat-messages]")

    pending_selector = "[data-chat] [data-chat-pending]"
    pending_page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=pending_selector,
        timeout=WAIT_MS,
    )
    pending_page.fill("[data-chat] [data-chat-input]", TALL_CHAT_MESSAGE)
    pending_page.click("[data-chat] [data-chat-send]")
    pending_page.wait_for_selector(pending_selector, timeout=WAIT_MS)
    pending_page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=pending_selector,
        timeout=WAIT_MS,
    )
    _wait_chat_text(pending_page, "planner", "history line 23")

    pending_page.goto(pending_server.base + "/#/workspace")
    pending_page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    pending_page.add_style_tag(content="[data-chat-messages] { flex: 0 0 120px !important; }")
    pending_page.goto(pending_server.base + f"/#/ticket/{pending_tid}")
    pending_page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(pending_page, "planner", "history line 23")
    pending_thread_selector = "[data-chat] [data-chat-messages]"
    pending_jump_selector = "[data-chat] [data-chat-jump]"
    pending_page.wait_for_function(
        "selector => { const el = document.querySelector(selector);"
        " return el && el.scrollHeight > el.clientHeight; }",
        arg=pending_thread_selector,
        timeout=WAIT_MS,
    )
    initial_pending_scroll = pending_page.eval_on_selector(
        pending_thread_selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )
    assert abs(initial_pending_scroll["top"] - initial_pending_scroll["max"]) <= 1, (
        initial_pending_scroll
    )

    # The affordance is based on distance alone: it appears as soon as the reader
    # moves meaningfully upward, before any new message exists.
    pending_page.eval_on_selector(
        pending_thread_selector,
        "el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }",
    )
    pending_page.wait_for_selector(pending_jump_selector, timeout=WAIT_MS)
    assert (
        pending_page.get_attribute(pending_jump_selector, "aria-label")
        == "Jump to latest message"
    )

    # Clicking returns to the latest message and restores follow mode. The next live
    # output growth remains pinned while the turn is still active.
    pending_page.click(pending_jump_selector)
    pending_page.wait_for_function(
        "selector => { const el = document.querySelector(selector);"
        " return el && Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) <= 1; }",
        arg=pending_thread_selector,
        timeout=WAIT_MS,
    )
    pending_page.fill("[data-chat] [data-chat-input]", "follow streaming growth")
    pending_page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(pending_page, "you", "follow streaming growth")
    pending_page.wait_for_selector(pending_selector, timeout=WAIT_MS)
    following_after_send = pending_page.eval_on_selector(
        pending_thread_selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )
    assert abs(following_after_send["top"] - following_after_send["max"]) <= 1, (
        following_after_send
    )

    _wait_chat_text(pending_page, "planner", "echo: follow streaming growth")
    assert pending_page.query_selector(pending_selector) is not None
    following_during_output = pending_page.eval_on_selector(
        pending_thread_selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )
    assert abs(following_during_output["top"] - following_during_output["max"]) <= 1, (
        following_during_output
    )
    pending_page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=pending_selector,
        timeout=WAIT_MS,
    )
    following_after_settlement = pending_page.eval_on_selector(
        pending_thread_selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )
    assert abs(following_after_settlement["top"] - following_after_settlement["max"]) <= 1, (
        following_after_settlement
    )

    # Meaningful upward scrolling disables follow and preserves the viewport through
    # the human line, live planner output, and final settlement separately.
    pending_page.eval_on_selector(
        pending_thread_selector,
        "el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }",
    )
    pending_page.wait_for_selector(pending_jump_selector, timeout=WAIT_MS)
    pending_page.fill("[data-chat] [data-chat-input]", "stream without moving")
    pending_page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(pending_page, "you", "stream without moving")
    assert pending_page.eval_on_selector(pending_thread_selector, "el => el.scrollTop") == 0

    pending_page.wait_for_selector(pending_selector, timeout=WAIT_MS)
    _wait_chat_text(pending_page, "planner", "echo: stream without moving")
    assert pending_page.query_selector(pending_selector) is not None
    assert pending_page.eval_on_selector(pending_thread_selector, "el => el.scrollTop") == 0
    assert pending_page.query_selector(pending_jump_selector) is not None
    pending_page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=pending_selector,
        timeout=WAIT_MS,
    )
    assert pending_page.eval_on_selector(pending_thread_selector, "el => el.scrollTop") == 0

    # Manually returning near the bottom also restores follow mode for later growth.
    pending_page.eval_on_selector(
        pending_thread_selector,
        "el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }",
    )
    pending_page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=pending_jump_selector,
        timeout=WAIT_MS,
    )
    pending_page.fill("[data-chat] [data-chat-input]", "follow after manual return")
    pending_page.click("[data-chat] [data-chat-send]")
    pending_page.wait_for_selector(pending_selector, timeout=WAIT_MS)
    _wait_chat_text(pending_page, "planner", "echo: follow after manual return")
    manual_follow_scroll = pending_page.eval_on_selector(
        pending_thread_selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )
    assert abs(manual_follow_scroll["top"] - manual_follow_scroll["max"]) <= 1, (
        manual_follow_scroll
    )
    pending_page.wait_for_function(
        "selector => document.querySelector(selector) === null",
        arg=pending_selector,
        timeout=WAIT_MS,
    )

    # --- echo half (default echo gateway) ---
    tid = cli(server, "ticket", "create", "--title", "T18 chat ticket")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )

    # A2: a warmup send mints the session and fires the one-and-only chat flush; the
    # asserted send follows it, so no re-render can race the reply paint.
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.fill("[data-chat] [data-chat-input]", "warmup")
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_function("f => window.__plannerDebug.flushes > f", arg=f0, timeout=WAIT_MS)
    page.wait_for_selector("[data-chat] [data-chat-input]", timeout=WAIT_MS)

    assert api.get(server, f"/api/tickets/{tid}")["chat_session_key"] == "fake-sess-1"

    page.fill("[data-chat] [data-chat-input]", "hello from e2e")
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"you\"]');"
        " return els.length > 0 && els[els.length - 1].textContent === 'hello from e2e'; }",
        timeout=WAIT_MS,
    )
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"planner\"]');"
        " for (const el of els) {"
        " if (el.textContent.indexOf('echo: hello from e2e') !== -1) return true; }"
        " return false; }",
        timeout=WAIT_MS,
    )

    history = api.get(server, f"/api/chat/{tid}/history")
    assert [msg["text"] for msg in history["messages"]] == [
        "warmup",
        "echo: warmup",
        "hello from e2e",
        "echo: hello from e2e",
    ]

    # Make the recovered transcript tall enough to prove initial and subsequent
    # scroll behavior without relying on viewport-specific message heights.
    page.fill("[data-chat] [data-chat-input]", TALL_CHAT_MESSAGE)
    page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(page, "planner", "history line 23")

    # The transcript is gateway history, not component-local state. Leaving the ticket,
    # returning, and a hard reload must all recover the visible turns.
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.add_style_tag(content="[data-chat-messages] { flex: 0 0 120px !important; }")
    page.goto(server.base + f"/#/ticket/{tid}")
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(page, "you", "hello from e2e")
    _wait_chat_text(page, "planner", "echo: hello from e2e")
    _wait_chat_text(
        page,
        "planner",
        "history line 23",
    )

    thread_selector = "[data-chat] [data-chat-messages]"
    page.wait_for_function(
        "selector => { const el = document.querySelector(selector);"
        " return el && el.scrollHeight > el.clientHeight; }",
        arg=thread_selector,
        timeout=WAIT_MS,
    )
    initial_scroll = page.eval_on_selector(
        thread_selector,
        "el => ({ top: el.scrollTop, max: el.scrollHeight - el.clientHeight })",
    )
    assert abs(initial_scroll["top"] - initial_scroll["max"]) <= 1, initial_scroll

    page.eval_on_selector(thread_selector, "el => { el.scrollTop = 0; }")
    page.fill("[data-chat] [data-chat-input]", "do not move my scroll")
    page.click("[data-chat] [data-chat-send]")
    _wait_chat_text(page, "planner", "echo: do not move my scroll")
    assert page.eval_on_selector(thread_selector, "el => el.scrollTop") == 0

    page.reload()
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(page, "you", "hello from e2e")
    _wait_chat_text(page, "planner", "echo: hello from e2e")

    # --- offline half (boot-time adapter -> a second instance) ---
    off = server_factory(gateway="offline")
    tid2 = cli(off, "ticket", "create", "--title", "T18 offline ticket")["id"]
    page2 = open_page(
        context_factory(),
        off,
        f"#/ticket/{tid2}",
        'section[data-screen="ticket"] [data-chat] [data-chat-offline]',
        settled=True,
    )
    assert page2.query_selector("[data-chat] [data-chat-offline]") is not None
    assert page2.query_selector("[data-chat] [data-chat-send]") is None
    assert page2.query_selector("[data-chat] [data-chat-input]") is None


def test_e27_auto_accept_chain(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 chain ticket")["id"]

    # Unattributed direct scope: ceiling needs_plan, at_cap propose.
    g = api.direct_post(
        server, f"/api/tickets/{tid}/scope", {"ceiling": "needs_plan", "at_cap": "propose"}
    )
    assert g["ceiling"] == "needs_plan", g
    assert g["at_cap"] == "propose", g

    # Success + approach auto-accept and advance; the plan proposal parks at the ceiling.
    r1 = cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Success is ready.",
        ticket_id=tid,
        stdin=E27_SUCCESS,
    )
    assert r1["state"] == "needs_approach", r1
    r2 = cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Approach is ready.",
        ticket_id=tid,
        stdin=E27_APPROACH,
    )
    assert r2["state"] == "needs_plan", r2
    r3 = cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Plan is ready.",
        ticket_id=tid,
        stdin=E27_PLAN,
    )
    assert r3["state"] == "needs_plan", r3
    assert r3["fields"]["plan"]["proposal"]["body"] == E27_PLAN, r3

    d = api.get(server, f"/api/tickets/{tid}")
    assert d["state"] == "needs_plan", d
    assert d["ceiling"] == "needs_plan", d
    assert d["at_cap"] == "propose", d
    assert d["fields"]["success"]["value"] == E27_SUCCESS
    assert d["fields"]["approach"]["value"] == E27_APPROACH
    assert d["fields"]["plan"]["value"] is None
    assert d["fields"]["plan"]["proposal"] is not None

    q = api.get(server, "/api/queues")["approvals"]
    assert len(q) == 1, q
    assert q[0]["entity_id"] == tid, q
    assert q[0]["kind"] == "plan", q

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)
    assert page.get_attribute(card, "data-kind") == "plan"


def test_slash_menu_runs_skill(server, context_factory, open_page, cli, api):
    # The "/" menu is a read of the gateway command catalog; selecting a Skill runs
    # it on the ticket's own mind via POST /command (fake gateway -> a scripted reply).
    tid = cli(server, "ticket", "create", "--title", "T18 slash ticket")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )

    # Warmup send mints the session and fires the one-and-only chat flush, so the skill
    # run below (key reused -> no event -> no flush) can't race the reply re-render.
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.fill("[data-chat] [data-chat-input]", "warmup")
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_function("f => window.__plannerDebug.flushes > f", arg=f0, timeout=WAIT_MS)
    page.wait_for_selector("[data-chat] [data-chat-input]", timeout=WAIT_MS)

    # Typing "/" opens the catalog popover; the Skills row is present (fetched, grouped).
    page.fill("[data-chat] [data-chat-input]", "/")
    page.wait_for_selector(
        '[data-chat] [data-chat-menu] [data-chat-skill][data-chat-cmd="/writing-plans"]',
        timeout=WAIT_MS,
    )

    # Typing an alias ("/wp") keeps its canonical skill row visible (canon-aware filter).
    page.fill("[data-chat] [data-chat-input]", "/wp")
    page.wait_for_selector(
        '[data-chat] [data-chat-menu] [data-chat-skill][data-chat-cmd="/writing-plans"]',
        timeout=WAIT_MS,
    )

    # Selecting the skill runs it; the fake returns an assistant turn on this session.
    page.click('[data-chat-menu] [data-chat-cmd="/writing-plans"]')
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"you\"]');"
        " return els.length > 0 && els[els.length - 1].textContent === '/writing-plans'; }",
        timeout=WAIT_MS,
    )
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"planner\"]');"
        " for (const el of els) {"
        " if (el.textContent.indexOf('skill /writing-plans loaded') !== -1) return true; }"
        " return false; }",
        timeout=WAIT_MS,
    )


def test_slash_menu_runs_display_command(server, context_factory, open_page, cli, api):
    # A non-skill display command (/status) executes on the ticket's own mind via POST
    # /command and renders as a system line — on BOTH the menu-pick and the typed-Send
    # path. The Exit category stays out of the menu (a web chat can't quit the mind).
    tid = cli(server, "ticket", "create", "--title", "T18 display ticket")["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )

    # Warmup send mints the session and fires the one-and-only chat flush, so the command
    # runs below (key reused -> no event -> no flush) can't race the reply re-render.
    f0 = page.evaluate("window.__plannerDebug.flushes")
    page.fill("[data-chat] [data-chat-input]", "warmup")
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_function("f => window.__plannerDebug.flushes > f", arg=f0, timeout=WAIT_MS)
    page.wait_for_selector("[data-chat] [data-chat-input]", timeout=WAIT_MS)

    # Typing "/" opens the catalog popover; /status is present and Exit is hidden.
    page.fill("[data-chat] [data-chat-input]", "/")
    page.wait_for_selector(
        '[data-chat] [data-chat-menu] [data-chat-cmd="/status"]', timeout=WAIT_MS
    )
    assert page.query_selector('[data-chat-menu] [data-chat-cmd="/quit"]') is None

    # (i) Menu pick: /status runs and draws a system line with the fake's exec output.
    page.click('[data-chat-menu] [data-chat-cmd="/status"]')
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"system\"]');"
        " for (const el of els) {"
        " if (el.textContent.indexOf('exec: /status') !== -1) return true; }"
        " return false; }",
        timeout=WAIT_MS,
    )

    # (ii) Typed-Send: the same command via the send button also runs (routeSend ->
    # commandFor -> runCommand) — a second system line, not a plain-chat echo.
    page.fill("[data-chat] [data-chat-input]", "/status")
    page.click("[data-chat] [data-chat-send]")
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"system\"]');"
        " let n = 0; for (const el of els) {"
        " if (el.textContent.indexOf('exec: /status') !== -1) n += 1; }"
        " return n >= 2; }",
        timeout=WAIT_MS,
    )


def test_ticket_user_note_renders_as_own_intake_block(server, context_factory, open_page, cli):
    placeholder = "Preserve user guidance, source context, and boundaries..."
    tid = cli(
        server,
        "ticket",
        "create",
        "--title",
        "User note UI ticket",
        "--user-note",
        "Preserve this intake boundary.",
    )["id"]

    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        f'section[data-screen="ticket"][data-ticket-id="{tid}"] [data-user-note]',
        settled=True,
    )
    # The user note now lives in the stage spine, collapsed by default; open it before
    # reading or editing its body.
    assert page.locator("[data-user-note] details[open]").count() == 0
    page.click("[data-user-note] .disclosure-summary")
    page.wait_for_selector("[data-user-note] details[open]", timeout=WAIT_MS)
    assert "Preserve this intake boundary." in page.inner_text("[data-user-note]")
    assert page.query_selector("[data-user-note] [data-markdown-inline-edit]") is not None
    user_note_editor = page.locator("[data-user-note] [data-markdown-inline-edit]")
    user_note_editor.focus()
    user_note_editor.evaluate(SELECT_NODE_CONTENTS)
    page.keyboard.type("Updated intake boundary.")
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{tid}")
    ):
        user_note_editor.blur()
    page.wait_for_function(
        "() => document.querySelector('[data-user-note]')?.textContent?.includes("
        "'Updated intake boundary.')"
    )
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT context_key, revision FROM pending_worker_context "
            "WHERE worker_entity_id = ?",
            (tid,),
        ).fetchall() == [("ticket_changed", 1)]

    empty_tid = cli(server, "ticket", "create", "--title", "Empty user note UI ticket")["id"]
    empty_page = open_page(
        context_factory(),
        server,
        f"#/ticket/{empty_tid}",
        f'section[data-screen="ticket"][data-ticket-id="{empty_tid}"] [data-user-note]',
        settled=True,
    )
    empty_page.click("[data-user-note] .disclosure-summary")
    empty_page.wait_for_selector("[data-user-note] details[open]", timeout=WAIT_MS)
    assert "No user note yet." not in empty_page.inner_text("[data-user-note]")
    assert (
        empty_page.get_attribute("[data-user-note] [data-markdown-inline-edit]", "data-ph")
        == placeholder
    )
    assert (
        empty_page.locator("[data-user-note] [data-markdown-inline-edit]").get_attribute(
            "contenteditable"
        )
        == "true"
    )
    assert empty_page.locator("[data-user-note] [data-markdown-edit]").count() == 0


def test_ticket_implementer_assignment_edits_in_facts_without_changing_workflow(
    server, context_factory, open_page, cli, api
):
    tid = cli(server, "ticket", "create", "--title", "Implementer assignment UI ticket")["id"]
    ready = f'section[data-screen="ticket"][data-ticket-id="{tid}"]'
    page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        ready,
        settled=True,
    )
    implementer = ".ticket-facts [data-implementer]"

    # The assignment is one inline selector in the existing facts row, not a new
    # edit/save/cancel flow.
    assert page.locator(implementer).count() == 1
    initial = api.get(server, f"/api/tickets/{tid}")
    initial_state = initial["state"]
    initial_ticket_status = initial["ticket_status"]
    assert initial["implementer"] is None

    def wait_for_assignment(value: str, label: str) -> None:
        page.wait_for_function(
            """({ selector, value, label }) => {
                const root = document.querySelector(selector);
                const select = root?.querySelector('select');
                const pill = root?.querySelector('.pill');
                const visibleLabel = pill
                    ? Array.from(pill.childNodes)
                        .filter(node => node.nodeType === Node.TEXT_NODE)
                        .map(node => node.textContent || '')
                        .join('')
                        .trim()
                    : '';
                return select?.value === value && visibleLabel === label;
            }""",
            arg={"selector": implementer, "value": value, "label": label},
            timeout=WAIT_MS,
        )

    def assert_assignment_without_workflow_change(expected: str | None) -> None:
        detail = api.get(server, f"/api/tickets/{tid}")
        assert detail["implementer"] == expected
        assert detail["state"] == initial_state
        assert detail["ticket_status"] == initial_ticket_status
        assert page.get_attribute(ready, "data-state") == initial_state
        assert (
            page.get_attribute("[data-ticket-status]", "data-ticket-status")
            == initial_ticket_status
        )
        assert page.locator(f"{implementer} button").count() == 0
        assert page.locator(
            f"{implementer} [data-edit], {implementer} [data-save], "
            f"{implementer} [data-cancel]"
        ).count() == 0
        for action in ("Edit", "Save", "Cancel"):
            assert page.get_by_role("button", name=action, exact=True).count() == 0

    wait_for_assignment("", "(unassigned)")
    assert_assignment_without_workflow_change(None)

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{tid}")
    ):
        page.select_option(f"{implementer} select", "khushal")
    wait_for_assignment("khushal", "Khushal")
    assert_assignment_without_workflow_change("khushal")

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{tid}")
    ):
        page.select_option(f"{implementer} select", "hermes_codex")
    wait_for_assignment("hermes_codex", "Hermes with Codex")
    assert_assignment_without_workflow_change("hermes_codex")

    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and response.url.endswith(f"/api/tickets/{tid}")
    ):
        page.select_option(f"{implementer} select", "")
    wait_for_assignment("", "(unassigned)")
    assert_assignment_without_workflow_change(None)

    page.reload()
    page.wait_for_selector(f"{ready} {implementer}", timeout=WAIT_MS)
    wait_for_assignment("", "(unassigned)")
    assert_assignment_without_workflow_change(None)
