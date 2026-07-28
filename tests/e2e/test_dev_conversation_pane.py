"""The dev conversation pane loads real markup from the real server.

The first two exercises are the ones a browser must make and node tests cannot: the route
on a running ``panels serve`` renders the distinct empty state with the backend cards, and
a conversation created through the HTTP API reloads into the pane surface.

The rest are behaviours rather than markup — a message drawn before the server has
answered, the words coming back when it gets nowhere, and where the thread scrolls to as a
turn grows. All three are things only a browser does, so they are asserted here rather than
looked at.

Two things stand in for a real agent, which is not a repeatable gate. Rows are written
straight into the record with the store the server itself uses, and read back over HTTP the
way the browser reads any other row. And the send is held in the page, so the moment
between pressing Enter and the server answering is a moment the test can stand inside.
"""

from __future__ import annotations

import asyncio
import struct
import threading
import zlib
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from playwright.sync_api import BrowserContext, Page
from tests.e2e.harness import ServerHandle

from planner.conversation.contracts import AgentCommand, PromptDeliveryMode
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventPayload,
    ConversationTurnEnding,
    PromptEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
)
from planner.conversation.message_content import (
    MessageImage,
    MessageText,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore

WAIT_MS = 10_000
BACKEND_CARD_WAIT_MS = 30_000  # backend cards probe real CLIs with subprocess calls
JUMP_BUTTON = "[aria-label='Jump to latest message']"

# The send, held in the page until the test lets it go. Nothing else is touched: every
# other request is the browser's own fetch, and the body handed over is the one the app
# built, so what the test reads out of it is what the server would have received.
HOLD_THE_SEND = """
window.__heldSends = [];
const realFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = typeof input === 'string' ? input : input.url;
  if (typeof url === 'string' && url.includes('/send')) {
    return new Promise((resolve, reject) => {
      window.__heldSends.push({
        body: init && init.body ? JSON.parse(init.body) : null,
        answer: (fate) => resolve(new Response(JSON.stringify(fate), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        })),
        turnAway: (detail) => resolve(new Response(JSON.stringify({ detail }), {
          status: 422,
          headers: { 'Content-Type': 'application/json' }
        })),
        fail: () => reject(new TypeError('the send got nowhere'))
      });
    });
  }
  return realFetch(input, init);
};
"""

# The pane's own reads of the conversation, held in the page until the test lets them go.
# Asked for at the moment the pane asked, and answered later with what came back then — so
# a read taken while a turn was running still says what was true while it was running,
# however long the test spends between the two. Everything else is the browser's own fetch.
HOLD_THE_VIEW_READS = """
window.__holdViewReads = false;
window.__heldViewReads = [];
window.__viewReadsAnswered = 0;
const realFetch = window.fetch.bind(window);
const A_VIEW_READ = new RegExp('/api/conversation/conversations/[^/?]+$');
window.fetch = (input, init) => {
  const url = typeof input === 'string' ? input : input.url;
  const method = (init && init.method ? init.method : 'GET').toUpperCase();
  if (method === 'GET' && A_VIEW_READ.test(url)) {
    const cameBack = realFetch(input, init).then(async (response) => ({
      body: await response.text(),
      status: response.status
    }));
    const give = (held) => {
      window.__viewReadsAnswered += 1;
      return new Response(held.body, {
        status: held.status,
        headers: { 'Content-Type': 'application/json' }
      });
    };
    if (!window.__holdViewReads) return cameBack.then(give);
    return new Promise((resolve, reject) => {
      window.__heldViewReads.push(() => cameBack.then((held) => resolve(give(held)), reject));
    });
  }
  return realFetch(input, init);
};
window.__letTheHeldViewReadsThrough = () => {
  const waiting = window.__heldViewReads;
  window.__heldViewReads = [];
  window.__holdViewReads = false;
  for (const release of waiting) release();
  return waiting.length;
};
"""

# What the thread is doing, in the same terms the pane itself uses: where it is scrolled
# to, how much of it is in view, and where the bottom of its last piece of content sits.
WHERE_THE_THREAD_IS = """
() => {
  const thread = document.querySelector('[data-conversation-thread]');
  // The transcript lays its rows out in the thread rather than in a box of its own, so
  // anything without a shape is looked through to the things inside it that have one.
  const content = [];
  const consider = (element) => {
    if (element.hasAttribute('data-conversation-reserved-space')) return;
    if (element.getClientRects().length > 0) { content.push(element); return; }
    for (const inside of element.children) consider(inside);
  };
  for (const child of thread.children) consider(child);
  const last = content[content.length - 1];
  const threadTop = thread.getBoundingClientRect().top;
  const outgoing = thread.querySelector('[data-conversation-outgoing]');
  const room = thread.querySelector('[data-conversation-reserved-space]');
  const answers = thread.querySelectorAll('[data-conversation-row="agent_message"]');
  const newestAnswer = answers[answers.length - 1] ?? null;
  return {
    scrollTop: Math.round(thread.scrollTop),
    clientHeight: thread.clientHeight,
    lastContentBottom: Math.round(last.getBoundingClientRect().bottom - threadTop),
    outgoingTop: outgoing === null
      ? null
      : Math.round(outgoing.getBoundingClientRect().top - threadTop),
    roomKept: room === null ? 0 : Math.round(room.getBoundingClientRect().height),
    toolCallsOnScreen: thread.querySelectorAll('[data-conversation-tool]').length,
    // Where the newest answer sits in the reader's view, which is what has to stay put
    // when something above it changes height or disappears.
    newestAnswerTop: newestAnswer === null
      ? null
      : Math.round(newestAnswer.getBoundingClientRect().top - threadTop)
  };
}
"""


def _create_conversation(server: ServerHandle, conversation_id: str) -> None:
    """A conversation for the pane to open, made the way the browser makes one.

    The model is named because every start names one, and it is a name rather than a real
    model on purpose: nothing here spawns a backend, so what these conversations run on is
    never asked of a machine. The pane is what is under test.
    """
    created = httpx.post(
        f"{server.base}/api/conversation/conversations",
        json={
            "conversation_id": conversation_id,
            "model": "e2e-model",
            "backend_key": "codex",
        },
        timeout=10.0,
    )
    assert created.status_code == 201, created.text


def _solid_png(red: int, green: int, blue: int) -> bytes:
    """A real 8x8 PNG of one flat colour, built here rather than checked in.

    A picture a browser can genuinely decode, so that "it loaded" is a real claim. Built
    by hand because a test fixture that is a binary blob says nothing about what it is.
    """
    width = height = 8
    raw = b"".join(b"\x00" + bytes([red, green, blue]) * width for _ in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


_A_RED_PNG = _solid_png(255, 0, 0)


def _on_its_own_thread(work: Coroutine[Any, Any, Any]) -> Any:
    """Run one coroutine to completion from a thread that already has a loop on it."""
    done: list[Any] = []
    fell_over: list[BaseException] = []

    def run_it() -> None:
        try:
            done.append(asyncio.run(work))
        except BaseException as trouble:  # noqa: BLE001 - re-raised on the calling thread
            fell_over.append(trouble)

    worker = threading.Thread(target=run_it)
    worker.start()
    worker.join()
    if fell_over:
        raise fell_over[0]
    return done[0]


def _append_rows(
    server: ServerHandle, conversation_id: str, *payloads: ConversationEventPayload
) -> None:
    """Write rows into the record, exactly as the conversation system writes them.

    The store's calls are awaited, and this thread already belongs to the browser driver's
    own loop, so the writing happens on a thread of its own and this one waits for it.
    """
    store = ConversationStore(str(server.db_path))

    async def write() -> None:
        for payload in payloads:
            await store.append_event(conversation_id, payload)

    fell_over: list[BaseException] = []

    def run_it() -> None:
        try:
            asyncio.run(write())
        except BaseException as error:  # noqa: BLE001 - re-raised on the calling thread
            fell_over.append(error)

    writer = threading.Thread(target=run_it)
    writer.start()
    writer.join()
    if fell_over:
        raise fell_over[0]


def _store_the_commands_the_agent_reported(
    server: ServerHandle, conversation_id: str, *commands: AgentCommand
) -> None:
    """Put the agent's own menu where the backend puts it when it reports one."""
    _on_its_own_thread(
        ConversationStore(str(server.db_path)).replace_available_commands(
            conversation_id, commands
        )
    )


def _let_the_browser_catch_up(page: Page, rows_expected: int) -> None:
    """Ask the page what it asks itself when it comes back to a tab.

    The live tail only carries rows the running server wrote itself, so rows written
    beside it arrive the other way the pane already has: say which row you hold and take
    everything after it. That is the same call, down to the function.
    """
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function(
        "(expected) => document.querySelectorAll('[data-conversation-row]').length >= expected",
        arg=rows_expected,
        timeout=WAIT_MS,
    )


def _a_turn_full_of_tool_calls(first_call: int) -> tuple[ConversationEventPayload, ...]:
    """A turn whose work is what a reader scrolls up into — and what a fold takes away.

    A settled turn puts its tool calls behind its fold, which takes them out of the page
    entirely. That is the one thing in this thread that makes it shorter rather than
    longer, and it happens under a reader who is reading exactly those lines.
    """
    rows: list[ConversationEventPayload] = [
        PromptEventPayload(
            content=text_message_content("the question with the work"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        )
    ]
    for call in range(12):
        rows.append(
            ToolCallStartedEventPayload(
                tool_call_id=f"call-{first_call + call}",
                title=f"Tool call number {first_call + call}",
                tool_kind="read",
                detail=None,
            )
        )
        rows.append(
            ToolCallFinishedEventPayload(
                tool_call_id=f"call-{first_call + call}",
                tool_call_status=ToolCallStatus.completed,
                detail=None,
            )
        )
    rows.append(
        AgentMessageEventPayload(
            content=text_message_content(
                "\n\n".join(f"the answer under the work, line {at}" for at in range(4))
            )
        )
    )
    return tuple(rows)


def _a_conversation_worth_scrolling() -> tuple[ConversationEventPayload, ...]:
    rows: list[ConversationEventPayload] = []
    for turn in range(8):
        rows.append(
            PromptEventPayload(
                content=text_message_content(f"question {turn}"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
            )
        )
        rows.append(
            AgentMessageEventPayload(
                content=text_message_content(
                    "\n\n".join(f"answer {turn} line {at}" for at in range(6))
                )
            )
        )
        rows.append(TurnEndedEventPayload(ending=ConversationTurnEnding.completed))
    return tuple(rows)


def test_the_dev_route_renders_the_empty_state_and_backend_cards(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    page = open_page(
        context_factory(), server, "#/dev/conversation", "[data-conversation-route]"
    )
    # The empty state is a real surface, visibly distinct from a broken blank screen.
    page.wait_for_selector("[data-conversation-new]", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-new-id]", timeout=WAIT_MS)
    for backend_key in ("hermes", "codex", "claude"):
        page.wait_for_selector(
            f'[data-conversation-backend="{backend_key}"]', timeout=BACKEND_CARD_WAIT_MS
        )


def test_a_started_conversation_reloads_into_the_pane_surface(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    _create_conversation(server, "e2e-dev-pane")

    page = open_page(
        context_factory(),
        server,
        "#/dev/conversation?id=e2e-dev-pane",
        "[data-conversation-pane]",
    )
    page.wait_for_selector("[data-conversation-thread]", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-workspace]", timeout=WAIT_MS)


def test_a_sent_message_is_in_the_thread_before_the_server_answers(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Sending is not a round trip anybody should have to watch.

    Everything asserted while the send is held came out of what this browser already knew:
    the record has no row for this message and the server has not answered, so the thread
    can only be drawing the browser's own copy.
    """
    _create_conversation(server, "e2e-optimistic")
    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context, server, "#/dev/conversation?id=e2e-optimistic", "[data-conversation-pane]"
    )

    page.fill("[data-conversation-input]", "what is the plan")
    page.press("[data-conversation-input]", "Enter")
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)

    drawn = page.wait_for_selector("[data-conversation-outgoing]", timeout=WAIT_MS)
    assert drawn is not None
    assert drawn.inner_text().strip() == "what is the plan"
    # The box is theirs again straight away, and it never stopped being typeable.
    assert page.input_value("[data-conversation-input]") == ""
    assert page.is_enabled("[data-conversation-input]")
    # The one thing that says a send is still happening.
    page.wait_for_selector("[data-conversation-sending]", timeout=WAIT_MS)

    # The message carries the identity and the instant this browser minted for it.
    sent = page.evaluate("() => window.__heldSends[0].body")
    # The message goes out as the pieces it is made of, which for words typed into the box
    # is one piece of written words.
    assert sent["content"] == [{"piece": "text", "text": "what is the plan"}]
    assert isinstance(sent["sender_message_id"], str)
    assert sent["sender_message_id"] != ""
    assert sent["sent_at_unix_milliseconds"] > 1_700_000_000_000

    # Held for a busy agent. It stays where it was put and says it has reached nothing,
    # because nothing is answering it yet.
    page.evaluate("() => window.__heldSends[0].answer({ fate: 'queued', queue_position: 1 })")
    page.wait_for_selector("[data-conversation-outgoing-label]", timeout=WAIT_MS)
    assert "waiting for the agent to be free" in page.inner_text(
        "[data-conversation-outgoing-label]"
    )
    assert page.query_selector("[data-conversation-sending]") is None


def test_the_first_message_of_a_conversation_says_nothing_it_does_not_know(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """The first message is the one that starts the conversation on its way to a backend.

    Everything happens on that send: the conversation is created, its reading is opened,
    and only then does the message go. It is in flight perfectly normally throughout, so
    it must say nothing about itself — least of all on a cold start, which is the longest
    a person ever waits and the worst moment to be told their message may have gone
    nowhere.
    """
    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context, server, "#/dev/conversation?id=e2e-first-send", "[data-conversation-pane]"
    )
    # Nothing has been started yet: this is the empty state, not a conversation.
    page.wait_for_selector("[data-conversation-new]", timeout=WAIT_MS)
    # And a conversation is created on a model somebody can name, so the first message can
    # only make one once this page has read what the backend it is on runs. That read is a
    # real probe of a real CLI, so it is waited for the way the backend cards are.
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-new-model]').value !== ''",
        timeout=BACKEND_CARD_WAIT_MS,
    )

    page.fill("[data-conversation-input]", "the very first thing")
    page.press("[data-conversation-input]", "Enter")
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)

    drawn = page.wait_for_selector("[data-conversation-outgoing]", timeout=WAIT_MS)
    assert drawn is not None
    assert drawn.inner_text().strip() == "the very first thing"
    assert page.query_selector("[data-conversation-outgoing-label]") is None, (
        "a message on its way says nothing about itself"
    )
    # The conversation really was created on the way through, and the message really is
    # still in flight.
    assert httpx.get(
        f"{server.base}/api/conversation/conversations/e2e-first-send", timeout=10.0
    ).status_code == 200
    page.wait_for_selector("[data-conversation-sending]", timeout=WAIT_MS)

    page.evaluate("() => window.__heldSends[0].answer({ fate: 'started' })")
    page.wait_for_selector("[data-conversation-sending]", state="detached", timeout=WAIT_MS)
    assert page.query_selector("[data-conversation-outgoing-label]") is None


def test_a_send_that_gets_nowhere_gives_the_words_back(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    _create_conversation(server, "e2e-send-failed")
    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context, server, "#/dev/conversation?id=e2e-send-failed", "[data-conversation-pane]"
    )

    page.fill("[data-conversation-input]", "try this one")
    page.press("[data-conversation-input]", "Enter")
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)
    page.evaluate("() => window.__heldSends[0].turnAway('the server would not take that')")

    page.wait_for_selector("[data-conversation-error]", timeout=WAIT_MS)
    # The copy on screen goes, because nothing has it and no row is coming for it.
    assert page.query_selector("[data-conversation-outgoing]") is None
    assert page.input_value("[data-conversation-input]") == "try this one"
    assert page.evaluate(
        """() => {
          const box = document.querySelector('[data-conversation-input]');
          return [box.selectionStart, box.selectionEnd, document.activeElement === box];
        }"""
    ) == [len("try this one"), len("try this one"), True]

    # Unless something else has been written in the meantime. That draft is the thing that
    # matters, so it is left alone and the error under the box is the whole of the news.
    page.fill("[data-conversation-input]", "second try")
    page.press("[data-conversation-input]", "Enter")
    page.wait_for_function("() => window.__heldSends.length === 2", timeout=WAIT_MS)
    page.fill("[data-conversation-input]", "a different thought")
    page.evaluate("() => window.__heldSends[1].turnAway('no')")
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-outgoing]') === null",
        timeout=WAIT_MS,
    )
    assert page.input_value("[data-conversation-input]") == "a different thought"
    assert page.query_selector("[data-conversation-error]") is not None


def test_a_send_nobody_heard_the_end_of_is_not_offered_back_to_be_sent_again(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """No answer at all is not the same as being told no.

    The message may have arrived and it may not. Putting the words back in the box would
    leave a person one keystroke from sending the same thing twice, so the copy stays and
    says what is actually known about it, which is nothing.
    """
    _create_conversation(server, "e2e-send-unanswered")
    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context, server, "#/dev/conversation?id=e2e-send-unanswered", "[data-conversation-pane]"
    )

    page.fill("[data-conversation-input]", "did this arrive")
    page.press("[data-conversation-input]", "Enter")
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)
    page.evaluate("() => window.__heldSends[0].fail()")

    page.wait_for_selector("[data-conversation-error]", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-outgoing-label]", timeout=WAIT_MS)
    assert "the server never said whether this arrived" in page.inner_text(
        "[data-conversation-outgoing-label]"
    )
    assert page.inner_text("[data-conversation-outgoing]").strip().endswith("did this arrive")
    assert page.input_value("[data-conversation-input]") == ""


def test_a_reader_who_has_gone_elsewhere_is_left_where_they_are(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """A wheel is a person saying where they want to be. Nothing else here is."""
    conversation_id = "e2e-reader"
    _create_conversation(server, conversation_id)
    _append_rows(server, conversation_id, *_a_conversation_worth_scrolling())

    page = open_page(
        context_factory(),
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-pane]",
    )
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-row]').length >= 16",
        timeout=WAIT_MS,
    )
    at_the_end = page.evaluate(WHERE_THE_THREAD_IS)

    page.hover("[data-conversation-thread]")
    page.mouse.wheel(0, -400)
    page.wait_for_selector(JUMP_BUTTON, timeout=WAIT_MS)
    gone_reading = page.evaluate(WHERE_THE_THREAD_IS)
    assert gone_reading["scrollTop"] < at_the_end["scrollTop"]

    # Something arriving does not move somebody who is reading something else.
    _append_rows(
        server,
        conversation_id,
        AgentMessageEventPayload(content=text_message_content("a new answer")),
    )
    _let_the_browser_catch_up(page, 17)
    assert page.evaluate(WHERE_THE_THREAD_IS)["scrollTop"] == gone_reading["scrollTop"]

    # The jump is the way back, and it is the only one this pane offers.
    page.click(JUMP_BUTTON)
    page.wait_for_selector(JUMP_BUTTON, state="detached", timeout=WAIT_MS)
    back = page.evaluate(WHERE_THE_THREAD_IS)
    assert back["scrollTop"] > gone_reading["scrollTop"]
    assert back["lastContentBottom"] <= back["clientHeight"]


TURN_FOLD = "[data-conversation-turn-fold]"


def test_a_screenful_disappearing_above_the_reader_leaves_them_where_they_are(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """The thread getting shorter under somebody — the one direction that can strand them.

    Closing a turn's fold takes its whole work log off the page at once. A reader below it
    is reading something that has not changed at all, so it has to stay exactly where it
    is on their screen while a screenful vanishes above it.
    """
    conversation_id = "e2e-fold-closing"
    _create_conversation(server, conversation_id)
    _append_rows(
        server,
        conversation_id,
        *_a_turn_full_of_tool_calls(first_call=100),
        TurnEndedEventPayload(ending=ConversationTurnEnding.completed),
        *_a_conversation_worth_scrolling(),
    )

    page = open_page(
        context_factory(),
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-pane]",
    )
    page.wait_for_selector(TURN_FOLD, timeout=WAIT_MS)

    # Open the oldest turn's fold and its run of tool calls: a screenful of work log, back
    # on the page, well above where the reader is.
    page.evaluate("() => document.querySelector('[data-conversation-turn-fold]').click()")
    page.wait_for_selector("[data-conversation-work-fold]", timeout=WAIT_MS)
    page.evaluate("() => document.querySelector('[data-conversation-work-fold]').click()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-tool]').length > 4",
        timeout=WAIT_MS,
    )

    page.hover("[data-conversation-thread]")
    page.mouse.wheel(0, -200)
    page.wait_for_selector(JUMP_BUTTON, timeout=WAIT_MS)
    reading = page.evaluate(WHERE_THE_THREAD_IS)
    assert reading["newestAnswerTop"] is not None

    # And now it all goes.
    page.evaluate("() => document.querySelector('[data-conversation-turn-fold]').click()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-tool]').length === 0",
        timeout=WAIT_MS,
    )

    shrunk = page.evaluate(WHERE_THE_THREAD_IS)
    assert shrunk["scrollTop"] < reading["scrollTop"], "the thread moved under them"
    assert abs(shrunk["newestAnswerTop"] - reading["newestAnswerTop"]) <= 2, (reading, shrunk)


def test_something_above_the_reader_changing_height_leaves_them_where_they_are(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """A fold opening further up the thread is a screenful appearing above somebody.

    They asked for none of it and they are reading something else, so the line they are
    looking at has to stay exactly where it is on their screen — which means the thread
    has to move underneath them by however much appeared, and only by that much.
    """
    conversation_id = "e2e-height-above"
    _create_conversation(server, conversation_id)
    _append_rows(
        server,
        conversation_id,
        *_a_turn_full_of_tool_calls(first_call=200),
        TurnEndedEventPayload(ending=ConversationTurnEnding.completed),
        *_a_conversation_worth_scrolling(),
    )

    page = open_page(
        context_factory(),
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-pane]",
    )
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-row]').length >= 17",
        timeout=WAIT_MS,
    )

    # The reader goes off to read something in the middle. From here on nothing may move
    # them that they did not do themselves.
    page.hover("[data-conversation-thread]")
    page.mouse.wheel(0, -300)
    page.wait_for_selector(JUMP_BUTTON, timeout=WAIT_MS)
    reading = page.evaluate(WHERE_THE_THREAD_IS)
    assert reading["newestAnswerTop"] is not None

    # The oldest turn's fold — far above them — opens, and its run of tool calls with it.
    page.evaluate("() => document.querySelector('[data-conversation-turn-fold]').click()")
    page.wait_for_selector("[data-conversation-work-fold]", timeout=WAIT_MS)
    page.evaluate("() => document.querySelector('[data-conversation-work-fold]').click()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-tool]').length > 4",
        timeout=WAIT_MS,
    )

    grown = page.evaluate(WHERE_THE_THREAD_IS)
    assert grown["scrollTop"] > reading["scrollTop"], "the thread moved under them"
    assert abs(grown["newestAnswerTop"] - reading["newestAnswerTop"]) <= 2, (reading, grown)


def test_the_thread_follows_the_answer_instead_of_the_bottom(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Where the thread goes when you send, and what moves it afterwards."""
    conversation_id = "e2e-scroll"
    _create_conversation(server, conversation_id)
    _append_rows(server, conversation_id, *_a_conversation_worth_scrolling())

    context = context_factory()
    context.add_init_script(HOLD_THE_SEND)
    page = open_page(
        context,
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-pane]",
    )
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-row]').length >= 16",
        timeout=WAIT_MS,
    )

    # Opening a conversation puts you at the end of it.
    opened = page.evaluate(WHERE_THE_THREAD_IS)
    assert opened["scrollTop"] > 0, "a conversation this long has somewhere to scroll"
    assert opened["lastContentBottom"] <= opened["clientHeight"]

    page.fill("[data-conversation-input]", "the newest question")
    page.press("[data-conversation-input]", "Enter")
    page.wait_for_function("() => window.__heldSends.length === 1", timeout=WAIT_MS)
    page.wait_for_selector("[data-conversation-outgoing]", timeout=WAIT_MS)
    page.wait_for_function(
        "(was) => document.querySelector('[data-conversation-thread]').scrollTop > was",
        arg=opened["scrollTop"],
        timeout=WAIT_MS,
    )

    # The message you just sent settles near the top, with the rest of the view left for
    # the answer.
    sent_settled = page.evaluate(WHERE_THE_THREAD_IS)
    assert 0 <= sent_settled["outgoingTop"] <= 48, sent_settled
    assert sent_settled["scrollTop"] > opened["scrollTop"]

    # Its row arrives carrying the same identity. The copy stops being drawn and the row
    # is already in the place it was drawn in, so nothing moves.
    minted = page.evaluate("() => window.__heldSends[0].body")
    page.evaluate("() => window.__heldSends[0].answer({ fate: 'started' })")
    _append_rows(
        server,
        conversation_id,
        PromptEventPayload(
            content=text_message_content("the newest question"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            sender_message_id=minted["sender_message_id"],
            sent_at_unix_milliseconds=minted["sent_at_unix_milliseconds"],
        ),
    )
    _let_the_browser_catch_up(page, 17)
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-outgoing]') === null",
        timeout=WAIT_MS,
    )
    took_over = page.evaluate(WHERE_THE_THREAD_IS)
    assert took_over["scrollTop"] == sent_settled["scrollTop"], took_over

    assert took_over["roomKept"] > 0, "the room for the answer is being kept"

    # An answer that fits in the space that was kept for it moves nothing at all, and
    # takes up exactly as much of that space as it fills.
    _append_rows(
        server,
        conversation_id,
        AgentMessageEventPayload(content=text_message_content("a short answer")),
    )
    _let_the_browser_catch_up(page, 18)
    fitted = page.evaluate(WHERE_THE_THREAD_IS)
    assert fitted["scrollTop"] == took_over["scrollTop"], fitted
    assert 0 < fitted["roomKept"] < took_over["roomKept"], (took_over, fitted)

    # An answer that outgrows it is followed, by the least that keeps its last line in
    # sight, and never backwards.
    _append_rows(
        server,
        conversation_id,
        AgentMessageEventPayload(
            content=text_message_content(
                "\n\n".join(f"a much longer answer, line {at}" for at in range(60))
            )
        ),
    )
    _let_the_browser_catch_up(page, 19)
    page.wait_for_function(
        "(was) => document.querySelector('[data-conversation-thread]').scrollTop > was",
        arg=fitted["scrollTop"],
        timeout=WAIT_MS,
    )
    followed = page.evaluate(WHERE_THE_THREAD_IS)
    assert followed["scrollTop"] > fitted["scrollTop"]
    assert followed["lastContentBottom"] <= followed["clientHeight"]
    # An answer that outgrew the room has earned all of it back, and none is left over.
    assert followed["roomKept"] == 0, followed


def test_a_picture_in_the_record_is_drawn_and_really_loads(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """The whole path, in a browser, against the real server.

    A row that says a message had a picture in it, bytes kept beside the record, and an
    image the browser actually fetched and decoded. ``naturalWidth`` is the assertion that
    matters: an ``<img>`` pointing at nothing draws as a broken image and reports zero, so
    this fails if the route does not serve the file or serves it as the wrong thing.
    """
    _create_conversation(server, "e2e-picture")
    # This thread already belongs to the browser driver's own loop, so the keeping happens
    # on a thread of its own — the same way the rows above are written.
    kept = _on_its_own_thread(
        ConversationMessageFiles(str(server.db_path)).keep(
            "e2e-picture", _A_RED_PNG, media_type="image/png"
        )
    )
    _append_rows(
        server,
        "e2e-picture",
        PromptEventPayload(
            content=(
                MessageText(text="look at this"),
                MessageImage(
                    stored_file_id=kept.stored_file_id,
                    media_type="image/png",
                    file_name="red.png",
                ),
            ),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        ),
    )

    page = open_page(
        context_factory(),
        server,
        "#/dev/conversation?id=e2e-picture",
        "[data-conversation-pane]",
    )
    page.wait_for_selector("[data-conversation-piece='image']", timeout=WAIT_MS)
    page.wait_for_function(
        "() => {"
        "  const drawn = document.querySelector(\"[data-conversation-piece='image']\");"
        "  return drawn !== null && drawn.complete && drawn.naturalWidth === 8;"
        "}",
        timeout=WAIT_MS,
    )
    # The words that came with it are still beside it.
    assert "look at this" in page.inner_text("[data-conversation-row='prompt']")


def test_a_link_you_paste_reads_like_the_agent_s_links_do(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Your own words go through the same renderer the agent's do.

    A link pasted into your own message used to sit in the thread as literal text while
    the identical link in the agent's reply became a preview. Same thread, same link, two
    different things — which is what this asserts is over. It fails on the old behaviour,
    where a prompt row was drawn as plain text and contained no anchor at all.
    """
    _create_conversation(server, "e2e-own-link")
    _append_rows(
        server,
        "e2e-own-link",
        PromptEventPayload(
            content=text_message_content("have a look at [the docs](https://example.com/docs)"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        ),
        AgentMessageEventPayload(
            content=text_message_content("I read [the docs](https://example.com/docs)")
        ),
    )

    page = open_page(
        context_factory(),
        server,
        "#/dev/conversation?id=e2e-own-link",
        "[data-conversation-pane]",
    )
    page.wait_for_selector("[data-conversation-row='prompt'] a", timeout=WAIT_MS)
    mine = page.locator("[data-conversation-row='prompt'] a").first
    theirs = page.locator("[data-conversation-row='agent_message'] a").first
    assert mine.get_attribute("href") == "https://example.com/docs"
    assert theirs.get_attribute("href") == "https://example.com/docs"
    # The literal markdown is gone from both, which is what says it was rendered rather
    # than printed.
    assert "](" not in page.inner_text("[data-conversation-row='prompt']")


def test_the_commands_an_agent_reports_reach_the_menu_when_its_turn_stops(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """An agent reports its commands moments after its session starts.

    That is during its first turn, so a conversation opened before that turn was told
    none and asking again is the only thing that puts it right. A turn stopping is the
    occasion to ask, and this is that seen from outside: the commands are reported while
    the turn is open, and they are in the menu once it is over, on the page that was
    already there.

    What the pane knows about this conversation is held still while the turn runs, so the
    only thing that can put the commands in front of a person is the pane asking again.
    """
    conversation_id = "e2e-commands"
    _create_conversation(server, conversation_id)
    context = context_factory()
    context.add_init_script(HOLD_THE_VIEW_READS)
    page = open_page(
        context,
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-pane]",
    )
    page.wait_for_selector("[data-conversation-input]:not([disabled])", timeout=WAIT_MS)
    # Opening asks about the conversation twice — once to have it, once when the reading
    # of its rows is live. Both are in before anything is held, so what is held after this
    # is only what the pane asks from here on.
    page.wait_for_function("() => window.__viewReadsAnswered >= 2", timeout=WAIT_MS)
    page.evaluate("() => { window.__thisVeryPage = true; window.__holdViewReads = true; }")

    # A turn is open: a prompt reached the backend and nothing has ended it.
    _append_rows(
        server,
        conversation_id,
        PromptEventPayload(
            content=text_message_content("get started"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        ),
    )
    _let_the_browser_catch_up(page, 1)
    page.wait_for_selector("[data-conversation-alive]", timeout=WAIT_MS)

    # Moments into that turn, the agent says what it can be asked to do.
    _store_the_commands_the_agent_reported(
        server,
        conversation_id,
        AgentCommand(name="plan", description="Write the plan", argument_hint="[what to plan]"),
        AgentCommand(name="compact", description="Shrink the context"),
    )

    # Nothing has told the pane and nothing asks on a clock, so the menu is still the one
    # a conversation that had never run was given.
    page.fill("[data-conversation-input]", "/")
    page.wait_for_selector("[data-conversation-commands-empty]", timeout=WAIT_MS)
    assert page.locator("[data-conversation-command]").count() == 0

    # The turn stops. There is no ending row for it — only the system's own word, which is
    # the answer the pane has been holding.
    page.evaluate("() => window.__letTheHeldViewReadsThrough()")
    page.wait_for_selector("[data-conversation-alive]", state="detached", timeout=WAIT_MS)

    # And the commands are there, under the slash that was already typed.
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-command]').length === 2",
        timeout=WAIT_MS,
    )
    assert page.eval_on_selector_all(
        "[data-conversation-command]",
        "rows => rows.map(row => row.dataset.conversationCommand)",
    ) == ["compact", "plan"]
    assert "[what to plan]" in page.inner_text("[data-conversation-commands]")
    assert page.evaluate("() => window.__thisVeryPage === true"), (
        "the page that has the commands is the page that was already open"
    )
