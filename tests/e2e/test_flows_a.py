"""E2E flows A — SPEC §18.3 items 22-27 (Playwright, chromium, two contexts where
stated).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item may exist across the whole e2e
suite, so there is no parametrize and every shared helper below has a non-``test_``
name. Assertions use the SPEC's exact values (states, ceilings, scope pairs, the
minted session key, rendered markdown structure) — never weakened approximations.
"""

from __future__ import annotations

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
E27_SUCCESS = "Success body for the chain."
E27_APPROACH = "Approach body for the chain."
E27_PLAN = "Plan body for the chain."


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
        "({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg=\"${who}\"]`))"
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
    page_b.wait_for_function("f => window.__plannerDebug.flushes > f", arg=flushes_b,
                             timeout=WAIT_MS)
    assert page_b.query_selector(card) is None
    assert page_a.query_selector(card) is None

    flushes_b = page_b.evaluate("window.__plannerDebug.flushes")
    api.human_post(server, "/api/day/today/tickets", {"ticket_id": tid})

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
        "worker", "propose", "--body-file", "-", "--recap", "Success criteria proposed.",
        ticket_id=tid,
        stdin=MD_BODY,
    )

    ready = f'section[data-screen="ticket"][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, f"#/ticket/{tid}", ready, settled=True)

    # Rendered: the proposal's markdown structure, with exact texts.
    b = '[data-approval-block] .approval-draft .markdown-block'
    assert page.inner_text(f"{b} h1") == "Success criteria"
    assert page.eval_on_selector_all(
        f"{b} ul li", "els => els.map(e => e.textContent)"
    ) == ["six anchored tests", "two browser contexts"]
    assert page.eval_on_selector_all(
        f"{b} ol li", "els => els.map(e => e.textContent)"
    ) == ["boot server", "drive UI"]
    assert page.inner_text(f"{b} strong") == "exact"
    paras = page.eval_on_selector_all(f"{b} p", "els => els.map(e => e.textContent)")
    assert len(paras) == 2, paras
    assert paras[-1] == "Done when verify flips items 22-27."

    # Focusing the contenteditable draft keeps the rendered markdown DOM in place.
    page.focus('[data-approval-block] .approval-draft')
    assert page.inner_text(f"{b} h1") == "Success criteria"
    assert page.eval_on_selector_all(
        f"{b} ul li", "els => els.map(e => e.textContent)"
    ) == ["six anchored tests", "two browser contexts"]
    assert page.eval_on_selector_all(
        f"{b} ol li", "els => els.map(e => e.textContent)"
    ) == ["boot server", "drive UI"]
    assert page.inner_text(f"{b} strong") == "exact"

    # Did not advance: still needs_success, and the value is still unset.
    assert page.get_attribute(ready, "data-state") == "needs_success"
    assert api.get(server, f"/api/tickets/{tid}")["fields"]["success"]["value"] is None


def test_e24_accept_in_review(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 review ticket")["id"]
    cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Success ready for review.",
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
    assert d["ceiling"] == "needs_approach", d      # default approval scope is next stage
    assert d["at_cap"] == "propose", d
    assert d["fields"]["success"]["value"] == E24_BODY
    assert d["fields"]["success"]["proposal"] is None


def test_review_return_for_revision_sends_guidance_and_clears_queue(
    server, context_factory, open_page, cli, api
):
    tid = cli(server, "ticket", "create", "--title", "Revision review ticket")["id"]
    cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Needs revision.",
        ticket_id=tid,
        stdin="Too much detail.",
    )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)
    page.fill(f"{card} [data-review-revision-input]", "Make it shorter.")
    page.click(f"{card} [data-review-revision-send]")
    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    ticket = api.get(server, f"/api/tickets/{tid}")
    assert ticket["state"] == "needs_success"
    assert ticket["ticket_status"] == "empty"
    assert ticket["fields"]["success"]["value"] is None
    assert ticket["fields"]["success"]["proposal"] is None
    assert api.get(server, "/api/queues")["approvals"] == []
    chat = api.get(server, f"/api/chat/{tid}/state")
    assert chat["messages"][-1]["role"] == "human"
    assert chat["messages"][-1]["text"].endswith("\n\nMake it shorter.")


def test_markdown_approval_focus_noop_keeps_raw_source(
    server, context_factory, open_page, cli, api
):
    tid = cli(server, "ticket", "create", "--title", "Markdown noop ticket")["id"]
    cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Raw forms ready.",
        ticket_id=tid,
        stdin=NOOP_MARKDOWN_BODY,
    )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)

    # Focus leaves the rendered structure in place, but an untouched focus/approve must
    # not serialize the DOM back to canonical markdown forms.
    page.focus(f"{card} [data-edit]")
    assert page.inner_text(f"{card} [data-edit] h1") == "Raw forms"
    assert page.eval_on_selector_all(
        f"{card} [data-edit] ul li", "els => els.map(e => e.textContent)"
    ) == ["star bullet"]
    assert page.eval_on_selector_all(
        f"{card} [data-edit] ol li", "els => els.map(e => e.textContent)"
    ) == ["ordered paren"]
    assert page.inner_text(f"{card} [data-edit] em") == "line italic"

    _wait_enabled(page, f"{card} [data-accept]")
    page.click(f"{card} [data-accept]")
    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    d = api.get(server, f"/api/tickets/{tid}")
    assert d["fields"]["success"]["value"] == NOOP_MARKDOWN_BODY
    assert d["fields"]["success"]["proposal"] is None


def test_e25_edit_accept_in_review(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 edit ticket")["id"]
    cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Success draft for edit review.",
        ticket_id=tid,
        stdin=E25_ORIG,
    )

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)

    # Focusing keeps the rendered markdown structure editable in place.
    page.focus(f"{card} [data-edit]")
    assert page.inner_text(f"{card} [data-edit] h1") == "Original proposal"
    assert page.eval_on_selector_all(
        f"{card} [data-edit] ul li", "els => els.map(e => e.textContent)"
    ) == ["old structure"]

    page.locator(f"{card} [data-edit] h1").click()
    page.keyboard.press("End")
    page.keyboard.type(" v2")
    page.locator(f"{card} [data-edit]").evaluate(
        """(el) => {
            el.querySelector('li').textContent = 'kept structure';
            const extra = document.createElement('li');
            extra.textContent = 'serialized from DOM';
            el.querySelector('ul').appendChild(extra);
        }"""
    )
    page.locator(f"{card} [data-edit]").blur()
    page.select_option(f"{card} [data-scope-ceiling]", "needs_plan")
    page.select_option(f"{card} [data-scope-atcap] select", "propose")
    _wait_enabled(page, f"{card} [data-accept]")
    page.click(f"{card} [data-accept]")

    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    d = api.get(server, f"/api/tickets/{tid}")
    # Exact markdown serialized from the edited rendered DOM.
    assert d["fields"]["success"]["value"] == E25_EDIT, repr(d["fields"]["success"]["value"])
    assert d["ceiling"] == "needs_plan", d
    assert d["at_cap"] == "propose", d
    assert d["state"] == "needs_approach", d

    # Renders as the field value on Ticket.
    ticket_page = open_page(
        context_factory(),
        server,
        f"#/ticket/{tid}",
        'section[data-screen="ticket"][data-state="needs_approach"]',
        settled=True,
    )
    assert ticket_page.text_content(
        '[data-field="success"] .markdown-block h1'
    ) == "Original proposal v2"
    assert ticket_page.eval_on_selector_all(
        '[data-field="success"] .markdown-block ul li',
        "els => els.map(e => e.textContent)",
    ) == ["kept structure", "serialized from DOM"]


def test_e26_chat_panel_echo_and_offline(
    server, server_factory, context_factory, open_page, cli, api
):
    pending_server = server_factory(gateway="slow_fake")
    pending_tid = cli(
        pending_server, "ticket", "create", "--title", "T18 pending chat ticket"
    )["id"]
    pending_page = open_page(
        context_factory(),
        pending_server,
        f"#/ticket/{pending_tid}",
        'section[data-screen="ticket"] [data-chat] [data-chat-input]',
        settled=True,
    )
    assert pending_page.text_content('[data-ticket-status="empty"]') == "status empty"
    pending_page.fill('[data-chat] [data-chat-input]', "hold before first token")
    pending_page.click('[data-chat] [data-chat-send]')
    pending_page.wait_for_selector('[data-chat] [data-chat-pending]', timeout=WAIT_MS)
    pending_state = api.get(pending_server, f"/api/chat/{pending_tid}/state")
    assert pending_state["active_turn"]["status"] == "running"
    assert [msg["text"] for msg in pending_state["messages"]] == ["hold before first token"]
    assert "(none)" not in pending_page.inner_text("[data-chat] [data-chat-messages]")

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
    page.fill('[data-chat] [data-chat-input]', "warmup")
    page.click('[data-chat] [data-chat-send]')
    page.wait_for_function("f => window.__plannerDebug.flushes > f", arg=f0, timeout=WAIT_MS)
    page.wait_for_selector('[data-chat] [data-chat-input]', timeout=WAIT_MS)

    assert api.get(server, f"/api/tickets/{tid}")["chat_session_key"] == "fake-sess-1"

    page.fill('[data-chat] [data-chat-input]', "hello from e2e")
    page.click('[data-chat] [data-chat-send]')
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

    # The transcript is gateway history, not component-local state. Leaving the ticket,
    # returning, and a hard reload must all recover the visible turns.
    page.goto(server.base + "/#/workspace")
    page.wait_for_selector('section[data-screen="workspace"]', timeout=WAIT_MS)
    page.goto(server.base + f"/#/ticket/{tid}")
    page.wait_for_selector(
        'section[data-screen="ticket"] [data-chat] [data-chat-input]', timeout=WAIT_MS
    )
    _wait_chat_text(page, "you", "hello from e2e")
    _wait_chat_text(page, "planner", "echo: hello from e2e")

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
    assert page2.query_selector('[data-chat] [data-chat-offline]') is not None
    assert page2.query_selector('[data-chat] [data-chat-send]') is None
    assert page2.query_selector('[data-chat] [data-chat-input]') is None


def test_e27_auto_accept_chain(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 chain ticket")["id"]

    # Human scope (no headers -> /scope accepts it): ceiling needs_plan, at_cap propose.
    g = api.human_post(
        server, f"/api/tickets/{tid}/scope", {"ceiling": "needs_plan", "at_cap": "propose"}
    )
    assert g["ceiling"] == "needs_plan", g
    assert g["at_cap"] == "propose", g

    # Success + approach auto-accept and advance; the plan proposal parks at the ceiling.
    r1 = cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Success is ready.",
        ticket_id=tid,
        stdin=E27_SUCCESS,
    )
    assert r1["state"] == "needs_approach", r1
    r2 = cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Approach is ready.",
        ticket_id=tid,
        stdin=E27_APPROACH,
    )
    assert r2["state"] == "needs_plan", r2
    r3 = cli(
        server,
        "worker", "propose", "--body-file", "-", "--recap", "Plan is ready.",
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
    page.fill('[data-chat] [data-chat-input]', "warmup")
    page.click('[data-chat] [data-chat-send]')
    page.wait_for_function("f => window.__plannerDebug.flushes > f", arg=f0, timeout=WAIT_MS)
    page.wait_for_selector('[data-chat] [data-chat-input]', timeout=WAIT_MS)

    # Typing "/" opens the catalog popover; the Skills row is present (fetched, grouped).
    page.fill('[data-chat] [data-chat-input]', "/")
    page.wait_for_selector(
        '[data-chat] [data-chat-menu] [data-chat-skill][data-chat-cmd="/writing-plans"]',
        timeout=WAIT_MS,
    )

    # Typing an alias ("/wp") keeps its canonical skill row visible (canon-aware filter).
    page.fill('[data-chat] [data-chat-input]', "/wp")
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
    page.fill('[data-chat] [data-chat-input]', "warmup")
    page.click('[data-chat] [data-chat-send]')
    page.wait_for_function("f => window.__plannerDebug.flushes > f", arg=f0, timeout=WAIT_MS)
    page.wait_for_selector('[data-chat] [data-chat-input]', timeout=WAIT_MS)

    # Typing "/" opens the catalog popover; /status is present and Exit is hidden.
    page.fill('[data-chat] [data-chat-input]', "/")
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
    page.fill('[data-chat] [data-chat-input]', "/status")
    page.click('[data-chat] [data-chat-send]')
    page.wait_for_function(
        "() => { const els = document.querySelectorAll('[data-chat-msg=\"system\"]');"
        " let n = 0; for (const el of els) {"
        " if (el.textContent.indexOf('exec: /status') !== -1) n += 1; }"
        " return n >= 2; }",
        timeout=WAIT_MS,
    )
