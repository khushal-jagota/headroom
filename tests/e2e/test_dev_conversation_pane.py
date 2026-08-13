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
from pathlib import Path
from typing import Any, cast

import httpx
from playwright.sync_api import BrowserContext, Page, Route
from tests.e2e.harness import ServerHandle

from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    PromptDeliveryMode,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationEventPayload,
    ConversationTurnEnding,
    PromptEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
    UserInputOption,
    UserInputQuestion,
    UserInputRequestedEventPayload,
)
from planner.conversation.message_content import (
    MessageImage,
    MessageText,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore

WAIT_MS = 10_000
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


def _store_the_composer_catalog(
    server: ServerHandle, conversation_id: str, *entries: ComposerCatalogEntry
) -> None:
    """Put the composer catalog where a backend report puts it."""
    _on_its_own_thread(
        ConversationStore(str(server.db_path)).replace_composer_catalog(conversation_id, entries)
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


def test_agent_questions_survive_reload_submit_as_one_map_and_replay_answers(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
    tmp_path: Path,
) -> None:
    """The real pane consumes durable rows and sends the real user-input API body."""
    conversation_id = "e2e-agent-questions"
    _create_conversation(server, conversation_id)
    questions = (
        UserInputQuestion(
            question_id="scope",
            header="Scope",
            question="Which surfaces should change?",
            options=(
                UserInputOption(label="Composer", description="The live composer"),
                UserInputOption(label="Transcript", description="Durable history"),
            ),
            multi_select=True,
            allow_other=True,
        ),
        UserInputQuestion(
            question_id="proof",
            header="Proof",
            question="What proof should be required?",
            options=(UserInputOption(label="Browser test", description="Exercise the full flow"),),
            multi_select=False,
            allow_other=True,
        ),
    )
    _append_rows(
        server,
        conversation_id,
        PromptEventPayload(
            content=text_message_content("ask me the implementation questions"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
        ),
        UserInputRequestedEventPayload(request_id="input-1", questions=questions),
    )

    submitted: list[dict[str, Any]] = []
    context = context_factory()

    def running_view(route: Any) -> None:
        response = route.fetch()
        body = response.json()
        body["is_running"] = True
        body["pending_user_input"] = {
            "request_id": "input-1",
            "questions": [
                {
                    "question_id": question.question_id,
                    "header": question.header,
                    "question": question.question,
                    "options": [
                        {"label": option.label, "description": option.description}
                        for option in question.options
                    ],
                    "multi_select": question.multi_select,
                    "allow_other": question.allow_other,
                }
                for question in questions
            ],
        }
        route.fulfill(response=response, json=body)

    context.route(f"**/api/conversation/conversations/{conversation_id}", running_view)

    def take_user_input_answer(route: Any) -> None:
        submitted.append(route.request.post_data_json)
        route.fulfill(status=200, json={"landed": True})

    context.route(
        f"**/api/conversation/conversations/{conversation_id}/user-input-answers",
        take_user_input_answer,
    )
    page = open_page(
        context,
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-user-input-panel]",
    )
    assert "Approve" not in page.locator("[data-user-input-panel]").inner_text()

    page.reload()
    page.wait_for_selector("[data-user-input-panel]", timeout=WAIT_MS)
    screenshot = tmp_path / "agent-questions.png"
    page.screenshot(path=str(screenshot))
    assert screenshot.stat().st_size > 0

    page.locator('[data-user-input-option="Composer"]').click()
    page.locator('[data-user-input-option="Transcript"]').click()
    page.locator("[data-user-input-continue]").click()
    page.locator("[data-user-input-other]").fill("Recorded event replay")
    page.locator("[data-user-input-continue]").click()
    page.wait_for_function("() => document.querySelector('[data-user-input-panel]') !== null")
    assert submitted == [
        {
            "request_id": "input-1",
            "answers": {
                "scope": {"answers": ["Composer", "Transcript"]},
                "proof": {"answers": ["Recorded event replay"]},
            },
        }
    ]

    _append_rows(
        server,
        conversation_id,
        UserInputAnsweredEventPayload(
            request_id="input-1",
            answers=(
                UserInputAnswer("scope", ("Composer", "Transcript")),
                UserInputAnswer("proof", ("Recorded event replay",)),
            ),
        ),
    )
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector('[data-conversation-user-input-state="answered"]', timeout=WAIT_MS)
    assert (
        "Recorded event replay"
        in page.locator("[data-conversation-user-input-answers]").inner_text()
    )


def test_canonical_queue_rows_work_across_tabs_and_on_a_phone(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """Two binders draw and act on one server-owned FIFO stack."""
    conversation_id = "e2e-canonical-queue"
    _create_conversation(server, conversation_id)
    view = httpx.get(
        f"{server.base}/api/conversation/conversations/{conversation_id}",
        timeout=10.0,
    ).json()
    held = [
        {
            "held_prompt_id": f"held-{number}",
            "text": f"queued message {number}",
            "sender_label": "owner",
            "sender_message_id": f"sender-{number}",
            "sent_at_unix_milliseconds": 1_700_000_000_000 + number,
        }
        for number in range(1, 7)
    ]
    actions: list[tuple[str, str]] = []

    def queue_api(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        view_path = f"/api/conversation/conversations/{conversation_id}"
        if request.method == "GET" and path.endswith(view_path):
            route.fulfill(
                json={**view, "backend_key": "hermes", "is_running": True, "held_prompts": held}
            )
            return
        if request.method == "DELETE" and "/held-prompts/" in path:
            held_prompt_id = path.rsplit("/", 1)[-1]
            actions.append((held_prompt_id, "discard"))
            held[:] = [item for item in held if item["held_prompt_id"] != held_prompt_id]
            route.fulfill(json={"discarded": True})
            return
        if request.method == "POST" and path.endswith("/promote"):
            held_prompt_id = path.rsplit("/", 2)[-2]
            payload = request.post_data_json
            assert payload is not None
            mode = str(payload["mode"])
            actions.append((held_prompt_id, mode))
            held[:] = [item for item in held if item["held_prompt_id"] != held_prompt_id]
            route.fulfill(
                json={
                    "promoted": True,
                    "fate": "injected" if mode == "steer" else "started",
                }
            )
            return
        route.continue_()

    context = context_factory()
    context.route(
        f"**/api/conversation/conversations/{conversation_id}**",
        queue_api,
    )
    desktop = open_page(
        context,
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-held-stack]",
    )
    phone = open_page(
        context,
        server,
        f"#/dev/conversation?id={conversation_id}",
        "[data-conversation-held-stack]",
    )
    phone.set_viewport_size({"width": 390, "height": 844})

    expected = [f"queued message {number}" for number in range(1, 7)]
    for page in (desktop, phone):
        assert (
            page.locator("[data-conversation-held-row] .chat-qrow-txt").all_inner_texts()
            == expected
        )

    def control_signature(page: Page) -> dict[str, object]:
        return cast(
            dict[str, object],
            page.evaluate(
                """() => {
              const signature = (button) => {
                const style = getComputedStyle(button);
                return {
                  label: button.getAttribute('aria-label') || button.textContent.trim(),
                  className: button.className,
                  borderRadius: style.borderRadius,
                  fontFamily: style.fontFamily,
                  fontSize: style.fontSize,
                  height: style.height,
                  padding: style.padding,
                };
              };
              const row = document.querySelector('[data-conversation-held-row]');
              const foot = document.querySelector('.chat-foot');
              return {
                queue: [...row.querySelectorAll('button')].map(signature),
                input: [...foot.querySelectorAll('button')].map(signature),
                inputWrap: getComputedStyle(foot).flexWrap,
              };
            }"""
            ),
        )

    desktop_controls = control_signature(desktop)
    phone_controls = control_signature(phone)
    assert desktop_controls == phone_controls
    assert desktop_controls["inputWrap"] == "nowrap"

    phone.locator(".c2-route").evaluate(
        "route => route.style.setProperty('--conversation-safe-area-bottom', '34px')"
    )

    assert phone.evaluate(
        "() => {"
        " const stack = document.querySelector('.chat-queue-stack');"
        " const text = document.querySelector('.chat-qrow-txt');"
        " const composer = document.querySelector('[data-conversation-composer]');"
        " const bottomNav = document.querySelector('.shell-nav');"
        " return stack.scrollHeight > stack.clientHeight"
        "   && getComputedStyle(text).whiteSpace === 'nowrap'"
        "   && composer.getBoundingClientRect().bottom"
        "     <= bottomNav.getBoundingClientRect().top"
        "   && document.documentElement.scrollWidth <= window.innerWidth;"
        "}"
    )

    desktop.click('[data-conversation-held-promote="steer"][data-held-prompt-id="held-1"]')
    desktop.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 5",
        timeout=WAIT_MS,
    )
    assert actions[-1] == ("held-1", "steer")

    # The other binder asks the canonical snapshot again when its live connection opens.
    phone.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    phone.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 5",
        timeout=WAIT_MS,
    )
    assert "queued message 1" not in phone.inner_text("[data-conversation-held-stack]")

    phone.click('[data-conversation-held-discard="held-2"]')
    phone.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 4",
        timeout=WAIT_MS,
    )
    assert actions[-1] == ("held-2", "discard")

    desktop.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
    desktop.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 4",
        timeout=WAIT_MS,
    )
    desktop.click('[data-conversation-held-promote="send_now"][data-held-prompt-id="held-3"]')
    desktop.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-held-row]').length === 3",
        timeout=WAIT_MS,
    )
    assert actions[-1] == ("held-3", "send_now")


TURN_FOLD = "[data-conversation-turn-fold]"


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


def test_the_mixed_catalog_an_agent_reports_reaches_the_menu_when_its_turn_stops(
    server: ServerHandle,
    context_factory: Callable[[], BrowserContext],
    open_page: Callable[..., Page],
) -> None:
    """An agent reports its mixed composer catalog moments after its session starts.

    That is during its first turn, so a conversation opened before that turn was told
    none and asking again is the only thing that puts it right. A turn stopping is the
    occasion to ask, and this is that seen from outside: the entries are reported while
    the turn is open, and they are in the menu once it is over, on the page that was
    already there.

    What the pane knows about this conversation is held still while the turn runs, so the
    only thing that can put the entries in front of a person is the pane asking again.
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
    _store_the_composer_catalog(
        server,
        conversation_id,
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind.command,
            display_text="/plan",
            insertion_text="/plan ",
            description="Write the plan",
            argument_hint="[what to plan]",
        ),
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind.command,
            display_text="/compact",
            insertion_text="/compact",
            description="Shrink the context",
        ),
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind.skill,
            display_text="$skill-creator",
            insertion_text="$skill-creator ",
            description="Create or update a skill",
        ),
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind.app,
            display_text="@Google Drive",
            insertion_text="@google-drive ",
            description="Use Google Drive",
        ),
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind.plugin,
            display_text="@Analytics",
            insertion_text="@analytics@personal ",
            description="Use Analytics",
        ),
    )

    # Nothing has told the pane and nothing asks on a clock, so the menu is still the one
    # a conversation that had never run was given.
    page.fill("[data-conversation-input]", "/")
    page.wait_for_selector("[data-conversation-catalog-empty]", timeout=WAIT_MS)
    assert page.locator("[data-conversation-catalog-entry]").count() == 0

    # The turn stops. There is no ending row for it — only the system's own word, which is
    # the answer the pane has been holding.
    page.evaluate("() => window.__letTheHeldViewReadsThrough()")
    page.wait_for_selector("[data-conversation-alive]", state="detached", timeout=WAIT_MS)

    # And the commands are there, under the slash that was already typed.
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-catalog-entry]').length === 2",
        timeout=WAIT_MS,
    )
    assert page.eval_on_selector_all(
        "[data-conversation-catalog-entry]",
        "rows => rows.map(row => row.dataset.conversationCatalogEntry)",
    ) == ["/compact", "/plan"]
    assert "[what to plan]" in page.inner_text("[data-conversation-catalog]")
    page.fill("[data-conversation-input]", "$")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-catalog-entry]').length === 1",
        timeout=WAIT_MS,
    )
    assert page.get_by_text("$skill-creator", exact=True).is_visible()
    page.fill("[data-conversation-input]", "@")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-catalog-entry]').length === 2",
        timeout=WAIT_MS,
    )
    assert page.eval_on_selector_all(
        "[data-conversation-catalog-entry]",
        "rows => rows.map(row => row.dataset.conversationCatalogEntry)",
    ) == ["@Analytics", "@Google Drive"]
    page.get_by_text("@Analytics", exact=True).click()
    assert page.input_value("[data-conversation-input]") == "@analytics@personal "
    assert page.evaluate("() => window.__thisVeryPage === true"), (
        "the page that has the commands is the page that was already open"
    )
