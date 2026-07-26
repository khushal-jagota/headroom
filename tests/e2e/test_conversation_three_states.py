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
it, and the draft is asserted as its text, its caret and its focus together.

Rows are written straight into the record with the store the server itself uses and read
back over HTTP the way the browser reads any other row, so a turn's work and a permission
ask are real rows here without an agent being involved. One thing stands in for an agent,
because a real one is not a repeatable gate: whether a turn is running is the live system's
answer rather than the record's, so where a running turn is what is being shown, that one
answer is changed on the way past and nothing else is.
"""

from __future__ import annotations

import httpx
from tests.e2e.conftest import WAIT_MS
from tests.e2e.test_dev_conversation_pane import (
    _a_conversation_worth_scrolling,
    _append_rows,
    _let_the_browser_catch_up,
)

from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import (
    AgentMessageEventPayload,
    PermissionAskedEventPayload,
    PermissionAskOption,
    PlanEntry,
    PlanEntryStatus,
    PlanUpdatedEventPayload,
    PromptEventPayload,
    ToolCallStartedEventPayload,
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
  const thread = document.querySelector('[data-conversation-thread]');
  const bar = document.querySelector('[data-conversation-rest-bar]');
  const composer = document.querySelector('[data-conversation-composer]');
  const screen = document.querySelector('[data-screen="ticket"]');
  return {
    state: pane === null ? null : (pane.dataset.conversationState ?? null),
    paneInsideTheHost: host !== null && pane !== null && host.contains(pane),
    paneHeight: pane === null ? 0 : Math.round(pane.getBoundingClientRect().height),
    ticketHeight: screen === null ? 0 : Math.round(screen.getBoundingClientRect().height),
    // Hidden and gone are different things, and the difference is the whole design: one
    // conversation at three heights keeps the transcript mounted through all of them.
    transcriptMounted: thread !== null,
    transcriptOnScreen: onScreen(thread),
    rowsMounted: document.querySelectorAll('[data-conversation-row]').length,
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

# The draft, as the three things that together mean it survived: the words, where the
# cursor is in them, and whether the person is still in the box.
THE_DRAFT_IN_THE_BOX = """
() => {
  const box = document.querySelector('[data-conversation-input]');
  return {
    text: box.value,
    caret: [box.selectionStart, box.selectionEnd],
    focused: document.activeElement === box,
    stillAttached: box.isConnected
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


def _a_ticket_with_a_conversation(server, cli, title: str) -> tuple[str, str]:
    """A Ticket with a conversation of its own, so the layer has something in it."""
    ticket_id = cli(server, "ticket", "create", "--worker-type", "coding", "--title", title)["id"]
    started = httpx.post(f"{server.base}/api/tickets/{ticket_id}/conversation", timeout=10.0)
    assert started.status_code == 200, started.text
    conversation_id = started.json()["conversation_id"]
    assert conversation_id is not None
    return ticket_id, conversation_id


def _a_settled_conversation_worth_reading(last_thing: str) -> tuple:
    """Eight turns, long enough to scroll, ending in something with a first line."""
    return (
        *_a_conversation_worth_scrolling(),
        AgentMessageEventPayload(content=text_message_content(last_thing)),
    )


def _the_ticket_page(server, context, open_page, ticket_id: str, rows: int):
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


def _click_the_composers_input(page) -> None:
    """Clicking the box you speak into opens the conversation a little."""
    page.click(INPUT, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)


def _take_it_full(page) -> None:
    page.click(EXPAND, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="opened"]', timeout=WAIT_MS)


def _bring_it_back(page) -> None:
    page.click(COLLAPSE, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)


def _click_the_ticket_behind(page, lands_on: str) -> None:
    """A click on the ticket itself, which drops the conversation one state back."""
    page.click(THE_TICKET_BEHIND, timeout=WAIT_MS)
    page.wait_for_selector(f'{PANE}[data-conversation-state="{lands_on}"]', timeout=WAIT_MS)


def _the_draft_is_still_there(page, where: str, text: str, caret: int) -> None:
    """The three things that together mean the person can carry on where they were."""
    draft = page.evaluate(THE_DRAFT_IN_THE_BOX)
    assert draft["stillAttached"] is True, (where, "the box was re-made", draft)
    assert draft["text"] == text, (where, draft)
    assert draft["caret"] == [caret, caret], (where, draft)
    assert draft["focused"] is True, (
        where,
        "the keyboard left the box the person was typing in",
        draft,
    )


def _rows_on_the_page(page) -> int:
    return int(page.evaluate("() => document.querySelectorAll('[data-conversation-row]').length"))


def _a_turn_getting_going() -> tuple:
    """A turn starting, with a plan and two tool calls — the newest of them last."""
    return (
        PromptEventPayload(
            content=text_message_content("go and do the thing"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        ),
        PlanUpdatedEventPayload(
            entries=(
                PlanEntry(text="read the plan file", status=PlanEntryStatus.completed),
                PlanEntry(text="check the tree", status=PlanEntryStatus.in_progress),
                PlanEntry(text="write it down", status=PlanEntryStatus.pending),
            )
        ),
        ToolCallStartedEventPayload(
            tool_call_id="three-states-read",
            title="the plan file",
            tool_kind="read",
            detail=None,
        ),
        ToolCallStartedEventPayload(
            tool_call_id="three-states-run",
            title="git status",
            tool_kind="execute",
            detail=None,
        ),
    )


def test_the_three_states_are_what_the_ticket_page_shows(
    server, context_factory, open_page, cli
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
    assert at_rest["rowsMounted"] >= ROWS_IN_THE_SEED
    assert at_rest["restBarOnScreen"] is True
    assert at_rest["restBarAboveTheComposer"] is True
    assert at_rest["expand"] is False, "the pane head is not on screen at rest"
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
    assert peeked["restBarMounted"] is False, "the turn head says it; the bar would say it twice"
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
    assert opened["collapse"] is True, "a control back"
    assert opened["expand"] is False
    assert opened["paneHeight"] > peeked["paneHeight"], (peeked, opened)
    assert opened["paneHeight"] >= opened["ticketHeight"] * 0.8, (opened, "full height")

    # --- and back, by the control that says so ----------------------------------------------
    _bring_it_back(page)
    back = page.evaluate(WHERE_THE_LAYER_IS)
    assert back["state"] == "peeked"
    assert back["expand"] is True
    assert back["restBarMounted"] is False
    assert back["paneHeight"] == peeked["paneHeight"], (peeked, back)


def test_the_ticket_behind_is_still_readable_and_a_click_on_it_drops_a_state(
    server, context_factory, open_page, cli
) -> None:
    """Peeked is a layer, not a mode.

    The ticket underneath is still there to be read and still scrolls, and scrolling it is
    not a click, so it changes nothing about the layer. A click on it does: one state back,
    and no further than rest.
    """
    ticket_id, conversation_id = _a_ticket_with_a_conversation(server, cli, "The ticket behind")
    _append_rows(server, conversation_id, *_a_settled_conversation_worth_reading(THE_LAST_THING))
    page = _the_ticket_page(server, context_factory(), open_page, ticket_id, ROWS_IN_THE_SEED)
    # A short window, so the ticket's own column has somewhere to scroll and "it still
    # scrolls" is a claim with something behind it.
    page.set_viewport_size({"width": 1280, "height": 560})

    _click_the_composers_input(page)
    before = page.evaluate(WHERE_THE_TICKET_IS_SCROLLED_TO)
    assert before is not None, "the ticket page keeps its own scrolling column"
    assert before["scrollHeight"] > before["clientHeight"], before

    # A wheel over the ticket, with the layer over the bottom of it.
    box = page.locator(THE_TICKETS_OWN_SCROLLER).bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 60)
    page.mouse.wheel(0, 300)
    page.wait_for_function(
        "(was) => document.querySelector('[data-screen=\"ticket\"] .ticket-doc').scrollTop > was",
        arg=before["scrollTop"],
        timeout=WAIT_MS,
    )
    still_peeked = page.evaluate(WHERE_THE_LAYER_IS)
    assert still_peeked["state"] == "peeked", "reading the page behind is not dismissing it"
    assert still_peeked["ticketOnScreen"] is True

    # A click on it is a different thing, and it is worth one state.
    _click_the_ticket_behind(page, lands_on="rest")
    at_rest = page.evaluate(WHERE_THE_LAYER_IS)
    assert at_rest["restBarOnScreen"] is True
    assert at_rest["transcriptOnScreen"] is False

    # And there is nowhere further back to go.
    page.click(THE_TICKET_BEHIND, timeout=WAIT_MS)
    assert page.evaluate(WHERE_THE_LAYER_IS)["state"] == "rest"

    # Escape is the same move for somebody whose hands are on the keyboard, and a layer at
    # full height is the one place a click on the ticket behind cannot reach.
    _click_the_composers_input(page)
    _take_it_full(page)
    page.keyboard.press("Escape")
    page.wait_for_selector(f'{PANE}[data-conversation-state="peeked"]', timeout=WAIT_MS)
    page.keyboard.press("Escape")
    page.wait_for_selector(f'{PANE}[data-conversation-state="rest"]', timeout=WAIT_MS)


def test_the_reader_stays_on_the_line_they_were_reading_through_every_transition(
    server, context_factory, open_page, cli
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
    server, context_factory, open_page, cli
) -> None:
    """Half a sentence, and the cursor in the middle of it.

    A transition changes the height of one mounted conversation, so the box is the same
    box throughout: the words stay, the cursor stays where it was put, and the person is
    still in the box. Clicking the ticket behind is the one move that is a person leaving
    the box, so their words and their cursor survive it but their focus does not — and
    clicking back into the box puts the cursor where they clicked, which is what a click
    in a box is for.
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
    _the_draft_is_still_there(page, "typed at peeked", draft, mid_sentence)

    _take_it_full(page)
    _the_draft_is_still_there(page, "opened", draft, mid_sentence)

    _bring_it_back(page)
    _the_draft_is_still_there(page, "back at peeked", draft, mid_sentence)

    # Typing carries on from where the cursor was, which is what says it is really there.
    page.keyboard.type("was ")
    carried_on = f"{draft[:mid_sentence]}was {draft[mid_sentence:]}"
    _the_draft_is_still_there(page, "still typing", carried_on, mid_sentence + 4)

    # Clicking the ticket behind is a person leaving the box, so the keyboard goes with
    # them. Their words and their cursor do not.
    _click_the_ticket_behind(page, lands_on="rest")
    at_rest = page.evaluate(THE_DRAFT_IN_THE_BOX)
    assert at_rest["stillAttached"] is True, at_rest
    assert at_rest["text"] == carried_on, at_rest
    assert at_rest["caret"] == [mid_sentence + 4, mid_sentence + 4], at_rest

    # And back in, where a click puts the cursor wherever it landed, which is what a click
    # in a box is for.
    _click_the_composers_input(page)
    back_in_the_box = page.evaluate(THE_DRAFT_IN_THE_BOX)
    assert back_in_the_box["text"] == carried_on, back_in_the_box
    assert back_in_the_box["focused"] is True, back_in_the_box


def test_a_turn_starting_does_not_move_the_state_and_the_bar_says_what_is_happening(
    server, context_factory, open_page, cli
) -> None:
    """Nothing changes state on its own, and at rest the bar is what says anything at all.

    The turn's rows are in the record and this browser has them, so the line above the
    composer becomes the newest tool call with the plan's progress beside it. The layer
    does not move, because nobody moved it.
    """
    ticket_id, conversation_id = _a_ticket_with_a_conversation(server, cli, "A turn at rest")
    _append_rows(server, conversation_id, *_a_settled_conversation_worth_reading(THE_LAST_THING))
    context = context_factory()
    context.add_init_script(THE_SYSTEM_SAYS_A_TURN_IS_RUNNING)
    page = _the_ticket_page(server, context, open_page, ticket_id, ROWS_IN_THE_SEED)
    assert "The one line the bar shows" in page.inner_text(REST_LINE)

    rows_before = _rows_on_the_page(page)
    _append_rows(server, conversation_id, *_a_turn_getting_going())
    _let_the_browser_catch_up(page, rows_before + 1)
    page.wait_for_function(
        "() => { const line = document.querySelector('[data-conversation-rest-line]');"
        " return line !== null && line.textContent.includes('Ran command'); }",
        timeout=WAIT_MS,
    )

    working = page.evaluate(WHERE_THE_LAYER_IS)
    assert working["state"] == "rest", "a turn starting is not a person opening anything"
    assert working["transcriptOnScreen"] is False
    assert working["restBarOnScreen"] is True

    said = page.inner_text(REST_LINE)
    assert "Read file" not in said, ("the newest tool call, not the first", said)
    assert "The one line the bar shows" not in said, said
    # The plan's progress, quieter, beside it: three steps, and how far through them.
    beside_it = page.inner_text(REST_ASIDE).strip()
    assert beside_it != ""
    assert "3" in beside_it, beside_it


def test_a_permission_ask_does_not_move_the_state_and_the_bar_is_what_says_it_arrived(
    server, context_factory, open_page, cli
) -> None:
    """The ask arrives while the person has it peeked, and it leaves it peeked.

    Then they put it back themselves, and the bar does its most important job: with the
    conversation closed, a request that needs them is already on the screen.
    """
    ticket_id, conversation_id = _a_ticket_with_a_conversation(server, cli, "An ask arriving")
    _append_rows(server, conversation_id, *_a_settled_conversation_worth_reading(THE_LAST_THING))
    # An ask is only ever waiting inside a turn that is running, and a turn only runs
    # because the system says so.
    context = context_factory()
    context.add_init_script(THE_SYSTEM_SAYS_A_TURN_IS_RUNNING)
    page = _the_ticket_page(server, context, open_page, ticket_id, ROWS_IN_THE_SEED)

    _click_the_composers_input(page)
    rows_before = _rows_on_the_page(page)
    _append_rows(
        server,
        conversation_id,
        PromptEventPayload(
            content=text_message_content("clear the build out"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        ),
        PermissionAskedEventPayload(
            ask_id="three-states-ask",
            title=THE_ASK_TITLE,
            detail=None,
            options=(
                PermissionAskOption(option_id="allow", label="Allow", option_kind="allow_once"),
                PermissionAskOption(option_id="no", label="No", option_kind="reject_once"),
            ),
        ),
    )
    _let_the_browser_catch_up(page, rows_before + 2)
    page.wait_for_selector('[data-conversation-row="permission_ask"]', timeout=WAIT_MS)

    waiting = page.evaluate(WHERE_THE_LAYER_IS)
    assert waiting["state"] == "peeked", "no auto-peek, and no auto-anything-else either"
    assert waiting["restBarMounted"] is False, "the transcript is showing the ask itself"

    # The person puts it away themselves. Now the bar is the only thing left that can say
    # somebody is being waited on, and it says it.
    _click_the_ticket_behind(page, lands_on="rest")
    page.wait_for_selector(REST_WAITING, timeout=WAIT_MS)
    at_rest = page.evaluate(WHERE_THE_LAYER_IS)
    assert at_rest["state"] == "rest"
    assert at_rest["restBarOnScreen"] is True
    assert THE_ASK_TITLE in page.inner_text(REST_LINE)
