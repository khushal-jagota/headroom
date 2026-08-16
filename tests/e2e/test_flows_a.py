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
from collections.abc import Callable

from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle, open_status_group

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


def _add_to_today(api: ApiHelper, server: ServerHandle, *ticket_ids: str) -> None:
    for ticket_id in ticket_ids:
        api.direct_post(server, "/api/day/today/tickets", {"ticket_id": ticket_id})


def _wait_chat_text(page: Page, who: str, text: str) -> None:
    page.wait_for_function(
        '({ who, text }) => Array.from(document.querySelectorAll(`[data-chat-msg="${who}"]`))'
        ".some(el => el.textContent.includes(text))",
        arg={"who": who, "text": text},
        timeout=WAIT_MS,
    )


def test_e22_cli_create_live_board(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    board = 'section[data-screen="workspace"]'
    ctx_a = context_factory()
    ctx_b = context_factory()
    page_a = open_page(ctx_a, server, "#/workspace?view=tickets", board)
    page_b = open_page(ctx_b, server, "#/workspace?view=tickets", board)

    for page in (page_a, page_b):
        count = page.eval_on_selector_all(
            '[data-card][data-ticket-stage="needs_kickoff"]', "els => els.length"
        )
        assert count == 0, count
    flushes_b = page_b.evaluate("window.__plannerDebug.flushes")

    # The rail holds the groups that want the reader, so the new Ticket is created the
    # way a Ticket that wants one is: parked on its kickoff, in Waiting for Kickoff. What
    # this test proves is the arrival, not the group.
    created = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--backlog",
        "--title",
        "T18 board ticket",
        "--kickoff-note",
        "Waiting on the kickoff",
    )
    tid = created["id"]
    assert created["stage"] == "needs_kickoff", created

    card = f'[data-card][data-ticket-stage="needs_kickoff"][data-ticket-id="{tid}"]'
    # No reload, no goto: the today's-roster card arrives via a change-stream refetch of
    # the open pages. Its group is drawn only once it holds a Ticket and arrives shut, so
    # each page opens it after the card lands. The arrival is the subject, not the fold.
    _wait_present(page_b, card)
    open_status_group(page_b, "waiting_for_kickoff")
    assert "T18 board ticket" in page_b.inner_text(card)
    assert page_b.evaluate("window.__plannerDebug.flushes") > flushes_b

    _wait_present(page_a, card)
    open_status_group(page_a, "waiting_for_kickoff")
    assert "T18 board ticket" in page_a.inner_text(card)

    # Workspace follows today's membership: removing the Ticket removes its card.
    cli(server, "day", "remove-ticket", tid, "--date", "today")
    page_b.wait_for_function(
        "sel => document.querySelector(sel) === null", arg=card, timeout=WAIT_MS
    )
    page_a.wait_for_function(
        "sel => document.querySelector(sel) === null", arg=card, timeout=WAIT_MS
    )


def test_e23_env_pinned_propose(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "T18 propose ticket"
    )["id"]
    # PLAN_TICKET_ID resolves the ticket (no positional id); stdin carries the body.
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Success criteria proposed.",
        ticket_id=tid,
        stdin=MD_BODY,
    )

    ready = f'section[data-screen="ticket"][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, f"#/workspace/{tid}", ready)

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
    assert page.get_attribute(ready, "data-stage") == "needs_success"
    assert api.get(server, f"/api/tickets/{tid}")["fields"]["success"]["value"] is None


def test_e24_accept_in_review(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "T18 review ticket"
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Success ready for review.",
        ticket_id=tid,
        stdin=E24_BODY,
    )

    _add_to_today(api, server, tid)
    card = f'[data-review-card][data-ticket-id="{tid}"]'
    page_a = open_page(context_factory(), server, "#/review", card)
    assert page_a.get_attribute(card, "data-field") == "success"

    # Second context watches the ticket page for the flip.
    page_b = open_page(
        context_factory(),
        server,
        f"#/workspace/{tid}",
        'section[data-screen="ticket"][data-stage="needs_success"]',
    )

    # Scope defaults to the next stage and then propose.
    assert page_a.locator(f"{card} [data-scope-ceiling]").input_value() == "needs_approach"
    assert page_a.locator(f"{card} [data-scope-atcap] select").input_value() == "propose"
    _wait_enabled(page_a, f"{card} [data-accept]")
    page_a.click(f"{card} [data-accept]")

    # Review departure (only decision on a fresh DB).
    page_a.wait_for_selector("[data-review-empty]", timeout=WAIT_MS)
    assert api.get(server, "/api/review")["items"] == []

    # Second context updates without reload — the flip arrives via the change stream.
    page_b.wait_for_function(
        "() => { const s = document.querySelector('section[data-screen=\"ticket\"]');"
        " return !!s && s.getAttribute('data-stage') === 'needs_approach'; }",
        timeout=WAIT_MS,
    )

    d = api.get(server, f"/api/tickets/{tid}")
    assert d["stage"] == "needs_approach", d
    assert d["ceiling"] == "needs_approach", d  # default approval scope is next stage
    assert d["at_cap"] == "propose", d
    assert d["fields"]["success"]["value"] == E24_BODY
    assert d["fields"]["success"]["proposal"] is None


def test_markdown_approval_focus_noop_keeps_raw_source(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Markdown noop ticket",
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Raw forms ready.",
        ticket_id=tid,
        stdin=NOOP_MARKDOWN_BODY,
    )

    _add_to_today(api, server, tid)
    card = f'[data-review-card][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card)

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
        assert (
            conn.execute(
                "SELECT context_key FROM pending_worker_context WHERE worker_entity_id = ?",
                (tid,),
            ).fetchall()
            == []
        )


def test_e25_edit_accept_in_review(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "T18 edit ticket")[
        "id"
    ]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Success draft for edit review.",
        ticket_id=tid,
        stdin=E25_ORIG,
    )

    _add_to_today(api, server, tid)
    card = f'[data-review-card][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card)

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
    assert d["stage"] == "needs_approach", d
    with sqlite3.connect(server.db_path) as conn:
        assert conn.execute(
            "SELECT context_key, revision FROM pending_worker_context WHERE worker_entity_id = ?",
            (tid,),
        ).fetchall() == [("ticket_changed", 1)]

    # Renders as the field value on Ticket.
    ticket_page = open_page(
        context_factory(),
        server,
        f"#/workspace/{tid}",
        'section[data-screen="ticket"][data-stage="needs_approach"]',
    )
    assert (
        ticket_page.text_content('[data-field="success"] .markdown-block h1')
        == "Original proposal v2"
    )
    assert ticket_page.eval_on_selector_all(
        '[data-field="success"] .markdown-block ul li',
        "els => els.map(e => e.textContent)",
    ) == ["kept structure", "serialized from DOM"]


def test_review_keyboard_shortcuts(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    # The review chamber's global shortcuts (s skip, o open, cmd/ctrl+enter approve)
    # must never fire from inside an editable. Two Review decisions so a skip leaves a
    # card behind, and so cmd/ctrl+enter inside the editor is proven not to approve.
    first = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Shortcut ticket one",
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Recap one.",
        ticket_id=first,
        stdin="# Proposal one\n\n- a",
    )
    second = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Shortcut ticket two",
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Recap two.",
        ticket_id=second,
        stdin="# Proposal two\n\n- b",
    )

    _add_to_today(api, server, first, second)
    card = "[data-review-card]"
    page = open_page(context_factory(), server, "#/review", card)
    ticket_before = page.get_attribute(card, "data-ticket-id")

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
    # Flush the browser's network by awaiting a real round-trip: it resolves only after
    # any request the (synchronous) keydown handler dispatched has already fired its
    # request event, so approve_requests is authoritative without a sleep. (Waiting for
    # an idle network would never return — the change stream is open for the page's life.)
    page.evaluate("async () => { await (await fetch('/api/review')).text(); }")
    assert approve_requests == []
    assert api.get(server, f"/api/tickets/{first}")["fields"]["success"]["proposal"] is not None
    assert api.get(server, f"/api/tickets/{second}")["fields"]["success"]["proposal"] is not None
    assert page.locator("[data-review-empty]").count() == 0
    assert page.get_attribute(card, "data-ticket-id") == ticket_before

    # 'o' typed inside the editor is a keystroke, not open-ticket: still on /review.
    editor.focus()
    page.keyboard.type("o")
    assert page.url.endswith("#/review")

    # A focused scope <select> is also an editable target: 's'/'o' must not fire.
    page.locator(f"{card} [data-scope-ceiling]").focus()
    page.keyboard.press("s")
    page.keyboard.press("o")
    assert page.url.endswith("#/review")
    assert page.get_attribute(card, "data-ticket-id") == ticket_before

    # With focus outside any editable, 's' skips to the next Ticket decision.
    page.locator(".review-keys").click()
    page.keyboard.press("s")
    page.wait_for_function(
        "(prev) => { const el = document.querySelector('[data-review-card]');"
        " return !!el && el.getAttribute('data-ticket-id') !== prev; }",
        arg=ticket_before,
        timeout=WAIT_MS,
    )

    # cmd/ctrl+enter outside an editable approves via the same path as the button.
    _wait_enabled(page, f"{card} [data-accept]")
    approved_ticket = page.get_attribute(card, "data-ticket-id")
    page.locator(".review-keys").click()
    page.keyboard.press("Meta+Enter")
    page.wait_for_function(
        "(prev) => { const el = document.querySelector('[data-review-card]');"
        " return !el || el.getAttribute('data-ticket-id') !== prev; }",
        arg=approved_ticket,
        timeout=WAIT_MS,
    )
    approved = api.get(server, f"/api/tickets/{approved_ticket}")
    assert approved["fields"]["success"]["proposal"] is None


def test_needs_user_requests_share_the_review_walk(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    first_help = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "First Worker question",
    )["id"]
    cli(server, "worker", "request-user-help", ticket_id=first_help)
    second_help = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Second Worker question",
    )["id"]
    cli(server, "worker", "request-user-help", ticket_id=second_help)
    proposal = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Ordinary proposal",
    )["id"]
    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Proposal recap.",
        ticket_id=proposal,
        stdin="Proposal body.",
    )
    _add_to_today(api, server, first_help, second_help, proposal)

    # Give the two help requests explicit earlier waits so this browser test proves
    # the cross-kind order rather than depending on random Ticket IDs at equal times.
    with sqlite3.connect(server.db_path) as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status_changed_at = 1000 WHERE id = ?",
            (first_help,),
        )
        conn.execute(
            "UPDATE tickets SET ticket_status_changed_at = 2000 WHERE id = ?",
            (second_help,),
        )

    review_items = api.get(server, "/api/review")["items"]
    assert [item["review_item_type"] for item in review_items] == [
        "needs_user",
        "needs_user",
        "proposal",
    ]
    assert [item["ticket_id"] for item in review_items] == [first_help, second_help, proposal]

    help_card = '[data-review-card][data-review-item-type="needs_user"]'
    walk_page = open_page(context_factory(), server, "#/review", help_card)
    open_page_for_shortcut = open_page(context_factory(), server, "#/review", help_card)
    badge = walk_page.locator('a[data-screen="review"] .nav-badge')

    assert walk_page.get_attribute(help_card, "data-ticket-id") == first_help
    assert walk_page.text_content(f"{help_card} .review-ticket-title") == "First Worker question"
    assert walk_page.text_content(f"{help_card} .review-context-label") == "Worker needs your input"
    assert walk_page.locator(f"{help_card} [data-accept]").count() == 0
    assert walk_page.locator(f"{help_card} [data-review-revision]").count() == 0
    keys_text = walk_page.locator(".review-keys").text_content()
    assert keys_text is not None
    assert " ".join(keys_text.split()) == "S skip · O open ticket"
    badge_text = badge.text_content()
    assert badge_text is not None
    assert badge_text.strip() == "3"

    # Open uses the same global shortcut as a proposal item.
    open_page_for_shortcut.locator(".review-keys").click()
    open_page_for_shortcut.keyboard.press("o")
    open_page_for_shortcut.wait_for_url(f"**/#/workspace/{first_help}", timeout=WAIT_MS)

    # Skip advances within the same mixed queue without changing canonical state.
    walk_page.locator(".review-keys").click()
    walk_page.keyboard.press("s")
    walk_page.wait_for_function(
        "(ticketId) => document.querySelector('[data-review-card]')"
        "?.getAttribute('data-ticket-id') === ticketId",
        arg=second_help,
        timeout=WAIT_MS,
    )

    # Leaving needs_user removes the current item; the locally skipped first help
    # remains counted, and Review advances to the ordinary proposal.
    api.direct_post(server, f"/api/tickets/{second_help}/release", {})
    walk_page.wait_for_function(
        "(ticketId) => document.querySelector('[data-review-card]')"
        "?.getAttribute('data-ticket-id') === ticketId",
        arg=proposal,
        timeout=WAIT_MS,
    )
    walk_page.wait_for_function(
        "() => document.querySelector('a[data-screen=\"review\"] .nav-badge')"
        "?.textContent.trim() === '2'",
        timeout=WAIT_MS,
    )
    assert walk_page.get_attribute("[data-review-card]", "data-review-item-type") == "proposal"
    assert [item["ticket_id"] for item in api.get(server, "/api/review")["items"]] == [
        first_help,
        proposal,
    ]



def test_e27_auto_accept_chain(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "T18 chain ticket")[
        "id"
    ]

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
        "--recap",
        "Success is ready.",
        ticket_id=tid,
        stdin=E27_SUCCESS,
    )
    assert r1["stage"] == "needs_approach", r1
    r2 = cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Approach is ready.",
        ticket_id=tid,
        stdin=E27_APPROACH,
    )
    assert r2["stage"] == "needs_plan", r2
    r3 = cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Plan is ready.",
        ticket_id=tid,
        stdin=E27_PLAN,
    )
    assert r3["stage"] == "needs_plan", r3
    assert r3["fields"]["plan"]["proposal"]["body"] == E27_PLAN, r3

    d = api.get(server, f"/api/tickets/{tid}")
    assert d["stage"] == "needs_plan", d
    assert d["ceiling"] == "needs_plan", d
    assert d["at_cap"] == "propose", d
    assert d["fields"]["success"]["value"] == E27_SUCCESS
    assert d["fields"]["approach"]["value"] == E27_APPROACH
    assert d["fields"]["plan"]["value"] is None
    assert d["fields"]["plan"]["proposal"] is not None

    _add_to_today(api, server, tid)
    decisions = api.get(server, "/api/review")["items"]
    assert len(decisions) == 1, decisions
    assert decisions[0]["ticket_id"] == tid, decisions
    assert decisions[0]["field"] == "plan", decisions

    card = f'[data-review-card][data-ticket-id="{tid}"]'
    page = open_page(context_factory(), server, "#/review", card)
    assert page.get_attribute(card, "data-field") == "plan"



def test_pending_kickoff_edits_and_approves_before_five_worker_stages(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Draft title",
        "--kickoff-note",
        "Draft premise",
    )["id"]
    page = open_page(
        context_factory(),
        server,
        f"#/workspace/{tid}",
        f'section[data-screen="ticket"][data-ticket-id="{tid}"][data-stage="needs_kickoff"]',
    )
    assert page.locator('details[data-field="kickoff"] [data-approval-block]').count() == 1
    assert page.locator("details[data-field]").count() == 6
    assert page.locator("[data-ticket-takeover-toggle]").count() == 0
    assert page.locator('details[data-field="kickoff"] [data-scope-ceiling]').input_value() == (
        "needs_success"
    )
    assert (
        page.locator('details[data-field="kickoff"] [data-scope-atcap] select').input_value()
        == "propose"
    )

    title_editor = page.locator(".ticket-title [role=textbox]").first
    title_editor.focus()
    title_editor.evaluate(SELECT_NODE_CONTENTS)
    with page.expect_response(
        lambda response: (
            response.request.method == "PATCH" and response.url.endswith(f"/api/tickets/{tid}")
        )
    ):
        page.keyboard.type("Approved title")
        title_editor.blur()
    note_editor = page.locator('details[data-field="kickoff"] [data-edit]').first
    note_editor.focus()
    note_editor.evaluate(SELECT_NODE_CONTENTS)
    page.keyboard.type("Approved premise")
    note_editor.blur()
    page.select_option('details[data-field="kickoff"] [data-scope-ceiling]', "needs_approach")
    page.select_option('details[data-field="kickoff"] [data-scope-atcap] select', "stop")
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(f"/api/tickets/{tid}/accept/kickoff")
        )
    ):
        page.click('details[data-field="kickoff"] [data-accept]')
    page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{tid}"][data-stage="needs_success"]',
        timeout=WAIT_MS,
    )
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["title"] == "Approved title"
    assert detail["ceiling"] == "needs_approach"
    assert detail["at_cap"] == "stop"
    assert detail["fields"]["kickoff"]["value"] == "Approved premise"
    assert detail["fields"]["kickoff"]["proposal"] is None
    assert len(detail["fields"]) == 6


def test_kickoff_ceiling_suggestion_prefills_ticket_and_review_but_owner_choice_wins(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    api.direct_put(
        server,
        "/api/workers/coding/suggested-next-ceiling",
        {"suggested_next_ceiling": "needs_plan"},
    )
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Ticket suggestion",
        "--kickoff-note",
        "Confirm Ticket scope.",
    )["id"]
    initial = api.get(server, f"/api/tickets/{ticket_id}")
    assert initial["stage"] == "needs_kickoff"
    assert initial["ceiling"] == "needs_kickoff"
    assert initial["suggested_next_ceiling"] == "needs_plan"

    ticket_scope = 'details[data-field="kickoff"] [data-scope-ceiling]'
    ticket_page = open_page(
        context_factory(),
        server,
        f"#/workspace/{ticket_id}",
        ticket_scope,
    )
    assert ticket_page.locator(ticket_scope).input_value() == "needs_plan"
    ticket_page.select_option(ticket_scope, "needs_approach")
    ticket_page.select_option(
        'details[data-field="kickoff"] [data-scope-atcap] select', "stop"
    )
    with ticket_page.expect_response(
        lambda response: response.request.method == "GET"
        and response.url.endswith(f"/api/tickets/{ticket_id}")
    ):
        api.direct_patch(
            server,
            f"/api/tickets/{ticket_id}",
            {"priority": "P2"},
        )
    assert ticket_page.locator(ticket_scope).input_value() == "needs_approach"
    assert ticket_page.locator(
        'details[data-field="kickoff"] [data-scope-atcap] select'
    ).input_value() == "stop"
    ticket_page.click('details[data-field="kickoff"] [data-accept]')
    ticket_page.wait_for_selector(
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]'
        '[data-stage="needs_success"]',
        timeout=WAIT_MS,
    )
    accepted_ticket = api.get(server, f"/api/tickets/{ticket_id}")
    assert accepted_ticket["ceiling"] == "needs_approach"
    assert accepted_ticket["at_cap"] == "stop"

    review_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Review suggestion",
        "--kickoff-note",
        "Confirm Review scope.",
    )["id"]
    _add_to_today(api, server, review_id)
    review_card = f'[data-review-card][data-ticket-id="{review_id}"]'
    review_page = open_page(context_factory(), server, "#/review", review_card)
    review_scope = f"{review_card} [data-scope-ceiling]"
    assert review_page.locator(review_scope).input_value() == "needs_plan"
    review_page.select_option(review_scope, "needs_closeout")
    review_page.click(f"{review_card} [data-accept]")
    review_page.wait_for_selector(review_card, state="detached", timeout=WAIT_MS)
    accepted_review = api.get(server, f"/api/tickets/{review_id}")
    assert accepted_review["ceiling"] == "needs_closeout"
    assert accepted_review["at_cap"] == "propose"


def test_review_pending_kickoff_corrects_own_ticket_priority(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Priority correction",
        "--priority",
        "P3",
        "--kickoff-note",
        "Confirm this kickoff.",
    )["id"]
    _add_to_today(api, server, tid)

    card = f'[data-review-card][data-ticket-id="{tid}"]'
    priority = f"{card} [data-review-priority-control]"
    page = open_page(context_factory(), server, "#/review", card)
    assert page.get_attribute(card, "data-field") == "kickoff"
    assert page.locator(f"{priority} select").input_value() == "P3"
    assert page.locator(f'{priority} [data-priority-tile="P3"]').count() == 1

    page.evaluate(
        """ticketId => {
          const originalFetch = window.fetch.bind(window);
          let releasePatch;
          const heldPatch = new Promise(resolve => { releasePatch = resolve; });
          let heldOnce = false;
          window.__releasePriorityPatch = releasePatch;
          window.fetch = async (input, init = {}) => {
            const url = typeof input === "string" ? input : input.url;
            if (
              !heldOnce &&
              init.method === "PATCH" &&
              url.endsWith(`/api/tickets/${ticketId}`) &&
              JSON.parse(init.body).priority === "P1"
            ) {
              heldOnce = true;
              await heldPatch;
              return new Response(
                JSON.stringify({
                  error: {
                    code: "priority_save_failed",
                    message: "priority save failed",
                  },
                }),
                {
                  status: 500,
                  headers: { "Content-Type": "application/json" },
                },
              );
            }
            return originalFetch(input, init);
          };
        }""",
        tid,
    )
    page.locator(f"{priority} select").select_option("P1")
    page.wait_for_function(
        "selector => document.querySelector(selector)?.disabled === true",
        arg=f"{card} [data-accept]",
        timeout=WAIT_MS,
    )
    assert page.locator(f"{card} [data-accept]").is_disabled()
    assert page.locator(f"{card} [data-review-priority-error]").count() == 0
    page.evaluate("window.__releasePriorityPatch()")
    page.locator(f"{card} [data-review-priority-error]", has_text="priority save failed").wait_for(
        state="visible", timeout=WAIT_MS
    )
    _wait_enabled(page, f"{card} [data-accept]")
    assert page.locator(f"{priority} select").input_value() == "P3"
    assert api.get(server, f"/api/tickets/{tid}")["priority"] == "P3"

    with page.expect_response(
        lambda response: (
            response.request.method == "PATCH"
            and response.url.endswith(f"/api/tickets/{tid}")
            and response.ok
        )
    ):
        page.locator(f"{priority} select").select_option("P0")
    assert page.locator(f"{priority} select").input_value() == "P0"
    assert page.locator(f"{card} [data-review-priority-error]").count() == 0
    assert api.get(server, f"/api/tickets/{tid}")["priority"] == "P0"
    page.locator(f'{priority} [data-priority-tile="P0"]').wait_for(
        state="visible", timeout=WAIT_MS
    )

    page.reload()
    page.wait_for_selector(card, timeout=WAIT_MS)
    assert page.locator(f"{priority} select").input_value() == "P0"
    assert page.locator(f'{priority} [data-priority-tile="P0"]').count() == 1

    _wait_enabled(page, f"{card} [data-accept]")
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and request.url.endswith(f"/api/tickets/{tid}/accept/kickoff")
        )
    ) as approve_request:
        page.click(f"{card} [data-accept]")
    assert approve_request.value.post_data_json == {
        "next_ceiling": "needs_success",
        "at_cap": "propose",
    }
    page.wait_for_selector(card, state="detached", timeout=WAIT_MS)
    approved = api.get(server, f"/api/tickets/{tid}")
    assert approved["priority"] == "P0"
    assert approved["fields"]["kickoff"]["proposal"] is None

    cli(
        server,
        "worker",
        "propose",
        "--recap",
        "Success is ready.",
        ticket_id=tid,
        stdin="Later-stage proposal.",
    )
    page.reload()
    page.wait_for_selector(card, timeout=WAIT_MS)
    assert page.get_attribute(card, "data-field") == "success"
    assert page.locator(f"{card} [data-review-priority-control]").count() == 0
