/** The conversation pane's decisions, tested where they are made.
 *
 * Everything the pane decides lives in plain modules under src/lib/conversation2, so it
 * can be asked directly rather than through a rendered page: what a reader is holding
 * after a tail replayed over itself, what a reconnect asks for, what a picker changes,
 * and what an ask offers when its shape is not one Panels understands.
 *
 * The modules are TypeScript, so they are transpiled into one temp directory and
 * imported. transpileModule keeps extensionless specifiers, which Node ESM will not
 * resolve, so the surviving value imports are rewritten to .mjs before they are written.
 */

import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const compilerOptions = {
  module: ts.ModuleKind.ES2022,
  target: ts.ScriptTarget.ES2022,
  verbatimModuleSyntax: true
};

const directory = await mkdtemp(join(tmpdir(), "planner-conversation2-"));

async function transpile(name) {
  const source = await readFile(
    new URL(`../src/lib/conversation2/${name}.ts`, import.meta.url),
    "utf8"
  );
  return ts
    .transpileModule(source, { compilerOptions })
    .outputText.replace(/from\s+["']\.\/(wire|feed|transcript|composer)["']/g, 'from "./$1.mjs"');
}

for (const name of ["wire", "feed", "transcript", "composer"]) {
  await writeFile(join(directory, `${name}.mjs`), await transpile(name), "utf8");
}

const wire = await import(join(directory, "wire.mjs"));
const {
  emptyConversationFeed,
  feedWithCommittedEvent,
  feedWithCommittedEvents,
  feedWithLiveFrame,
  conversationIsRunning,
  conversationLiveness,
  currentRunValues,
  createConversationStream
} = await import(join(directory, "feed.mjs"));
const { transcriptRows, liveAskFrom, askDeadSentence, refusalSentence, turnEndingSentence } =
  await import(join(directory, "transcript.mjs"));
const {
  askActions,
  askIsGeneric,
  askPlaceholder,
  armedChangeFor,
  deliveryOptionsFor,
  deliveryModeIsOffered,
  fateSentence,
  hasArmedChange,
  sendBodyFor
} = await import(join(directory, "composer.mjs"));

function event(sequence, kind, payload) {
  return { conversation_id: "c1", sequence, kind, payload, created_at: 1_700_000_000 };
}

const PROMPT = (sequence, text = "hello", mode = "run_when_free") =>
  event(sequence, "prompt", { text, sender_label: "owner", mode });
const AGENT = (sequence, text) => event(sequence, "agent_message", { text });
const TURN_ENDED = (sequence, ending = "completed", error_summary = null) =>
  event(sequence, "turn_ended", { ending, error_summary });

// --- the record a reader holds ------------------------------------------------------------

{
  // A replay that overlaps a live tail leaves one of each row, in order.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvents(feed, [PROMPT(1), AGENT(2, "one"), AGENT(3, "two")]);
  feed = feedWithCommittedEvents(feed, [AGENT(2, "one"), AGENT(3, "two"), AGENT(4, "three")]);
  assert.deepEqual(
    feed.events.map((held) => held.sequence),
    [1, 2, 3, 4],
    "a row that arrives twice is held once"
  );
  assert.equal(feed.latestSequence, 4);
}

{
  // The same sequence arriving again is the same row rewritten, not a second one.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvent(feed, AGENT(2, "first reading"));
  feed = feedWithCommittedEvent(feed, AGENT(2, "second reading"));
  assert.equal(feed.events.length, 1);
  assert.equal(feed.events[0].payload.text, "second reading");
}

{
  // A row that arrives out of order takes its place by sequence rather than by arrival.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvent(feed, AGENT(3, "late"));
  feed = feedWithCommittedEvent(feed, PROMPT(1));
  assert.deepEqual(feed.events.map((held) => held.sequence), [1, 3]);
  assert.equal(feed.latestSequence, 3);
}

{
  // Half-finished text is shown, then replaced by its row — never kept beside it.
  let feed = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(0));
  feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "he" });
  feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "llo" });
  assert.equal(feed.streamingAgentText, "hello");
  assert.equal(feed.events.length, 1, "a live frame is never a row");
  feed = feedWithCommittedEvent(feed, AGENT(1, "hello there"));
  assert.equal(feed.streamingAgentText, "");
  assert.equal(feed.events.length, 2);
}

{
  // A frame that arrives after its own turn's rows is a ghost. The tail can hand one over
  // behind the replay it interrupted, and drawing it would put half a message back on
  // screen underneath the finished one.
  let feed = feedWithCommittedEvents(emptyConversationFeed(), [
    PROMPT(1),
    AGENT(2, "the whole message"),
    TURN_ENDED(3)
  ]);
  feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "the who" });
  assert.equal(feed.streamingAgentText, "", "a delta after the turn ended is dropped");
  feed = feedWithLiveFrame(feed, {
    frame: "tool_call_progress",
    tool_call_id: "t1",
    detail: "still going"
  });
  assert.deepEqual(feed.toolCallProgress, {}, "so is tool progress from a turn that is over");
  assert.equal(
    transcriptRows(feed).some((row) => row.kind === "streaming_agent_message"),
    false
  );

  // The same frame during a running turn is exactly what it was always for.
  let live = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(1));
  live = feedWithLiveFrame(live, { frame: "agent_message_delta", text_delta: "arriving" });
  assert.equal(live.streamingAgentText, "arriving");
}

{
  // Tool progress is dropped by the finish it was leading up to, and by the turn's end.
  let feed = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(0));
  feed = feedWithLiveFrame(feed, { frame: "tool_call_progress", tool_call_id: "t1", detail: "…" });
  feed = feedWithLiveFrame(feed, { frame: "tool_call_progress", tool_call_id: "t2", detail: "…" });
  assert.deepEqual(Object.keys(feed.toolCallProgress).sort(), ["t1", "t2"]);
  feed = feedWithCommittedEvent(
    feed,
    event(1, "tool_call_finished", { tool_call_id: "t1", tool_call_status: "completed", detail: null })
  );
  assert.deepEqual(Object.keys(feed.toolCallProgress), ["t2"]);
  feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "partial" });
  feed = feedWithCommittedEvent(feed, TURN_ENDED(2, "interrupted"));
  assert.deepEqual(feed.toolCallProgress, {}, "nothing half-finished outlives its turn");
  assert.equal(feed.streamingAgentText, "");
}

{
  // Running-ness is read from the rows, so a live tail keeps it current without a fetch.
  let feed = emptyConversationFeed();
  assert.equal(conversationIsRunning(feed), false);
  feed = feedWithCommittedEvent(feed, PROMPT(1));
  assert.equal(conversationIsRunning(feed), true);
  feed = feedWithCommittedEvent(feed, TURN_ENDED(2));
  assert.equal(conversationIsRunning(feed), false);
}

// --- a turn that stopped without an ending ---------------------------------------------------

{
  // Ending a turn is a row, so a server that went away mid-turn wrote none: the rows are
  // left saying "running" forever. A snapshot that had already seen every row this reader
  // holds is the only thing that can say otherwise, and it is believed.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    PROMPT(1),
    event(2, "permission_asked", { ask_id: "a1", title: "Run ls", detail: null, options: [] })
  ]);
  assert.equal(conversationIsRunning(feed), true, "the rows alone still say running");

  const liveness = conversationLiveness(feed, { latestSequence: 2, isRunning: false });
  assert.deepEqual(liveness, { isRunning: false, turnStoppedWithoutAnEnding: true });

  const rows = transcriptRows(feed, {
    turnStoppedWithoutAnEnding: liveness.turnStoppedWithoutAnEnding
  });
  const askRow = rows.find((row) => row.kind === "permission_ask");
  assert.equal(askRow.state, "dead", "an ask does not outlive the turn it was waiting on");
  assert.equal(askRow.deadReason, "no_ending_recorded");
  assert.equal(askDeadSentence(askRow.deadReason), "expired — its turn stopped without an ending");
  assert.equal(liveAskFrom(rows), null, "so it stops holding the composer");
  assert.equal(
    rows.some((row) => row.kind === "turn_stopped"),
    true,
    "the thread says the ending the record will never contain"
  );
}

{
  // The live path is untouched: a snapshot taken before the newest row cannot overrule it.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    TURN_ENDED(1),
    PROMPT(2, "the turn that is running now")
  ]);
  assert.deepEqual(
    conversationLiveness(feed, { latestSequence: 1, isRunning: false }),
    { isRunning: true, turnStoppedWithoutAnEnding: false },
    "rows newer than the snapshot stay authoritative"
  );
  const rows = transcriptRows(feed, { turnStoppedWithoutAnEnding: false });
  assert.equal(rows.some((row) => row.kind === "turn_stopped"), false);
}

{
  // With no snapshot at all there is nothing to reconcile against, so the rows stand.
  const running = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(1));
  assert.deepEqual(conversationLiveness(running, null), {
    isRunning: true,
    turnStoppedWithoutAnEnding: false
  });

  // A fresh snapshot that says running while the rows show an ending is a turn whose
  // prompt row has not arrived yet — believed, and not a stopped turn.
  const ended = feedWithCommittedEvents(emptyConversationFeed(), [PROMPT(1), TURN_ENDED(2)]);
  assert.deepEqual(conversationLiveness(ended, { latestSequence: 3, isRunning: true }), {
    isRunning: true,
    turnStoppedWithoutAnEnding: false
  });

  // An idle conversation the rows already agree about is not a stopped turn either.
  assert.deepEqual(conversationLiveness(ended, { latestSequence: 2, isRunning: false }), {
    isRunning: false,
    turnStoppedWithoutAnEnding: false
  });
}

{
  // The picker shows what the conversation runs on now, which is the last word on it.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvents(feed, [
    event(1, "model_changed", { model: "opus", reasoning_effort: "high" }),
    event(2, "model_changed", { model: "sonnet", reasoning_effort: "low" })
  ]);
  assert.deepEqual(currentRunValues({ model: "fable", reasoningEffort: null }, feed), {
    model: "sonnet",
    reasoningEffort: "low"
  });
  assert.deepEqual(
    currentRunValues({ model: "fable", reasoningEffort: null }, emptyConversationFeed()),
    { model: "fable", reasoningEffort: null },
    "with no change rows the conversation runs on what it started on"
  );
}

// --- reconnecting is a fetch, then a tail --------------------------------------------------

{
  const calls = [];
  let liveHandlers = null;
  let openTails = 0;
  let closedTails = 0;
  const ports = {
    async readEventsAfter(id, after) {
      calls.push(["fetch", id, after]);
      return after === 0 ? [PROMPT(1), AGENT(2, "first")] : [AGENT(3, "second")];
    },
    openTail(id, after, handlers) {
      calls.push(["tail", id, after]);
      liveHandlers = handlers;
      openTails += 1;
      return () => {
        closedTails += 1;
      };
    }
  };

  let published = emptyConversationFeed();
  let connectedCount = 0;
  const stream = createConversationStream(
    "c1",
    ports,
    (next) => (published = next),
    () => (connectedCount += 1)
  );
  await stream.connect();
  assert.equal(connectedCount, 1, "connecting is where a reader asks the system about itself");
  assert.deepEqual(calls, [
    ["fetch", "c1", 0],
    ["tail", "c1", 2]
  ], "opening fetches from the position held, then tails from where the fetch got to");
  assert.equal(published.latestSequence, 2);

  liveHandlers.onLiveFrame({ frame: "agent_message_delta", text_delta: "streaming" });
  assert.equal(published.streamingAgentText, "streaming");

  // Trouble is not a special path: it is the same fetch-then-tail from the row held.
  const troubled = liveHandlers;
  troubled.onTrouble();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls.slice(2), [
    ["fetch", "c1", 2],
    ["tail", "c1", 3]
  ], "reconnecting asks for what is missing rather than the whole conversation again");
  assert.equal(openTails, 2);
  assert.equal(closedTails, 1, "the old tail is closed before another is opened");
  assert.equal(
    connectedCount,
    2,
    "and it asks again after a reconnect, which is the after-a-restart path"
  );
  assert.deepEqual(published.events.map((held) => held.sequence), [1, 2, 3]);

  stream.close();
  assert.equal(closedTails, 2);
}

// --- the thread ----------------------------------------------------------------------------

{
  // A tool call is one line that fills in its own mark; an answer folds into its ask.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvents(feed, [
    PROMPT(1, "do it"),
    event(2, "tool_call_started", {
      tool_call_id: "t1",
      title: "Read file",
      tool_kind: "read",
      detail: null
    }),
    event(3, "tool_call_finished", {
      tool_call_id: "t1",
      tool_call_status: "completed",
      detail: "42 lines"
    })
  ]);
  const rows = transcriptRows(feed);
  const toolRows = rows.filter((row) => row.kind === "tool_call");
  assert.equal(toolRows.length, 1, "a start and its finish are one line");
  assert.equal(toolRows[0].status, "completed");
  assert.equal(toolRows[0].detail, "42 lines");
  assert.equal(rows[0].kind, "prompt");
  assert.equal(rows[0].mode, "run_when_free");
}

{
  // An ask that was never answered and whose turn has ended is dead, and says so.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvents(feed, [
    PROMPT(1),
    event(2, "permission_asked", {
      ask_id: "a1",
      title: "Run rm -rf",
      detail: "in ~/Coding",
      options: [{ option_id: "allow_once", label: "Approve once", option_kind: "allow_once" }]
    }),
    TURN_ENDED(3, "interrupted")
  ]);
  const rows = transcriptRows(feed);
  const askRow = rows.find((row) => row.kind === "permission_ask");
  assert.equal(askRow.state, "dead");
  assert.equal(liveAskFrom(rows), null, "a dead ask never takes the composer over");
}

{
  // An answered ask shows the answer it took, by the backend's own label for it.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvents(feed, [
    PROMPT(1),
    event(2, "permission_asked", {
      ask_id: "a1",
      title: "Run ls",
      detail: null,
      options: [
        { option_id: "o-reject", label: "No", option_kind: "reject_once" },
        { option_id: "o-allow", label: "Yes", option_kind: "allow_once" }
      ]
    }),
    event(3, "permission_answered", { ask_id: "a1", option_id: "o-allow" })
  ]);
  const rows = transcriptRows(feed);
  const askRow = rows.find((row) => row.kind === "permission_ask");
  assert.equal(askRow.state, "answered");
  assert.equal(askRow.answeredOptionLabel, "Yes");
  assert.equal(liveAskFrom(rows), null);
}

{
  // An ask with a turn still running is the one waiting for a person.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvents(feed, [
    PROMPT(1),
    event(2, "permission_asked", { ask_id: "a1", title: "Run ls", detail: null, options: [] })
  ]);
  const rows = transcriptRows(feed);
  assert.equal(liveAskFrom(rows).askId, "a1");
  assert.equal(liveAskFrom(rows).state, "live");
}

{
  // Text still arriving is a line of its own, and it goes away when its row lands.
  let feed = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(1));
  feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "half" });
  assert.equal(transcriptRows(feed).at(-1).kind, "streaming_agent_message");
  feed = feedWithCommittedEvent(feed, AGENT(2, "half a message, finished"));
  const rows = transcriptRows(feed);
  assert.equal(rows.length, 2);
  assert.equal(rows[1].kind, "agent_message");
}

{
  // A refusal is a fate reported in words, and only a dequeued one is ever a row.
  let feed = emptyConversationFeed();
  feed = feedWithCommittedEvent(
    feed,
    event(1, "prompt_delivery_refused", {
      text: "held text",
      sender_label: "owner",
      mode: "run_when_free",
      refusal_reason: "backend_did_not_start"
    })
  );
  const row = transcriptRows(feed)[0];
  assert.equal(row.kind, "prompt_refused");
  assert.equal(row.sentence, refusalSentence("backend_did_not_start"));
  assert.match(row.sentence, /would not start/);
  assert.equal(turnEndingSentence("failed", "child died"), "turn failed · child died");
  assert.equal(turnEndingSentence("interrupted", null), "turn interrupted");
}

// --- what the composer offers ---------------------------------------------------------------

{
  // Steering is a per-backend fact: the backends that cannot do it are not offered it.
  assert.deepEqual(
    deliveryOptionsFor("hermes").map((option) => option.mode),
    ["run_when_free", "send_now", "steer"]
  );
  for (const backendKey of ["codex", "claude", null]) {
    assert.deepEqual(
      deliveryOptionsFor(backendKey).map((option) => option.mode),
      ["run_when_free", "send_now"],
      `${backendKey} is not offered steering at all`
    );
    assert.equal(deliveryModeIsOffered(backendKey, "steer"), false);
  }
  assert.equal(deliveryModeIsOffered("hermes", "steer"), true);
  assert.equal(wire.backendSupportsSteer("hermes"), true);
  assert.equal(wire.backendSupportsSteer("codex"), false);
}

{
  // A well-formed ask keeps the backend's own answers, ordered by how much they commit to.
  const ask = {
    options: [
      { option_id: "o-allow-always", label: "Always allow this session", option_kind: "allow_always" },
      { option_id: "o-allow", label: "Approve once", option_kind: "allow_once" },
      { option_id: "o-reject", label: "Decline", option_kind: "reject_once" }
    ]
  };
  const actions = askActions(ask);
  assert.deepEqual(
    actions.map((action) => (action.act === "cancel_turn" ? "cancel_turn" : action.optionId)),
    ["cancel_turn", "o-reject", "o-allow-always", "o-allow"]
  );
  assert.equal(actions.at(-1).emphasis, "primary");
  assert.equal(actions.every((action) => action.supplied), true);
  assert.equal(askIsGeneric(ask), false);
}

{
  // The obligation: an ask nobody understands is still answerable, and cancelling always is.
  for (const ask of [null, {}, { options: [] }]) {
    const actions = askActions(ask);
    assert.deepEqual(
      actions.map((action) => action.label),
      ["Cancel turn", "Decline", "Approve once"],
      "every ask offers at least these three"
    );
    assert.equal(actions[0].act, "cancel_turn");
    assert.deepEqual(
      actions.slice(1).map((action) => action.supplied),
      [false, false],
      "the answers Panels had to supply say that they were supplied"
    );
    assert.equal(askIsGeneric(ask), true);
  }
}

{
  // An ask whose options are real but whose shape is not one Panels knows keeps them all,
  // and still gets the anchors that make it answerable.
  const ask = {
    options: [
      { option_id: "o-weird", label: "Do the unusual thing", option_kind: "vendor_special" }
    ]
  };
  const actions = askActions(ask);
  assert.deepEqual(
    actions.map((action) => (action.act === "cancel_turn" ? "cancel_turn" : action.optionId)),
    ["cancel_turn", "reject_once", "o-weird", "allow_once"]
  );
  assert.equal(actions[2].supplied, true, "the backend's own option is offered verbatim");
  assert.equal(askIsGeneric(ask), true);
}

{
  assert.equal(askPlaceholder({ title: "Run ls", detail: "in ~/Coding" }), "in ~/Coding");
  assert.equal(askPlaceholder({ title: "Run ls", detail: null }), "Run ls");
  assert.equal(askPlaceholder(null), "");
}

{
  // Commit-on-send: browsing changes nothing, and what was picked rides the next message.
  const current = { model: "opus", reasoningEffort: "high" };
  assert.deepEqual(
    armedChangeFor(current, { model: null, reasoningEffort: null }, "run_when_free"),
    {},
    "an untouched picker sends no change"
  );
  assert.deepEqual(
    armedChangeFor(current, { model: "opus", reasoningEffort: "high" }, "run_when_free"),
    {},
    "picking what it already runs on is not a change"
  );
  assert.deepEqual(
    armedChangeFor(current, { model: "sonnet", reasoningEffort: null }, "run_when_free"),
    { model_change: "sonnet" }
  );
  assert.deepEqual(
    armedChangeFor(current, { model: "sonnet", reasoningEffort: "low" }, "send_now"),
    { model_change: "sonnet", reasoning_effort_change: "low" }
  );
  assert.deepEqual(
    armedChangeFor(current, { model: "sonnet", reasoningEffort: "low" }, "steer"),
    {},
    "a steer joins a turn that is already running, so it carries no change"
  );
  assert.equal(hasArmedChange(current, { model: "sonnet", reasoningEffort: null }, "run_when_free"), true);
  assert.equal(hasArmedChange(current, { model: "sonnet", reasoningEffort: null }, "steer"), false);

  assert.deepEqual(
    sendBodyFor({
      text: "go",
      senderLabel: "owner",
      mode: "run_when_free",
      current,
      picked: { model: "sonnet", reasoningEffort: null }
    }),
    { text: "go", sender_label: "owner", mode: "run_when_free", model_change: "sonnet" }
  );
  assert.deepEqual(
    sendBodyFor({
      text: "go",
      senderLabel: "owner",
      mode: "send_now",
      current,
      picked: { model: null, reasoningEffort: null }
    }),
    { text: "go", sender_label: "owner", mode: "send_now" },
    "a message with nothing picked carries no change field at all"
  );
}

{
  // A started fate carries no note: the running turn shows as itself, and a note
  // repeating it would outlive the turn and go stale (found in dogfooding).
  assert.equal(fateSentence({ fate: "started" }), null);
  assert.equal(fateSentence({ fate: "queued", queue_position: 2 }), "queued · position 2");
  assert.equal(fateSentence({ fate: "injected" }), "steered into the running turn");
  assert.match(
    fateSentence({ fate: "refused", refusal_reason: "backend_cannot_steer" }),
    /^not delivered · .*running turn$/
  );
}

// --- the wire's own names --------------------------------------------------------------------

{
  assert.equal(wire.CONVERSATION2_BASE, "/api/conversation2");
  assert.equal(wire.COMMITTED_EVENT_STREAM_NAME, "conversation-event");
  assert.equal(wire.LIVE_FRAME_STREAM_NAME, "conversation-frame");
  assert.deepEqual(wire.CONVERSATION_BACKEND_KEYS, ["hermes", "codex", "claude"]);
}

await rm(directory, { recursive: true, force: true });

console.log("conversation2-wire.test.mjs: all assertions passed");
