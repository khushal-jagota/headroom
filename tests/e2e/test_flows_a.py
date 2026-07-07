"""E2E flows A — SPEC §18.3 items 22-27 (Playwright, chromium, two contexts where
stated).

One test per acceptance item, its name carrying the ``test_eNN_`` anchor the verify
scorer matches: exactly one anchored match per item may exist across the whole e2e
suite, so there is no parametrize and every shared helper below has a non-``test_``
name. Assertions use the SPEC's exact values (states, ceilings, grant pairs, the
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
E25_ORIG = "Original proposal body."
E25_EDIT = "Human-edited success criteria. "   # trailing space: stored + asserted verbatim
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


def test_e22_cli_create_live_board(server, context_factory, open_page, cli):
    board = 'section[data-screen="board"]'
    ctx_a = context_factory()
    ctx_b = context_factory()
    page_a = open_page(ctx_a, server, "#/board", board, settled=False)
    page_b = open_page(ctx_b, server, "#/board", board, settled=False)

    for page in (page_a, page_b):
        count = page.eval_on_selector_all(
            '[data-column="needs_success"] [data-card]', "els => els.length"
        )
        assert count == 0, count
    flushes_b = page_b.evaluate("window.__plannerDebug.flushes")

    created = cli(server, "ticket", "create", "--title", "T18 board ticket")
    tid = created["id"]
    assert created["state"] == "needs_success", created

    card = f'[data-column="needs_success"] [data-card][data-ticket-id="{tid}"]'
    # No reload, no goto: the card can only arrive via a WS-flush re-render.
    _wait_present(page_b, card)
    assert "T18 board ticket" in page_b.inner_text(card)
    assert page_b.evaluate("window.__plannerDebug.flushes") > flushes_b

    _wait_present(page_a, card)
    assert "T18 board ticket" in page_a.inner_text(card)


def test_e23_env_pinned_propose(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 propose ticket")["id"]
    # PLAN_TICKET_ID resolves the ticket (no positional id); stdin carries the body.
    cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=MD_BODY)

    ready = f'section[data-screen="ticket"][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, f"#/ticket/{tid}", ready, settled=True)

    # Rendered: the proposal's markdown structure, with exact texts. Read the draft's
    # rendered markdown AT REST first — focusing it (below) swaps it to raw source.
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

    # Intact: focusing the contenteditable draft swaps it to the RAW markdown source,
    # which carries the proposal body byte-for-byte.
    page.focus('[data-approval-block] .approval-draft')
    assert page.text_content('[data-approval-block] .approval-draft') == MD_BODY

    # Did not advance: still needs_success, and the value is still unset.
    assert page.get_attribute(ready, "data-state") == "needs_success"
    assert api.get(server, f"/api/tickets/{tid}")["fields"]["success"]["value"] is None


def test_e24_accept_in_review(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 review ticket")["id"]
    cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=E24_BODY)

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

    # Accept-impossible ladder: nothing picked -> ceiling only -> both halves.
    assert page_a.is_disabled(f"{card} [data-accept]")
    page_a.select_option(f"{card} [data-grant-ceiling]", "none")
    assert page_a.is_disabled(f"{card} [data-accept]")
    page_a.check(f'{card} [data-grant-atcap] input[value="stop"]')
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
    assert d["ceiling"] == "needs_approach", d      # "no further" pins the ceiling
    assert d["at_cap"] == "stop", d
    assert d["fields"]["success"]["value"] == E24_BODY
    assert d["fields"]["success"]["proposal"] is None


def test_e25_edit_accept_in_review(server, context_factory, open_page, cli, api):
    tid = cli(server, "ticket", "create", "--title", "T18 edit ticket")["id"]
    cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=E25_ORIG)

    card = f'[data-review-card][data-entity-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card, settled=True)

    # Prefill first (before any fill).
    assert page.input_value(f"{card} [data-edit]") == E25_ORIG

    page.fill(f"{card} [data-edit]", E25_EDIT)
    page.select_option(f"{card} [data-grant-ceiling]", "needs_plan")
    page.check(f'{card} [data-grant-atcap] input[value="propose"]')
    _wait_enabled(page, f"{card} [data-accept]")
    page.click(f"{card} [data-accept]")

    page.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)

    d = api.get(server, f"/api/tickets/{tid}")
    # Exact, trailing space included.
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
    assert "Human-edited success criteria." in ticket_page.text_content(
        '[data-field="success"] .markdown-block'
    )


def test_e26_chat_panel_echo_and_offline(
    server, server_factory, context_factory, open_page, cli, api
):
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

    # Human grant (no headers -> /grant accepts it): ceiling needs_plan, at_cap propose.
    g = api.human_post(
        server, f"/api/tickets/{tid}/grant", {"ceiling": "needs_plan", "at_cap": "propose"}
    )
    assert g["ceiling"] == "needs_plan", g
    assert g["at_cap"] == "propose", g

    # Success + approach auto-accept and advance; the plan proposal parks at the ceiling.
    r1 = cli(server, "propose", "success", "--body-file", "-", ticket_id=tid, stdin=E27_SUCCESS)
    assert r1["state"] == "needs_approach", r1
    r2 = cli(server, "propose", "approach", "--body-file", "-", ticket_id=tid, stdin=E27_APPROACH)
    assert r2["state"] == "needs_plan", r2
    r3 = cli(server, "propose", "plan", "--body-file", "-", ticket_id=tid, stdin=E27_PLAN)
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
