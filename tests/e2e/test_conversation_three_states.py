"""The conversation's three states, on the ticket page, in a browser.

Three states and four ways between them, and the only thing that decides which one you are
in is the person. Rest is the composer with one line above it; peeked is a card over the
page with the transcript in it and the bar gone, because the turn head inside the
transcript already says what the bar said; opened is the same conversation at full height.

The two claims this file exists for are the last two. One conversation at three heights
means a transition is a change of size and nothing else, so the reader must come out of it
on the line they went into it on, and the draft must come out of it exactly as it went in.
Both are asserted against a real transcript in a real browser: the line is found by its own
words and measured from the thread's own top, the way the pane's other scroll tests measure
it, and the draft is asserted as its text, its caret, the box being the same box, and where
the keyboard went.

Rows are written straight into the record with the store the server itself uses and read
back over HTTP the way the browser reads any other row, so a turn's work and a permission
ask are real rows here without an agent being involved. One thing stands in for an agent,
because a real one is not a repeatable gate: whether a turn is running is the live system's
answer rather than the record's, so where a running turn is what is being shown, that one
answer is changed on the way past and nothing else is.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.conftest import WAIT_MS
from tests.e2e.harness import JsonObject, ServerHandle
from tests.e2e.test_dev_conversation_pane import (
    _a_conversation_worth_scrolling,
    _append_rows,
)

from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventPayload,
)
from planner.conversation.message_content import text_message_content

PANE = "[data-conversation-pane]"
INPUT = "[data-conversation-input]"
EXPAND = "[data-conversation-expand]"
COLLAPSE = "[data-conversation-collapse]"
REST_LINE = "[data-conversation-rest-line]"
REST_ASIDE = "[data-conversation-rest-aside]"
REST_WAITING = "[data-conversation-rest-waiting]"
THREAD = "[data-conversation-thread]"
# A piece of the ticket itself: on the page, above where the layer sits, and nothing
# happens when it is clicked except what the layer does about it.
THE_TICKET_BEHIND = '[data-screen="ticket"] [data-ticket-status]'
THE_TICKETS_OWN_SCROLLER = '[data-screen="ticket"] .ticket-doc'

# How many rows the seeded conversation puts on the page before anything else is added.
ROWS_IN_THE_SEED = 16

# Where a line is allowed to have moved to and still be the line the reader is on. The same
# tolerance the pane's other position tests hold themselves to.
STILL_THE_SAME_LINE_PIXELS = 2

THE_LAST_THING = (
    "The one line the bar shows\n\nand the second paragraph nobody reads at rest."
)
THE_ASK_TITLE = "Delete the whole of the build directory"

# What each state is, on screen. The state attribute is the pane's own word for where it
# is; everything else here is what a person would see.
WHERE_THE_LAYER_IS = """
() => {
  const onScreen = (element) => {
    if (element === null) return false;
    if (typeof element.checkVisibility === 'function'
        && !element.checkVisibility({ visibilityProperty: true })) return false;
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const pane = document.querySelector('[data-conversation-pane]');
  const host = document.querySelector('[data-conversation-layer-host]');
  const head = document.querySelector('[data-conversation-pane] .chat-head');
  const thread = document.querySelector('[data-conversation-thread]');
  const bar = document.querySelector('[data-conversation-rest-bar]');
  const composer = document.querySelector('[data-conversation-composer]');
  const screen = document.querySelector('[data-screen="ticket"]');
  const rows = Array.from(document.querySelectorAll('[data-conversation-row]'));
  return {
    state: pane === null ? null : (pane.dataset.conversationState ?? null),
    // The surface the pane is painted in, resolved to a colour. Rest and peeked wear the
    // ink blue, and opening is a change of size, not colour, so opened wears it too.
    paneBackground: pane === null ? null : getComputedStyle(pane).backgroundColor,
    paneInsideTheHost: host !== null && pane !== null && host.contains(pane),
    paneHeight: pane === null ? 0 : Math.round(pane.getBoundingClientRect().height),
    ticketHeight: screen === null ? 0 : Math.round(screen.getBoundingClientRect().height),
    // The head itself, rather than what happens to be in it. A control that is not
    // rendered at rest says nothing about whether the head is hidden.
    headMounted: head !== null,
    headOnScreen: onScreen(head),
    // Hidden and gone are different things, and the difference is the whole design: one
    // conversation at three heights keeps the transcript mounted through all of them.
    transcriptMounted: thread !== null,
    transcriptOnScreen: onScreen(thread),
    rowsMounted: rows.length,
    // What the transcript actually draws, whatever way it is being hidden. At rest this
    // is the whole claim: every row is still there and not one of them is on the screen.
    rowsOnScreen: rows.filter(onScreen).length,
    restBarMounted: bar !== null,
    restBarOnScreen: onScreen(bar),
    restBarAboveTheComposer: bar === null || composer === null
      ? null
      : Math.round(bar.getBoundingClientRect().bottom)
        <= Math.round(composer.getBoundingClientRect().top),
    expand: onScreen(document.querySelector('[data-conversation-expand]')),
    collapse: onScreen(document.querySelector('[data-conversation-collapse]')),
    ticketOnScreen: onScreen(document.querySelector('[data-screen="ticket"] .ticket-title'))
  };
}
"""

# The line the reader is on, and where it sits in the thread they are reading it in.
#
# Asked with nothing it answers with the topmost line still in view — the one a person
# reading is looking at. Asked with that line's own words it finds that same line again,
# whatever has happened to the thread around it. Positions are measured from the thread's
# own top, because that is the frame the reader's view is in and it is the frame the pane's
# other position tests use.
THE_LINE_THE_READER_IS_ON = """
(wanted) => {
  const thread = document.querySelector('[data-conversation-thread]');
  const threadTop = thread.getBoundingClientRect().top;
  const lines = Array.from(thread.querySelectorAll('p'))
    .filter((line) => line.getClientRects().length > 0);
  const words = (line) => line.textContent.trim();
  const chosen = wanted === null
    ? lines.find((line) => line.getBoundingClientRect().bottom > threadTop) ?? null
    : lines.find((line) => words(line) === wanted) ?? null;
  return {
    scrollTop: Math.round(thread.scrollTop),
    clientHeight: Math.round(thread.clientHeight),
    linesOnScreen: lines.length,
    line: chosen === null ? null : words(chosen),
    lineTop: chosen === null
      ? null
      : Math.round(chosen.getBoundingClientRect().top - threadTop)
  };
}
"""

# The draft: the words, where the cursor is in them, whether the box is the same box, and
# where the keyboard is.
#
# Where the keyboard is has two answers worth telling apart. In the box is where a person
# typing expects it, and it is what has to be true after anything they did from inside the
# box. Pressing a control is different: focus resting on the thing you just pressed is what
# a browser does, so the claim there is the weaker one — the keyboard is still on something
# inside the conversation, not dropped on the page.
THE_DRAFT_IN_THE_BOX = """
() => {
  const box = document.querySelector('[data-conversation-input]');
  const pane = document.querySelector('[data-conversation-pane]');
  const holding = document.activeElement;
  return {
    text: box.value,
    caret: [box.selectionStart, box.selectionEnd],
    stillAttached: box.isConnected,
    focusedInTheBox: holding === box,
    focusedInTheConversation: holding !== null && pane !== null && pane.contains(holding),
    focusedOn: holding === null ? null : holding.tagName.toLowerCase()
  };
}
"""

# The one thing rows cannot say, stood in for.
#
# Whether a turn is running is the live system's answer and not the record's, and the
# browser believes the system over the rows on exactly that question. So a conversation
# whose rows were written beside the server is never running however the rows read, and a
# turn cannot be started here without a real agent, which is not a repeatable gate.
#
# Only that one answer is changed, on the way past. The rows are real, the record is real,
# the reads are the browser's own, and every other field of the answer is the server's.
THE_SYSTEM_SAYS_A_TURN_IS_RUNNING = """
const realFetch = window.fetch.bind(window);
const theViewOfAConversation = /\\/conversations\\/[^/?]+$/;
window.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : input.url;
  const answered = await realFetch(input, init);
  if (typeof url !== 'string' || !theViewOfAConversation.test(url) || !answered.ok) {
    return answered;
  }
  const view = await answered.json();
  return new Response(JSON.stringify({ ...view, is_running: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  });
};
"""

WHERE_THE_TICKET_IS_SCROLLED_TO = """
() => {
  const doc = document.querySelector('[data-screen="ticket"] .ticket-doc');
  if (doc === null) return null;
  return {
    scrollTop: Math.round(doc.scrollTop),
    scrollHeight: Math.round(doc.scrollHeight),
    clientHeight: Math.round(doc.clientHeight)
  };
}
"""


def _a_ticket_with_a_conversation(
    server: ServerHandle, cli: Callable[..., JsonObject], title: str
) -> tuple[str, str]:
    """A Ticket with a conversation of its own, so the layer has something in it."""
    ticket_id = cli(server, "ticket", "create", "--worker-type", "coding", "--title", title)["id"]
    started = httpx.post(
        f"{server.base}/api/tickets/{ticket_id}/conversation/send",
        json={
            "conversation_id": None,
            "content": [{"piece": "text", "text": "hello"}],
            "sender_label": "owner",
        },
        timeout=10.0,
    )
    assert started.status_code == 200, started.text
    conversation_id = started.json()["conversation_id"]
    assert conversation_id is not None
    return ticket_id, conversation_id


def _a_settled_conversation_worth_reading(
    last_thing: str,
) -> tuple[ConversationEventPayload, ...]:
    """Eight turns, long enough to scroll, ending in something with a first line."""
    return (
        *_a_conversation_worth_scrolling(),
        AgentMessageEventPayload(content=text_message_content(last_thing)),
    )


def _the_ticket_page(
    server: ServerHandle,
    context: BrowserContext,
    open_page: Callable[..., Page],
    ticket_id: str,
    rows: int,
) -> Page:
    """The ticket page, with its conversation loaded and at the state the page opens in."""
    page = open_page(
        context,
        server,
        f"#/ticket/{ticket_id}",
        f'section[data-screen="ticket"][data-ticket-id="{ticket_id}"]',
    )
    page.wait_for_selector("[data-conversation-layer-host]", timeout=WAIT_MS)
    # The ticket page opens its conversation at rest: the composer, and one line above it.
    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)
    page.wait_for_selector(f"{INPUT}:not([disabled])", timeout=WAIT_MS)
    page.wait_for_function(
        "(expected) => document.querySelectorAll('[data-conversation-row]').length >= expected",
        arg=rows,
        timeout=WAIT_MS,
    )
    return page


def _click_the_composers_input(page: Page) -> None:
    """Clicking the box you speak into opens the conversation a little."""
    page.click(INPUT, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)


def _take_it_full(page: Page) -> None:
    page.click(EXPAND, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)


def _bring_it_back(page: Page) -> None:
    page.click(COLLAPSE, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)


def _click_the_ticket_behind(page: Page, lands_on: str) -> None:
    """A click on the ticket itself, which drops the conversation one state back."""
    page.click(THE_TICKET_BEHIND, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="{lands_on}"]', timeout=WAIT_MS)


def _the_draft_is_still_there(
    page: Page, where: str, text: str, caret: int
) -> dict[str, Any]:
    """What a change of height must not touch: the words, the cursor, and the box itself."""
    draft: dict[str, Any] = page.evaluate(THE_DRAFT_IN_THE_BOX)
    assert draft["stillAttached"] is True, (where, "the box was re-made", draft)
    assert draft["text"] == text, (where, draft)
    assert draft["caret"] == [caret, caret], (where, draft)
    return draft


def test_the_three_states_are_what_the_ticket_page_shows(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Each state, and the person's own way into it.

    Rest is one line and no transcript. Clicking the box opens it a little: the transcript
    arrives and the bar goes, because the turn head inside the transcript is about to say
    the same thing six pixels away. The control takes it full and the other one brings it
    back, and the three heights are three heights.
    """
    ticket_id, conversation_id = _a_ticket_with_a_conversation(server, cli, "The three states")
    _append_rows(server, conversation_id, *_a_settled_conversation_worth_reading(THE_LAST_THING))
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id, ROWS_IN_THE_SEED)

    # --- rest: the composer, and one line above it -----------------------------------------
    at_rest = page.evaluate(WHERE_THE_LAYER_IS)
    assert at_rest["state"] == "rest"
    assert at_rest["paneInsideTheHost"] is True, "the layer sits in the ticket page's host"
    assert at_rest["transcriptMounted"] is True, "one conversation at three heights"
    assert at_rest["transcriptOnScreen"] is False, "there is no transcript at rest"
    # Every row still in the page, and not one of them drawn. Mounted and hidden is the
    # whole of what rest is, and it is what the reader's place and the draft ride on.
    assert at_rest["rowsMounted"] >= ROWS_IN_THE_SEED
    assert at_rest["rowsOnScreen"] == 0, at_rest
    assert at_rest["restBarOnScreen"] is True
    assert at_rest["restBarAboveTheComposer"] is True
    # The head itself, not the controls in it: a control that is only rendered at peeked
    # would answer False here whether the head were hidden or not.
    assert at_rest["headMounted"] is True
    assert at_rest["headOnScreen"] is False, "the pane head is hidden at rest"
    assert at_rest["expand"] is False
    assert at_rest["collapse"] is False
    assert at_rest["ticketOnScreen"] is True

    # The one line is the first line of whatever happened last, and only the first line.
    said = page.inner_text(REST_LINE)
    assert "The one line the bar shows" in said
    assert "the second paragraph nobody reads at rest" not in said

    # --- peeked: a card over the page, and the bar gone --------------------------------------
    _click_the_composers_input(page)
    peeked = page.evaluate(WHERE_THE_LAYER_IS)
    assert peeked["state"] == "peeked"
    assert peeked["transcriptOnScreen"] is True
    assert peeked["rowsOnScreen"] > 0, peeked
    assert peeked["restBarMounted"] is False, "the turn head says it; the bar would say it twice"
    assert peeked["headOnScreen"] is True
    assert peeked["expand"] is True
    assert peeked["collapse"] is False
    assert peeked["paneHeight"] > at_rest["paneHeight"], (at_rest, peeked)
    assert peeked["paneHeight"] < peeked["ticketHeight"], "peeked is a card, not the page"
    assert peeked["ticketOnScreen"] is True, "the ticket is behind it, not gone"

    # --- opened: the same conversation at full height ---------------------------------------
    _take_it_full(page)
    opened = page.evaluate(WHERE_THE_LAYER_IS)
    assert opened["state"] == "opened"
    assert opened["transcriptOnScreen"] is True
    assert opened["restBarMounted"] is False
    assert opened["headOnScreen"] is True
    assert opened["collapse"] is True, "a control back"
    assert opened["expand"] is False
    assert opened["paneHeight"] > peeked["paneHeight"], (peeked, opened)
    # Opening is a change of size, not colour: the full-height surface is the same ink blue
    # the card wore at rest and peeked, not the lighter base surface it used to switch to.
    assert at_rest["paneBackground"] == peeked["paneBackground"], (at_rest, peeked)
    assert opened["paneBackground"] == at_rest["paneBackground"], (at_rest, opened)
    # Loose on purpose, and it must stay loose. Whether opened covers the nav or only the
    # page beneath it is one of the three questions the kickoff left open until it can be
    # seen; a tolerance tight enough to tell those two apart would answer it here instead.
    assert opened["paneHeight"] >= opened["ticketHeight"] * 0.8, (opened, "full height")

    # --- and back, by the control that says so ----------------------------------------------
    _bring_it_back(page)
    back = page.evaluate(WHERE_THE_LAYER_IS)
    assert back["state"] == "peeked"
    assert back["expand"] is True
    assert back["restBarMounted"] is False
    assert back["paneHeight"] == peeked["paneHeight"], (peeked, back)


def test_the_reader_stays_on_the_line_they_were_reading_through_every_transition(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """The failure this design is most likely to produce, and the one you would notice.

    A reader goes up into the middle of a long conversation and then moves the layer
    through every state it has. Each transition changes the height of the thread they are
    reading in, and after each one they must be on the same line, in the same place in
    their view. Rest is in the walk on purpose: the transcript is hidden there rather than
    unmounted, and hiding a scrolling box is exactly what loses a reader's place.
    """
    ticket_id, conversation_id = _a_ticket_with_a_conversation(server, cli, "The reader's place")
    _append_rows(server, conversation_id, *_a_settled_conversation_worth_reading(THE_LAST_THING))
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id, ROWS_IN_THE_SEED)

    _click_the_composers_input(page)
    at_the_end = page.evaluate(THE_LINE_THE_READER_IS_ON, None)
    assert at_the_end["linesOnScreen"] > 0, at_the_end
    assert at_the_end["scrollTop"] > 0, "a conversation this long has somewhere to scroll"

    # Off to read something in the middle. From here nothing may move them that they did
    # not do themselves.
    page.hover(THREAD, timeout=WAIT_MS)
    page.mouse.wheel(0, -400)
    page.wait_for_function(
        "(was) => document.querySelector('[data-conversation-thread]').scrollTop < was",
        arg=at_the_end["scrollTop"],
        timeout=WAIT_MS,
    )
    reading = page.evaluate(THE_LINE_THE_READER_IS_ON, None)
    assert reading["line"] is not None, reading
    theirs = reading["line"]

    def still_on_their_line(where: str) -> None:
        now = page.evaluate(THE_LINE_THE_READER_IS_ON, theirs)
        assert now["line"] == theirs, (where, theirs, now)
        assert abs(now["lineTop"] - reading["lineTop"]) <= STILL_THE_SAME_LINE_PIXELS, (
            where,
            reading,
            now,
        )

    _take_it_full(page)
    still_on_their_line("opened")

    _bring_it_back(page)
    still_on_their_line("back at peeked")

    _click_the_ticket_behind(page, lands_on="rest")
    _click_the_composers_input(page)
    still_on_their_line("peeked again, after rest")


def test_the_draft_survives_every_transition(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    cli: Callable[..., JsonObject],
) -> None:
    """Half a sentence, and the cursor in the middle of it.

    A transition changes the height of one mounted conversation, so the box is the same box
    throughout every one of them: the words stay and the cursor stays where it was put.

    Where the keyboard ends up is asked for differently depending on what the person did.
    Moving the conversation from inside the box — clicking into it, pressing Escape while
    typing — takes nothing off them, so the keyboard is still in the box. Pressing a control
    is pressing something, and a browser puts the keyboard on what was pressed; the claim
    there is the weaker one, that it is still on something inside the conversation rather
    than dropped on the page, which is what a control that removed itself would do.
    """
    ticket_id, conversation_id = _a_ticket_with_a_conversation(server, cli, "The draft")
    _append_rows(server, conversation_id, *_a_settled_conversation_worth_reading(THE_LAST_THING))
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id, ROWS_IN_THE_SEED)

    _click_the_composers_input(page)
    draft = "the thought I was in the middle of"
    page.keyboard.type(draft)
    # The cursor put somewhere that is not the end, because the end is where anything that
    # re-made the box, or quietly focused it again, would leave it.
    for _ in range(9):
        page.keyboard.press("ArrowLeft")
    mid_sentence = len(draft) - 9
    # Typing carries on from where the cursor was, which is what says it is really there.
    page.keyboard.type("was ")
    carried_on = f"{draft[:mid_sentence]}was {draft[mid_sentence:]}"
    cursor = mid_sentence + 4
    typing = _the_draft_is_still_there(page, "typed at peeked", carried_on, cursor)
    assert typing["focusedInTheBox"] is True, typing

    # Escape, pressed by somebody typing. Nothing about that takes the keyboard off them.
    page.keyboard.press("Escape")
    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)
    at_rest = _the_draft_is_still_there(page, "rest, by Escape", carried_on, cursor)
    assert at_rest["focusedInTheBox"] is True, ("Escape took the keyboard out of the box", at_rest)

    # Back in by clicking the box, which is where a person's own click puts the cursor.
    _click_the_composers_input(page)
    back_in = page.evaluate(THE_DRAFT_IN_THE_BOX)
    assert back_in["text"] == carried_on, back_in
    assert back_in["focusedInTheBox"] is True, back_in
    clicked_to = back_in["caret"][0]

    # The controls. They keep the focus, because a browser puts it on what was pressed —
    # but a control that vanished under the press would drop it on the page instead.
    _take_it_full(page)
    opened = _the_draft_is_still_there(page, "opened", carried_on, clicked_to)
    assert opened["focusedInTheConversation"] is True, (
        "the keyboard was dropped on the page rather than left in the conversation",
        opened,
    )

    _bring_it_back(page)
    peeked = _the_draft_is_still_there(page, "back at peeked", carried_on, clicked_to)
    assert peeked["focusedInTheConversation"] is True, peeked

    # And out again by the ticket behind, which changes what is on screen under the draft
    # and must not change the draft.
    _click_the_ticket_behind(page, lands_on="rest")
    # Nothing is asked here about where the keyboard went. The click landed on the ticket,
    # which is not something that can be focused, so the browser takes the keyboard off the
    # box — the same rule that leaves it on a control that was pressed. The person pressed
    # somewhere else, and this is what pressing somewhere else does.
    _the_draft_is_still_there(page, "rest, by the ticket behind", carried_on, clicked_to)
