"""Flag-ON ticket neutral pane e2e (S3 plan §9.5, contract Acceptance §2 + §4). Drives the REAL
ticket chat pane over /api/relay/neutral AND a REAL automatic Employee step through the composed
EmployeeStepRunner -> PoolStepGateway -> the STATEFUL scripted child, dispatched via the
test-mode-gated `POST /api/test/run-step/{ticket_id}` ingress. No real Hermes.

Every scenario opens against `relay_tickets_server` (PLAN_RELAY_BACKEND_ENABLED=1 in test mode,
seeded with eligible tickets) and waits on the pane's `data-neutral-ready` marker before
asserting, so it never races the pre-history mount. Intentionally unanchored (they do not affect
the verify scorer), mirroring test_chief_neutral_pane.py.

The pane renders from the Hermes relay frames the ticket employee's child emits — NOT Panels
chat rows. A worker step submitted through the pool child fans its native turn frames out to
every subscriber of that ticket employee, so a step's turn streams live into the subscribed
ticket pane exactly like a human turn ("chat IS the worker").
"""

from __future__ import annotations

import httpx
from conftest import RELAY_TICKET_HOLD_TITLE, RELAY_TICKET_TITLE, ServerHandle
from playwright.sync_api import Page

WAIT_MS = 10_000
NEUTRAL_READY = (
    'section[data-screen="ticket"] [data-chief-neutral-pane][data-neutral-ready="true"]'
)


def _ticket_id(server: ServerHandle, title: str) -> str:
    """Discover the seeded ticket's id by its title through /api/tickets (the seed mints a
    non-deterministic id; the title is the stable handle)."""
    resp = httpx.get(server.base + "/api/tickets", timeout=10.0)
    resp.raise_for_status()
    for ticket in resp.json()["tickets"]:
        if ticket["title"] == title:
            return ticket["id"]
    raise AssertionError(f"no seeded ticket titled {title!r}; got {resp.json()}")


def _open_ticket_pane(open_page, context_factory, server, ticket_id: str) -> Page:
    return open_page(
        context_factory(),
        server,
        f"#/ticket/{ticket_id}",
        NEUTRAL_READY,
        settled=False,
    )


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


def _run_step(server: ServerHandle, ticket_id: str) -> None:
    resp = httpx.post(server.base + f"/api/test/run-step/{ticket_id}", timeout=10.0)
    resp.raise_for_status()
    assert resp.json()["dispatched"] is True


# --- step streaming + settlement -------------------------------------------------------------


def test_ticket_step_streams_live_into_subscribed_pane(
    open_page, context_factory, relay_tickets_server
) -> None:
    ticket_id = _ticket_id(relay_tickets_server, RELAY_TICKET_TITLE)
    page = _open_ticket_pane(open_page, context_factory, relay_tickets_server, ticket_id)
    # Pre-install a MutationObserver that records every streaming-text value so a partial prefix
    # is proven to have existed even though the full string later lands (S2b F13 discipline).
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
    # Dispatch a REAL automatic step; its turn streams token-by-token into the subscribed pane.
    _run_step(relay_tickets_server, ticket_id)
    # The step prompt embeds the ticket title -> the scripted child echoes it; wait for that echo
    # to land in the pane as the settled assistant turn.
    _wait_chat_text(page, "planner", f"echo: Work ticket {ticket_id}")
    samples = page.evaluate("() => window.__streamSamples || []")
    # At least one streaming sample must be a strict, non-empty PREFIX of a later, longer sample:
    # proof the assistant text grew token-by-token (streamed live, not delivered whole).
    grew = any(
        a and b and b.startswith(a) and len(a) < len(b)
        for i, a in enumerate(samples)
        for b in samples[i + 1 :]
    )
    assert grew, f"no token-by-token growth observed; samples={samples}"


def test_ticket_step_settles_on_completed(
    open_page, context_factory, relay_tickets_server
) -> None:
    ticket_id = _ticket_id(relay_tickets_server, RELAY_TICKET_TITLE)
    page = _open_ticket_pane(open_page, context_factory, relay_tickets_server, ticket_id)
    _run_step(relay_tickets_server, ticket_id)
    # A normal step completes: the settled assistant turn (the echo) shows in the pane, and the
    # ticket returns to a non-running status (the runner settled Panels state).
    _wait_chat_text(page, "planner", f"echo: Work ticket {ticket_id}")
    page.wait_for_function(
        """
        (base) => fetch(base + '/api/tickets')
          .then(r => r.json())
          .then(d => (d.tickets.find(t => t.title.startsWith('Relay ticket employee'))
                      || {}).ticket_status !== 'agent_running_step')
        """,
        arg=relay_tickets_server.base,
        timeout=WAIT_MS,
    )


def test_ticket_step_settles_on_failed(
    open_page, context_factory, relay_tickets_server
) -> None:
    # The HOLD-titled ticket's step prompt carries the scripted child's "hold open" cue, so the
    # step's turn STAYS running (streaming) until interrupted. The pane interrupts it, and the
    # RUNNING turn closes as a failure terminal (interrupted) rendered in the transcript — a
    # step turn settling on failure, visible in the pane.
    ticket_id = _ticket_id(relay_tickets_server, RELAY_TICKET_HOLD_TITLE)
    page = _open_ticket_pane(open_page, context_factory, relay_tickets_server, ticket_id)
    _run_step(relay_tickets_server, ticket_id)
    # Wait until the step turn is genuinely streaming (a delta rendered) before interrupting.
    page.wait_for_selector("[data-neutral-streaming]", timeout=WAIT_MS)
    _send(page, "/interrupt")  # interrupt is a slash action in the "/" menu
    # The running step turn closes as interrupted (its failure line renders; live streaming gone).
    _wait_chat_text(page, "planner", "(interrupted)")
    page.wait_for_selector("[data-neutral-streaming]", state="detached", timeout=WAIT_MS)
    # The relay-rendered interruption is not enough: assert the RUNNER actually settled the ticket.
    # A failed (interrupted) step leaves agent_running_step and marks the ticket errored
    # (employee_step_runner: interrupted result -> mark_run_errored_if_still_running_step ->
    # TicketStatus.errored). Without this the test would pass even if the runner hung on the
    # never-completing held turn and never called mark_run_errored_if_still_running_step. Mirrors
    # test_ticket_step_settles_on_completed's status check, but asserts the errored terminal state.
    page.wait_for_function(
        """
        ({ base, title }) => fetch(base + '/api/tickets')
          .then(r => r.json())
          .then(d => {
            const t = d.tickets.find(x => x.title === title) || {};
            return t.ticket_status !== 'agent_running_step' && t.ticket_status === 'errored';
          })
        """,
        arg={"base": relay_tickets_server.base, "title": RELAY_TICKET_HOLD_TITLE},
        timeout=WAIT_MS,
    )


# --- human chat on the ticket neutral pane ---------------------------------------------------


def test_ticket_chat_send_and_render_flag_on(
    open_page, context_factory, relay_tickets_server
) -> None:
    ticket_id = _ticket_id(relay_tickets_server, RELAY_TICKET_TITLE)
    page = _open_ticket_pane(open_page, context_factory, relay_tickets_server, ticket_id)
    _send(page, "guide the ticket employee")
    _wait_chat_text(page, "you", "guide the ticket employee")
    _wait_chat_text(page, "planner", "echo: guide the ticket employee")


def test_ticket_human_send_mid_step_native_queue(
    open_page, context_factory, relay_tickets_server
) -> None:
    # A human send DURING a running step follows stock queue semantics: the composer is NEVER
    # disabled and no synthetic busy is raised (D-native-turn-concurrency). The held step keeps
    # the ticket employee's turn running while the human interjects.
    ticket_id = _ticket_id(relay_tickets_server, RELAY_TICKET_HOLD_TITLE)
    page = _open_ticket_pane(open_page, context_factory, relay_tickets_server, ticket_id)
    _run_step(relay_tickets_server, ticket_id)
    page.wait_for_selector("[data-neutral-streaming]", timeout=WAIT_MS)
    # The composer stays enabled while the step streams; with text present a mid-step send is
    # allowed (no turn-busy disable — the empty-composer disable is unrelated).
    assert page.is_enabled("[data-chat-input]")
    page.fill("[data-chat-input]", "interject while the step runs")
    assert page.is_enabled("[data-chat-send]")
    page.click("[data-chat-send]")
    _wait_chat_text(page, "you", "interject while the step runs")


# --- recovery after child reset --------------------------------------------------------------


def test_ticket_recovery_after_child_reset(
    open_page, context_factory, relay_tickets_server
) -> None:
    ticket_id = _ticket_id(relay_tickets_server, RELAY_TICKET_TITLE)
    page = _open_ticket_pane(open_page, context_factory, relay_tickets_server, ticket_id)
    # A completed turn whose messages persist to the durable (respawn-surviving) session store.
    _send(page, "before reset")
    _wait_chat_text(page, "planner", "echo: before reset")
    # The RESET cue kills the child; the relay synthesizes child_reset. The send first paints an
    # OPTIMISTIC human row; the barrier below proves the reset fired (that optimistic row must
    # DISAPPEAR when the post-reattach history snapshot — which never got the never-completed
    # reset-cue turn — REPLACES the transcript).
    _send(page, "reset-child now")
    # markdown renders __reset_child__ as bold, so the visible text is "reset_child now".
    _wait_chat_text(page, "you", "reset-child")
    page.wait_for_function(
        "() => !Array.from(document.querySelectorAll('[data-chat-msg=\"you\"]'))"
        ".some(el => el.textContent.includes('reset-child'))",
        timeout=WAIT_MS,
    )
    # The durable history is intact after re-attach (the persisted "before reset" turn).
    _wait_chat_text(page, "you", "before reset")
    _wait_chat_text(page, "planner", "echo: before reset")
    # The pane is functional after recovery: a fresh send streams normally.
    _send(page, "after reset")
    _wait_chat_text(page, "planner", "echo: after reset")
