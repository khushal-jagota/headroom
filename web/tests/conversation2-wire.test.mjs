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
    .outputText.replace(
      /from\s+["']\.\/(wire|feed|transcript|composer|outgoing)["']/g,
      'from "./$1.mjs"'
    );
}

for (const name of ["wire", "feed", "transcript", "composer", "outgoing"]) {
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
const {
  transcriptRows,
  threadItems,
  liveAskFrom,
  askDeadSentence,
  commandWithoutShellInvocation,
  elapsedSecondsSince,
  foldedWorkSentence,
  formatDuration,
  hiddenWorkSentence,
  lineShowsWholeDetail,
  millisecondsUntilNextSecond,
  promptLabelFor,
  readableDetail,
  refusalSentence,
  toolCallLine,
  toolGlyphKind,
  stoppedSentence,
  turnEndingSentence,
  turnFoldLabel,
  workedSentence,
  workingSentence,
  LIVE_COUNTER_TICK_MARGIN_MILLISECONDS,
  TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS,
  VISIBLE_RUNNING_WORK_ENTRIES
} = await import(join(directory, "transcript.mjs"));
const {
  askActions,
  askChoiceForDigit,
  askIsGeneric,
  askQuestionChoices,
  askShape,
  effortOptionsFor,
  modelDetail,
  preselectedValue,
  askPlaceholder,
  armedChangeFor,
  deliveryOptionsFor,
  deliveryModeIsOffered,
  fateSentence,
  hasArmedChange,
  sendBodyFor
} = await import(join(directory, "composer.mjs"));
const {
  mintOutgoingMessage,
  outgoingMessageNote,
  outgoingMessagesTheRecordHasNot,
  recallOutgoingMessages,
  rememberOutgoingMessages,
  senderMessageIdsInTheRecord
} = await import(join(directory, "outgoing.mjs"));

function event(sequence, kind, payload) {
  return { conversation_id: "c1", sequence, kind, payload, created_at: 1_700_000_000 };
}

const PROMPT = (sequence, text = "hello", mode = "run_when_free") =>
  event(sequence, "prompt", { text, sender_label: "owner", mode });
const AGENT = (sequence, text) => event(sequence, "agent_message", { text });
const TURN_ENDED = (sequence, ending = "completed", error_summary = null) =>
  event(sequence, "turn_ended", { ending, error_summary });
const TOOL_STARTED = (sequence, tool_call_id, title, tool_kind = "read") =>
  event(sequence, "tool_call_started", { tool_call_id, title, tool_kind, detail: null });
const TOOL_FINISHED = (sequence, tool_call_id, tool_call_status = "completed", detail = null) =>
  event(sequence, "tool_call_finished", { tool_call_id, tool_call_status, detail });
const PLAN = (sequence, entries) => event(sequence, "plan_updated", { entries });

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

  // What goes out is the message that was already drawn: same text, same id, same instant.
  const drawn = mintOutgoingMessage({
    text: "go",
    senderLabel: "owner",
    mode: "run_when_free",
    sentAtUnixMilliseconds: 1_700_000_000_123
  });
  assert.deepEqual(
    sendBodyFor({ message: drawn, current, picked: { model: "sonnet", reasoningEffort: null } }),
    {
      text: "go",
      sender_label: "owner",
      mode: "run_when_free",
      sender_message_id: drawn.messageId,
      sent_at_unix_milliseconds: 1_700_000_000_123,
      model_change: "sonnet"
    }
  );
  assert.deepEqual(
    sendBodyFor({
      message: { ...drawn, mode: "send_now" },
      current,
      picked: { model: null, reasoningEffort: null }
    }),
    {
      text: "go",
      sender_label: "owner",
      mode: "send_now",
      sender_message_id: drawn.messageId,
      sent_at_unix_milliseconds: 1_700_000_000_123
    },
    "a message with nothing picked carries no change field at all"
  );
}

// --- a message that has been sent and is not in the record yet -------------------------------

{
  const first = mintOutgoingMessage({ text: "one", senderLabel: "owner", mode: "run_when_free" });
  const second = mintOutgoingMessage({ text: "two", senderLabel: "owner", mode: "run_when_free" });
  assert.notEqual(first.messageId, second.messageId, "two messages are two messages");
  assert.equal(first.knownFate, "nothing_yet", "nothing is known about its fate yet");
  assert.equal(outgoingMessageNote(first), null, "and so it says nothing about itself");
  assert.ok(
    Math.abs(first.sentAtUnixMilliseconds - Date.now()) < 5_000,
    "the instant is when it was sent, in unix milliseconds"
  );

  // The two things a message can be known to be, and the words for each. Neither reads as
  // a message something is answering, because neither is.
  assert.equal(
    outgoingMessageNote({ ...first, knownFate: "waiting_for_the_agent" }),
    "waiting for the agent to be free"
  );
  assert.equal(
    outgoingMessageNote({ ...first, knownFate: "answer_never_came_back" }),
    "the server never said whether this arrived"
  );

  const sent = [first, second];
  assert.equal(
    outgoingMessagesTheRecordHasNot(sent, []),
    sent,
    "an empty record has caught up with nothing, and the same list is handed back"
  );
  const deliveredFirst = event(1, "prompt", {
    text: "one",
    sender_label: "owner",
    mode: "run_when_free",
    sender_message_id: first.messageId,
    sent_at_unix_milliseconds: first.sentAtUnixMilliseconds
  });
  assert.deepEqual(
    outgoingMessagesTheRecordHasNot(sent, [deliveredFirst]),
    [second],
    "the message the record now holds stops being drawn"
  );
  // The other two rows a sent message can become. Neither leaves a copy behind.
  assert.deepEqual(
    outgoingMessagesTheRecordHasNot(sent, [
      event(1, "prompt_delivery_refused", {
        text: "one",
        sender_label: "owner",
        mode: "run_when_free",
        refusal_reason: "backend_did_not_start",
        sender_message_id: first.messageId
      })
    ]),
    [second]
  );
  assert.deepEqual(
    outgoingMessagesTheRecordHasNot(sent, [
      event(2, "prompt_discarded", {
        text: "two",
        sender_label: "owner",
        sender_message_id: second.messageId
      })
    ]),
    [first]
  );
  assert.equal(
    outgoingMessagesTheRecordHasNot(sent, [PROMPT(1, "somebody else's message")]),
    sent,
    "a row nobody minted an id for belongs to somebody else and takes nothing away"
  );
  assert.deepEqual(
    senderMessageIdsInTheRecord([deliveredFirst, AGENT(2, "an answer")]),
    new Set([first.messageId])
  );
}

{
  // A message waiting for a busy agent lives in its tab and nowhere else — it has no row,
  // and the system knows only how many it holds, never their words. So the tab keeps it.
  const kept = new Map();
  globalThis.window = {
    sessionStorage: {
      getItem: (key) => (kept.has(key) ? kept.get(key) : null),
      setItem: (key, value) => kept.set(key, String(value)),
      removeItem: (key) => kept.delete(key)
    }
  };

  const held = {
    ...mintOutgoingMessage({ text: "held", senderLabel: "owner", mode: "run_when_free" }),
    knownFate: "waiting_for_the_agent"
  };
  const stillGoing = mintOutgoingMessage({
    text: "in flight",
    senderLabel: "owner",
    mode: "run_when_free"
  });
  rememberOutgoingMessages("c1", [held, stillGoing]);

  const recalled = recallOutgoingMessages("c1");
  assert.deepEqual(recalled[0], held, "what the system is holding comes back as it was");
  assert.equal(recalled[1].text, "in flight");
  assert.equal(
    recalled[1].knownFate,
    "answer_never_came_back",
    "a send the page went away in the middle of is one nobody ever heard the end of"
  );
  assert.deepEqual(recallOutgoingMessages("c2"), [], "one conversation's are not another's");

  kept.set("panels.conversation2.outgoing.c3", '[{"messageId":"x"},null,7,{"text":"no id"}]');
  assert.deepEqual(recallOutgoingMessages("c3"), [], "nothing that is not a message is drawn");
  kept.set("panels.conversation2.outgoing.c4", "not json at all");
  assert.deepEqual(recallOutgoingMessages("c4"), []);

  rememberOutgoingMessages("c1", []);
  assert.deepEqual(recallOutgoingMessages("c1"), [], "and holding nothing keeps nothing");
  delete globalThis.window;
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

// --- the space between a message and its reply -------------------------------------------------

{
  // While a turn runs, one line is what is happening; the rest wait behind a count.
  let feed = feedWithCommittedEvents(emptyConversationFeed(), [
    PROMPT(1, "do the thing"),
    TOOL_STARTED(2, "t1", "Read one"),
    TOOL_FINISHED(3, "t1"),
    TOOL_STARTED(4, "t2", "Read two"),
    TOOL_FINISHED(5, "t2"),
    TOOL_STARTED(6, "t3", "Read three")
  ]);
  const items = threadItems(transcriptRows(feed));
  const groups = items.filter((item) => item.kind === "work_group");
  assert.equal(groups.length, 1, "an unbroken run of tool calls is one group");
  assert.equal(groups[0].entries.length, 3);
  assert.equal(VISIBLE_RUNNING_WORK_ENTRIES, 1);
  assert.equal(hiddenWorkSentence(2), "+2 previous tool calls");
  assert.equal(hiddenWorkSentence(1), "+1 previous tool call");
  const anchor = items.find((item) => item.kind === "turn");
  assert.equal(anchor.settled, false);
  assert.equal(anchor.toolCallCount, 3);
  assert.deepEqual(
    items.filter((item) => item.kind === "row").map((item) => item.row.kind),
    ["prompt"]
  );
}

{
  // When the turn settles the whole log folds, and the fold says how long it took.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    { ...PROMPT(1, "do the thing"), created_at: 1_000 },
    { ...TOOL_STARTED(2, "t1", "Read one"), created_at: 1_002 },
    { ...TOOL_FINISHED(3, "t1"), created_at: 1_003 },
    { ...AGENT(4, "here you go"), created_at: 1_011 },
    { ...TURN_ENDED(5), created_at: 1_012 }
  ]);
  const items = threadItems(transcriptRows(feed));
  const anchor = items.find((item) => item.kind === "turn");
  assert.equal(anchor.settled, true);
  assert.equal(anchor.durationSeconds, 12, "start of the turn to its ending, in whole seconds");
  assert.equal(workedSentence(anchor.durationSeconds), "Worked for 12s");
  // The head is at the turn's start and the run stays where it happened.
  assert.deepEqual(
    items.map((item) =>
      item.kind === "turn" ? "turn" : item.kind === "work_group" ? "work" : item.row.kind
    ),
    ["prompt", "turn", "work", "agent_message", "turn_ended"]
  );
  assert.equal(
    items.find((item) => item.kind === "work_group").settled,
    true,
    "and it goes behind the turn's fold when the turn ends"
  );
}

{
  // A settled turn reads as one paragraph you can open. What the agent said on the way to
  // it is part of the work rather than part of the answer, so it goes behind the same fold
  // its tool calls go behind, and the last thing the turn said stays standing.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    { ...PROMPT(1, "do the thing"), created_at: 1_000 },
    { ...AGENT(2, "let me look"), created_at: 1_001 },
    { ...TOOL_STARTED(3, "t1", "Read one"), created_at: 1_002 },
    { ...TOOL_FINISHED(4, "t1"), created_at: 1_003 },
    { ...AGENT(5, "still going"), created_at: 1_004 },
    { ...AGENT(6, "here you go"), created_at: 1_011 },
    { ...TURN_ENDED(7), created_at: 1_012 }
  ]);
  const items = threadItems(transcriptRows(feed));
  const anchor = items.find((item) => item.kind === "turn");
  assert.equal(anchor.foldedMessageCount, 2);
  assert.equal(anchor.toolCallCount, 1);

  const behind = items.filter((item) => item.kind === "row" && item.behindTheFoldOf !== null);
  assert.deepEqual(behind.map((item) => item.row.text), ["let me look", "still going"]);
  assert.ok(
    behind.every((item) => item.behindTheFoldOf === anchor.turnKey),
    "and they go behind the fold of the turn that said them"
  );

  const standing = items.filter((item) => item.kind === "row" && item.behindTheFoldOf === null);
  assert.deepEqual(standing.map((item) => item.row.kind), ["prompt", "agent_message", "turn_ended"]);
  assert.equal(standing[1].row.text, "here you go", "the last thing it said is the answer");

  // Opened, everything is where it happened rather than gathered up at the end.
  assert.deepEqual(
    items.map((item) =>
      item.kind === "turn" ? "turn" : item.kind === "work_group" ? "work" : item.row.kind
    ),
    ["prompt", "turn", "agent_message", "work", "agent_message", "agent_message", "turn_ended"]
  );
}

{
  // A running turn folds nothing. The collapse happens on settling and only then.
  const items = threadItems(
    transcriptRows(
      feedWithCommittedEvents(emptyConversationFeed(), [
        PROMPT(1, "go"),
        AGENT(2, "one"),
        AGENT(3, "two")
      ])
    )
  );
  assert.equal(items.find((item) => item.kind === "turn").foldedMessageCount, 0);
  assert.deepEqual(
    items.filter((item) => item.kind === "row").map((item) => item.behindTheFoldOf),
    [null, null, null]
  );
}

{
  // A turn that never said anything has no answer to keep, so nothing is kept and the fold
  // holds all of it. The two things that never fold still stand: the person's own message,
  // and the turn's ending — an interrupted turn has to be able to say it was interrupted.
  const silent = threadItems(
    transcriptRows(
      feedWithCommittedEvents(emptyConversationFeed(), [
        PROMPT(1, "go"),
        TOOL_STARTED(2, "t1", "Read one"),
        TOOL_FINISHED(3, "t1"),
        TURN_ENDED(4, "interrupted")
      ])
    )
  );
  const silentAnchor = silent.find((item) => item.kind === "turn");
  assert.equal(silentAnchor.foldedMessageCount, 0);
  assert.equal(silentAnchor.toolCallCount, 1);
  assert.deepEqual(
    silent.filter((item) => item.kind === "row").map((item) => item.row.kind),
    ["prompt", "turn_ended"]
  );

  // A turn stopped part way through talking keeps the last thing it managed to say. The
  // rule does not change with the ending: it is always "everything but the last of it".
  const cutOff = threadItems(
    transcriptRows(
      feedWithCommittedEvents(emptyConversationFeed(), [
        PROMPT(1, "go"),
        AGENT(2, "starting"),
        AGENT(3, "half way"),
        TURN_ENDED(4, "interrupted")
      ])
    )
  );
  assert.equal(cutOff.find((item) => item.kind === "turn").foldedMessageCount, 1);
  assert.equal(
    cutOff.filter((item) => item.kind === "row" && item.behindTheFoldOf === null)[1].row.text,
    "half way"
  );
}

{
  // What the person did never folds. A permission ask records a decision they were asked
  // to make, which is their side of the conversation rather than something the turn made.
  const asked = threadItems(
    transcriptRows(
      feedWithCommittedEvents(emptyConversationFeed(), [
        PROMPT(1, "go"),
        event(2, "permission_asked", {
          ask_id: "a1",
          title: "Run rm -rf",
          detail: null,
          options: []
        }),
        AGENT(3, "done"),
        TURN_ENDED(4)
      ])
    )
  );
  assert.equal(
    asked.find((item) => item.kind === "row" && item.row.kind === "permission_ask")
      .behindTheFoldOf,
    null
  );
}

{
  // The fold used to hold only tool calls and said so. It holds more now, and the label
  // counts each kind it holds rather than the one it used to.
  assert.equal(foldedWorkSentence(3, 2), "3 tool calls · 2 messages");
  assert.equal(foldedWorkSentence(3, 0), "3 tool calls", "a turn that only worked reads as it did");
  assert.equal(foldedWorkSentence(0, 1), "1 message");
  assert.equal(foldedWorkSentence(1, 1), "1 tool call · 1 message");
  assert.equal(foldedWorkSentence(0, 0), "", "and a fold holding nothing counts nothing");
}

{
  // The record keeps whole seconds, so a turn too short to measure claims no duration.
  assert.equal(workedSentence(0), "Worked");
  assert.equal(workedSentence(null), "Worked");
  assert.equal(workedSentence(1), "Worked for 1s");
  // The boundaries T3 formats around, at the precision our rows actually have.
  assert.equal(formatDuration(59), "59s");
  assert.equal(formatDuration(60), "1m");
  assert.equal(formatDuration(61), "1m 1s");
  assert.equal(formatDuration(120), "2m");
  assert.equal(formatDuration(80), "1m 20s");
  assert.equal(formatDuration(3_600), "60m", "no hours unit, as T3 has none");
  assert.equal(workingSentence(12), "Working for 12s");
  assert.equal(workingSentence(null), "Working");

  // Only the newest turn takes the stopped wording; further back the line already said it.
  assert.equal(
    turnFoldLabel({ durationSeconds: 12, ending: "interrupted", isLatest: true }),
    "You stopped after 12s"
  );
  assert.equal(
    turnFoldLabel({ durationSeconds: null, ending: "interrupted", isLatest: true }),
    "You stopped this response"
  );
  assert.equal(
    turnFoldLabel({ durationSeconds: 12, ending: "interrupted", isLatest: false }),
    "Worked for 12s"
  );
  assert.equal(
    turnFoldLabel({ durationSeconds: 12, ending: "completed", isLatest: true }),
    "Worked for 12s"
  );
}

{
  // The live counter turns over on the turn's own second rather than the wall clock's:
  // one subtraction of the start instant from now, floored once.
  const begun = 1_000_400;
  assert.equal(elapsedSecondsSince(begun, begun), 0);
  assert.equal(elapsedSecondsSince(begun, begun + 999), 0);
  assert.equal(elapsedSecondsSince(begun, begun + 1_000), 1);
  assert.equal(
    elapsedSecondsSince(begun, 1_001_000),
    0,
    "the wall clock crossing a second is not this turn crossing one"
  );
  assert.equal(
    elapsedSecondsSince(begun, begun - 5_000),
    0,
    "a reader whose clock is behind the sender's counts nothing rather than counting down"
  );

  // Every wait is measured from the start instant, so the ticks land on its seconds.
  assert.equal(
    millisecondsUntilNextSecond(begun, begun),
    1_000 + LIVE_COUNTER_TICK_MARGIN_MILLISECONDS
  );
  assert.equal(
    millisecondsUntilNextSecond(begun, begun + 300),
    700 + LIVE_COUNTER_TICK_MARGIN_MILLISECONDS
  );
  assert.equal(
    millisecondsUntilNextSecond(begun, begun + 12_300),
    700 + LIVE_COUNTER_TICK_MARGIN_MILLISECONDS
  );
  assert.equal(
    millisecondsUntilNextSecond(begun, begun - 4_500),
    500 + LIVE_COUNTER_TICK_MARGIN_MILLISECONDS
  );

  // Run the schedule the way the head runs it, against a timer that never fires exactly
  // when it was asked to. Every second is said once and in order: this is the fault —
  // the number repeating and then skipping one — proved gone.
  let now = begun;
  const said = [];
  for (const lateness of [3, -4, 11, 0, -9, 7, 2, -1, 5]) {
    said.push(elapsedSecondsSince(begun, now));
    now += millisecondsUntilNextSecond(begun, now) + lateness;
  }
  assert.deepEqual(said, [0, 1, 2, 3, 4, 5, 6, 7, 8]);

  // A tab that was in the background comes back to the right number rather than to the
  // number of ticks it managed to fire, and goes straight back onto the turn's second.
  assert.equal(elapsedSecondsSince(begun, begun + 65_000), 65);
  assert.equal(
    millisecondsUntilNextSecond(begun, begun + 65_000),
    1_000 + LIVE_COUNTER_TICK_MARGIN_MILLISECONDS
  );

  // The running counter and the settled fold say a length the same way.
  assert.equal(workingSentence(elapsedSecondsSince(begun, begun + 80_000)), "Working for 1m 20s");
  assert.equal(workedSentence(80), "Worked for 1m 20s");
}

{
  // The instant the person pressed send is counted from wherever the record has it.
  const measured = event(1, "prompt", {
    text: "go",
    sender_label: "owner",
    mode: "run_when_free",
    sent_at_unix_milliseconds: 1_700_000_000_400
  });
  const anchor = threadItems(
    transcriptRows(feedWithCommittedEvent(emptyConversationFeed(), measured))
  ).find((item) => item.kind === "turn");
  assert.equal(anchor.startedAtUnixMilliseconds, 1_700_000_000_400);

  // The minted instant is somebody else's clock, and the row is a second opinion about
  // when it happened. An instant that cannot be reconciled with its own row is not
  // believed, and the whole second the row was written in is counted from instead.
  function anchorOf(sentAt) {
    const payload = { text: "go", sender_label: "owner", mode: "run_when_free" };
    return threadItems(
      transcriptRows(
        feedWithCommittedEvent(
          emptyConversationFeed(),
          event(1, "prompt", sentAt === null ? payload : { ...payload, sent_at_unix_milliseconds: sentAt })
        )
      )
    ).find((item) => item.kind === "turn").startedAtUnixMilliseconds;
  }
  const written = 1_700_000_000_000;

  // A sender that put seconds where milliseconds belong would open the counter on a
  // number in the tens of thousands of minutes.
  assert.equal(anchorOf(1_700_000_000), written, "seconds in a milliseconds field are refused");

  // A sender running ahead of the record would freeze the counter on "Working for 0s"
  // for as long as it is ahead.
  assert.equal(anchorOf(written + 90_000), written, "a clock a minute and a half ahead is refused");
  assert.equal(
    anchorOf(written + 1_500),
    written + 1_500,
    "but the row's own rounding and ordinary skew are not a wrong clock"
  );

  // A send really can precede its row by a long way: the row is written after the backend
  // has taken the text, so a cold start sits in that gap — and recovering exactly that
  // wait is what the instant is for.
  assert.equal(anchorOf(written - 20_000), written - 20_000, "a slow delivery is believed");
  assert.equal(anchorOf(written - 600_000), written, "ten minutes earlier is not this send");
  assert.equal(anchorOf(null), written, "and a sender that minted nothing counts from the row");

  // A steer joins the turn already running, so it never moves where the counting began.
  const steered = feedWithCommittedEvents(emptyConversationFeed(), [
    measured,
    event(2, "prompt", {
      text: "also this",
      sender_label: "owner",
      mode: "steer",
      sent_at_unix_milliseconds: 1_700_000_009_100
    })
  ]);
  assert.equal(
    threadItems(transcriptRows(steered)).find((item) => item.kind === "turn")
      .startedAtUnixMilliseconds,
    1_700_000_000_400
  );
}

{
  // Two turns keep their own work and their own durations.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    { ...PROMPT(1), created_at: 100 },
    { ...TOOL_STARTED(2, "t1", "One"), created_at: 101 },
    { ...TURN_ENDED(3), created_at: 105 },
    { ...PROMPT(4, "again"), created_at: 200 },
    { ...TOOL_STARTED(5, "t2", "Two"), created_at: 201 }
  ]);
  const turns = threadItems(transcriptRows(feed)).filter((item) => item.kind === "turn");
  assert.equal(turns.length, 2);
  assert.deepEqual(turns.map((item) => item.settled), [true, false]);
  assert.equal(turns[0].durationSeconds, 5);
  assert.equal(turns[1].durationSeconds, null, "a turn still running has no length yet");
  assert.deepEqual(turns.map((item) => item.isLatest), [false, true]);
}

{
  // A turn gets its place the moment it starts, before there is anything to put in it —
  // the wait before the first tool call is exactly when a person needs to see it.
  const justStarted = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(1, "go"));
  const items = threadItems(transcriptRows(justStarted));
  const anchor = items.find((item) => item.kind === "turn");
  assert.ok(anchor, "a running turn has a head before there is any work to put under it");
  assert.equal(anchor.toolCallCount, 0);
  assert.equal(anchor.settled, false);
  assert.equal(
    anchor.startedAtUnixMilliseconds,
    justStarted.events[0].created_at * 1_000,
    "it counts from the prompt, and a row with no measured instant contributes its second"
  );
  assert.deepEqual(
    items.map((item) => (item.kind === "turn" ? "turn" : item.row.kind)),
    ["prompt", "turn"],
    "and it sits directly under the message that started it"
  );

  // A steer joins the turn already running rather than opening a second head.
  const steered = feedWithCommittedEvent(justStarted, PROMPT(2, "also this", "steer"));
  assert.equal(
    threadItems(transcriptRows(steered)).filter((item) => item.kind === "turn").length,
    1
  );
}

{
  // A turn that stopped without an ending claims no length, and folds like any other.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    { ...PROMPT(1), created_at: 100 },
    { ...TOOL_STARTED(2, "t1", "One"), created_at: 101 }
  ]);
  const stoppedRows = transcriptRows(feed, { turnStoppedWithoutAnEnding: true });
  const anchor = threadItems(stoppedRows).find((item) => item.kind === "turn");
  assert.equal(anchor.settled, true, "a stopped turn is not still running");
  assert.equal(anchor.stopped, true);
  assert.equal(anchor.durationSeconds, null, "nobody saw the end, so nobody can time it");
  assert.equal(workedSentence(anchor.durationSeconds), "Worked");
}

{
  // A sign of life is a sign of life whatever it carries — and a frame carrying nothing
  // is the only thing private reasoning is ever allowed to become.
  let feed = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(1));
  assert.equal(feed.livenessPulse, 0);
  feed = feedWithLiveFrame(feed, { frame: "model_thinking" });
  assert.equal(feed.livenessPulse, 1);
  assert.equal(feed.streamingAgentText, "", "thinking never becomes content");
  assert.deepEqual(feed.toolCallProgress, {});
  assert.equal(feed.events.length, 1, "and it is never a row");
  feed = feedWithLiveFrame(feed, { frame: "model_thinking" });
  feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "hi" });
  feed = feedWithLiveFrame(feed, {
    frame: "tool_call_progress",
    tool_call_id: "t1",
    detail: "…"
  });
  assert.equal(feed.livenessPulse, 4, "every kind of frame counts as life");

  // A frame after the turn is over is a ghost, and a ghost is not a sign of life.
  const ended = feedWithCommittedEvent(feed, TURN_ENDED(2));
  const haunted = feedWithLiveFrame(ended, { frame: "model_thinking" });
  assert.equal(haunted.livenessPulse, ended.livenessPulse);

  // A frame this browser has not been taught yet changes nothing at all.
  let running = feedWithCommittedEvent(emptyConversationFeed(), PROMPT(1));
  const puzzled = feedWithLiveFrame(running, { frame: "something_new", payload: 1 });
  assert.deepEqual(puzzled, running, "an unknown frame is ignored rather than guessed at");
}

{
  // A plan is read as a strip, never as a line of the thread.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    PROMPT(1, "plan it"),
    PLAN(2, [
      { text: "read the code", status: "completed" },
      { text: "write it", status: "in_progress" },
      { text: "test it", status: "pending" }
    ])
  ]);
  const rows = transcriptRows(feed);
  assert.equal(
    rows.filter((row) => row.kind === "plan_updated").length,
    1,
    "the row is in the record"
  );
  const items = threadItems(rows);
  assert.deepEqual(
    items.map((item) => (item.kind === "turn" ? "turn" : item.row.kind)),
    ["prompt", "turn"],
    "and it is not a line of its own — the strip is its rendering"
  );
  const anchor = items.find((item) => item.kind === "turn");
  assert.equal(anchor.plan.length, 3);
  assert.equal(anchor.plan[1].status, "in_progress");
}

{
  // Each plan row is the whole plan, so the newest replaces the last outright.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    PROMPT(1),
    PLAN(2, [
      { text: "one", status: "in_progress" },
      { text: "two", status: "pending" }
    ]),
    PLAN(3, [{ text: "one", status: "completed" }])
  ]);
  const plans = threadItems(transcriptRows(feed))
    .filter((item) => item.kind === "turn" && item.plan !== null)
    .map((item) => item.plan);
  assert.equal(plans.length, 1, "one conversation, one strip");
  assert.deepEqual(plans[0], [{ text: "one", status: "completed" }], "replaced, never merged");
}

{
  // A plan outlives the turn that made it — it is fed by rows, so a reload still has it.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    { ...PROMPT(1), created_at: 100 },
    PLAN(2, [{ text: "one", status: "completed" }]),
    { ...TURN_ENDED(3), created_at: 104 }
  ]);
  const settledAnchor = threadItems(transcriptRows(feed)).find((item) => item.kind === "turn");
  assert.equal(settledAnchor.settled, true);
  assert.equal(settledAnchor.plan.length, 1, "the plan is still there after the turn ended");

  // A later turn's plan becomes the plan, and the earlier anchor stops showing one.
  const later = feedWithCommittedEvents(feed, [
    { ...PROMPT(4, "again"), created_at: 200 },
    PLAN(5, [{ text: "two", status: "in_progress" }])
  ]);
  const anchors = threadItems(transcriptRows(later)).filter((item) => item.kind === "turn");
  assert.equal(anchors.length, 2);
  assert.equal(anchors[0].plan, null, "the plan it stated is history now");
  assert.deepEqual(anchors[1].plan, [{ text: "two", status: "in_progress" }]);
}

{
  // A conversation that never planned has no plan anywhere.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [PROMPT(1), AGENT(2, "done")]);
  for (const item of threadItems(transcriptRows(feed))) {
    if (item.kind === "turn") assert.equal(item.plan, null);
  }
}

{
  // FINDING 10: work between two pieces of commentary stays between them. Each unbroken
  // run is its own group, sitting where it happened.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    PROMPT(1, "go"),
    TOOL_STARTED(2, "t1", "One"),
    TOOL_STARTED(3, "t2", "Two"),
    AGENT(4, "here is what I found"),
    TOOL_STARTED(5, "t3", "Three"),
    AGENT(6, "and the answer")
  ]);
  const items = threadItems(transcriptRows(feed));
  assert.deepEqual(
    items.map((item) =>
      item.kind === "turn" ? "turn" : item.kind === "work_group" ? "work" : item.row.kind
    ),
    ["prompt", "turn", "work", "agent_message", "work", "agent_message"],
    "two batches, each between the commentary it belongs to"
  );
  const groups = items.filter((item) => item.kind === "work_group");
  assert.deepEqual(groups.map((group) => group.entries.length), [2, 1]);
  assert.deepEqual(
    groups.map((group) => group.entries.map((entry) => entry.title)),
    [["One", "Two"], ["Three"]]
  );
  // Every group belongs to the turn that made it, so one fold covers them all.
  const anchor = items.find((item) => item.kind === "turn");
  assert.equal(anchor.toolCallCount, 3, "the count spans the whole turn, not one batch");
  assert.deepEqual(new Set(groups.map((group) => group.turnKey)), new Set([anchor.turnKey]));
}

{
  // On settle every group goes behind the one fold; a run of one hides nothing.
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    { ...PROMPT(1), created_at: 10 },
    TOOL_STARTED(2, "t1", "Only one"),
    AGENT(3, "done"),
    { ...TURN_ENDED(4), created_at: 13 }
  ]);
  const items = threadItems(transcriptRows(feed));
  const groups = items.filter((item) => item.kind === "work_group");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].entries.length, 1, "a run of one has nothing to hide");
  assert.equal(groups[0].settled, true, "and it is behind the turn's fold now");
  const anchor = items.find((item) => item.kind === "turn");
  assert.equal(turnFoldLabel(anchor), "Worked for 3s");
}

{
  // FINDING 11: a selector shows the concrete value in force, and "default" is never one.
  assert.equal(preselectedValue("sonnet", "opus"), "sonnet", "the record wins when it has one");
  assert.equal(preselectedValue(null, "opus"), "opus", "otherwise what the backend runs");
  assert.equal(preselectedValue(null, null), null, "and a backend naming none stays empty");
  assert.equal(preselectedValue(null, undefined), null, "a catalog yet to say is absence");

  // Untouched sends nothing — the backend already runs that value. A pick arms as ever.
  const current = { model: null, reasoningEffort: null };
  assert.deepEqual(
    armedChangeFor(current, { model: null, reasoningEffort: null }, "run_when_free"),
    {},
    "showing the backend's own value is not choosing it"
  );
  assert.deepEqual(
    armedChangeFor(current, { model: "opus", reasoningEffort: null }, "run_when_free"),
    { model_change: "opus" },
    "picking it is"
  );
}

{
  // Effort belongs to the model that will run. A model that names its own list is
  // believed — including when that list is empty, because that is an answer.
  const models = [
    { model_id: "opus", reasoning_effort_options: ["low", "high"] },
    { model_id: "haiku", reasoning_effort_options: [] },
    { model_id: "quiet" }
  ];
  const backendOptions = ["low", "medium", "high"];
  assert.deepEqual(effortOptionsFor(models, "opus", backendOptions), ["low", "high"]);
  assert.deepEqual(
    effortOptionsFor(models, "haiku", backendOptions),
    [],
    "a model that takes no effort gets no effort control at all"
  );
  assert.deepEqual(
    effortOptionsFor(models, "quiet", backendOptions),
    backendOptions,
    "a model naming nothing falls back to the backend's own list"
  );
  assert.deepEqual(effortOptionsFor(models, null, backendOptions), backendOptions);
  assert.deepEqual(effortOptionsFor(models, "unknown", backendOptions), backendOptions);
  assert.deepEqual(effortOptionsFor([], "opus", []), [], "hermes offers none, so none exist");
}

{
  // A payload is laid out to be read; prose is left exactly as it was written.
  assert.equal(readableDetail('{"a":1,"b":[2,3]}'), '{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}');
  assert.equal(readableDetail("ls -la /tmp"), "ls -la /tmp");
  assert.equal(readableDetail("{not actually json"), "{not actually json");
  assert.equal(readableDetail(""), null);
  assert.equal(readableDetail(null), null);
  assert.equal(readableDetail(undefined), null);
}

{
  // Your own messages are not labelled as yours; everyone else's are.
  assert.equal(promptLabelFor("owner", "owner"), null);
  assert.equal(promptLabelFor("the automatic loop", "owner"), "the automatic loop");
  assert.equal(promptLabelFor("owner", null), "owner", "with no pane label nothing is suppressed");
}

{
  // What a tool did, read from what it is called — the backends do not agree on this field.
  assert.equal(toolGlyphKind("read"), "read", "the protocol's own kinds pass straight through");
  assert.equal(toolGlyphKind("switch_mode"), "switch_mode");
  assert.equal(toolGlyphKind("Bash"), "execute", "a tool name is read by what the word means");
  assert.equal(toolGlyphKind("Grep"), "search");
  assert.equal(toolGlyphKind("WebFetch"), "fetch");
  assert.equal(toolGlyphKind("NotebookEdit"), "edit");
  assert.equal(toolGlyphKind("Read"), "read");
  assert.equal(toolGlyphKind("something nobody has heard of"), "other");
}

{
  // A tool call's line says what happened and which call it was. Every row below is a
  // payload one of the three backends has actually written into a conversation.

  // claude titles every call with the tool's name and puts the call's own arguments in
  // the detail as one JSON object, which is why its rows used to read "Bash" and nothing
  // else — an object is never the one short line the pane was willing to show.
  assert.deepEqual(
    toolCallLine({
      title: "Bash",
      toolKind: "Bash",
      startedDetail:
        '{"command": "ls -la /Users/khushaljagota/Coding", "description": "List project directory"}',
      detail: "total 0\ndrwxr-xr-x  12 khushaljagota  staff   384 25 Jul 21:38 ."
    }),
    { title: "Ran command", summary: "ls -la /Users/khushaljagota/Coding" },
    "the call is read from what it was asked to do, not from what it gave back"
  );
  assert.deepEqual(
    toolCallLine({
      title: "Read",
      toolKind: "Read",
      startedDetail: '{"file_path": "/Users/khushaljagota/Coding/planning-v2/AGENTS.md"}',
      detail: null
    }),
    { title: "Read file", summary: "/Users/khushaljagota/Coding/planning-v2/AGENTS.md" }
  );
  assert.deepEqual(
    toolCallLine({
      title: "Write",
      toolKind: "Write",
      startedDetail:
        '{"content": "# Scratch\\n\\nThrowaway.\\n", "file_path": "/Users/khushaljagota/Coding/scratch.md"}',
      detail: null
    }),
    { title: "Edited file", summary: "/Users/khushaljagota/Coding/scratch.md" },
    "the path a write was given, never the thing it was given to write"
  );

  // A call whose arguments name no subject keeps the tool's name: "TaskUpdate" with
  // nothing beside it still says more than "Edited" with nothing beside it.
  assert.deepEqual(
    toolCallLine({
      title: "TaskUpdate",
      toolKind: "TaskUpdate",
      startedDetail: '{"status": "completed", "taskId": "1"}',
      detail: null
    }),
    { title: "TaskUpdate", summary: null }
  );
  assert.deepEqual(
    toolCallLine({
      title: "AskUserQuestion",
      toolKind: "AskUserQuestion",
      startedDetail: '{"questions": [{"header": "Colour", "question": "Which colour?"}]}',
      detail: null
    }),
    { title: "AskUserQuestion", summary: null },
    "and nothing outside the named subjects is fished out of a call's arguments"
  );

  // codex runs everything through a login shell and titles the row with the invocation.
  assert.deepEqual(
    toolCallLine({
      title: '/bin/zsh -lc "pwd && rg --files | head -25"',
      toolKind: "execute",
      startedDetail: "/Users/khushaljagota/Coding",
      detail: "/Users/khushaljagota/Coding"
    }),
    { title: "Ran command", summary: "pwd && rg --files | head -25" },
    "the shell is how the command was carried; the command is what was done"
  );

  // hermes writes a title that already describes the call, so it stands as written — and
  // the detail that only says it again is dropped rather than said twice.
  assert.deepEqual(
    toolCallLine({
      title: "terminal: ls web/src/lib/conversation2",
      toolKind: "execute",
      startedDetail: "$ ls web/src/lib/conversation2",
      detail: "composer.ts\nfeed.ts\ntranscript.ts\nwire.ts"
    }),
    { title: "Ran command", summary: "ls web/src/lib/conversation2" }
  );
  assert.deepEqual(
    toolCallLine({
      title: "search: wire.ts",
      toolKind: "search",
      startedDetail: "Searching for 'wire.ts' (files) in /Users/khushaljagota/Coding",
      detail: null
    }),
    { title: "Searched files", summary: "wire.ts" },
    "hermes states the kind in front of the value, and the header is where the kind goes"
  );
  assert.deepEqual(
    toolCallLine({
      title: "read: /Users/khushaljagota/Coding/web/src/lib/conversation2/wire.ts",
      toolKind: "read",
      startedDetail: null,
      detail: "line one\nline two\nline three"
    }),
    {
      title: "Read file",
      summary: "/Users/khushaljagota/Coding/web/src/lib/conversation2/wire.ts"
    },
    "several lines is output, and output belongs behind the row rather than on it"
  );

  // A row is always one line: the summary is cut short and says that it was.
  const wordy = toolCallLine({
    title: "Bash",
    toolKind: "Bash",
    startedDetail: JSON.stringify({ command: `echo ${"long ".repeat(40)}` }),
    detail: null
  });
  assert.ok(wordy.summary.length <= TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS);
  assert.ok(wordy.summary.endsWith("…"), "and it says that it was cut");
  assert.equal(
    toolCallLine({
      title: "Bash",
      toolKind: "Bash",
      startedDetail: JSON.stringify({ command: "cat <<'EOF' > note.txt\nhello\nEOF" }),
      detail: null
    }).summary,
    "cat <<'EOF' > note.txt",
    "a command written over several lines still gives one line to read"
  );

  // The shell invocations a command arrives wrapped in, and the quoting they carry it in.
  assert.equal(commandWithoutShellInvocation('bash -c "npm test"'), "npm test");
  assert.equal(commandWithoutShellInvocation("sh -c 'npm test'"), "npm test");
  assert.equal(commandWithoutShellInvocation("/bin/zsh -lc 'npm test'"), "npm test");
  assert.equal(commandWithoutShellInvocation("cmd /c dir"), "dir");
  assert.equal(commandWithoutShellInvocation('pwsh -Command "Get-ChildItem"'), "Get-ChildItem");
  assert.equal(
    commandWithoutShellInvocation('bash -c "echo \\"hi\\""'),
    'echo "hi"',
    "the escapes belong to the quoting rather than to the command"
  );
  assert.equal(commandWithoutShellInvocation("npm test"), "npm test", "and a bare command is left alone");
  assert.equal(commandWithoutShellInvocation("bashful -c thing"), "bashful -c thing");

  // A summary is dropped when the title already says it — as words, in order and
  // together. Letters alone would be simpler and would eat the summary alive.
  assert.deepEqual(
    toolCallLine({
      title: "Grep",
      toolKind: "Grep",
      startedDetail: '{"pattern": "arch"}',
      detail: null
    }),
    { title: "Searched files", summary: "arch" },
    "a pattern spelled out of the letters of its own verb still says which call this was"
  );
  assert.deepEqual(
    toolCallLine({
      title: "Grep",
      toolKind: "Grep",
      startedDetail: '{"pattern": "search"}',
      detail: null
    }),
    { title: "Searched files", summary: "search" },
    "and so does one that is a whole word of the verb — searching for the word search"
  );
  assert.equal(
    lineShowsWholeDetail({ title: "Searched files", summary: "arch" }, "arch"),
    true,
    "a detail the line already shows in full is still a detail the line shows in full"
  );
  assert.equal(
    lineShowsWholeDetail({ title: "Searched files", summary: null }, "arch"),
    false,
    "but a row whose line does not say it keeps the way to open it"
  );

  // Opening a row that would only repeat what its line already says is an affordance
  // that does nothing, so a row like that does not offer one.
  const echoed = toolCallLine({
    title: "Bash",
    toolKind: "Bash",
    startedDetail: null,
    detail: "ls -la /tmp"
  });
  assert.equal(echoed.summary, "ls -la /tmp");
  assert.equal(lineShowsWholeDetail(echoed, "ls -la /tmp"), true);
  assert.equal(lineShowsWholeDetail(echoed, "ls -la /tmp\nfile one\nfile two"), false);
}

// --- the three shapes of ask -------------------------------------------------------------------

{
  const permission = {
    options: [
      { option_id: "o-reject", label: "Decline", option_kind: "reject_once" },
      { option_id: "o-allow", label: "Approve once", option_kind: "allow_once" }
    ]
  };
  const question = {
    options: [
      { option_id: "q-a", label: "Rewrite it", option_kind: "choice" },
      { option_id: "q-b", label: "Leave it", option_kind: "choice" }
    ]
  };
  assert.equal(askShape(permission), "permission");
  assert.equal(askShape(question), "question", "options that are choices are not approvals");
  assert.equal(askShape({ options: [] }), "shapeless");
  assert.equal(askShape(null), "shapeless");

  // A question is answered by picking, and the first nine picks have a number key.
  const many = {
    options: Array.from({ length: 11 }, (_unused, index) => ({
      option_id: `q${index}`,
      label: `Choice ${index}`,
      option_kind: "choice"
    }))
  };
  const choices = askQuestionChoices(many);
  assert.equal(choices.length, 11, "nothing is dropped for want of a key");
  assert.deepEqual(choices.slice(0, 9).map((choice) => choice.shortcutDigit), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(choices[9].shortcutDigit, null);
  assert.equal(askChoiceForDigit(many, 3).optionId, "q2");
  assert.equal(askChoiceForDigit(question, 3), null, "a key past the choices picks nothing");
  assert.equal(askChoiceForDigit(question, 0), null);
  assert.equal(askChoiceForDigit(question, Number.NaN), null);
}

{
  // A placeholder is one line of grey text, so a payload never becomes one.
  assert.equal(askPlaceholder({ title: "Run ls", detail: "ls -la /tmp" }), "ls -la /tmp");
  assert.equal(
    askPlaceholder({ title: "Which way?", detail: '{"questions":[{"q":"a"}]}' }),
    "Which way?",
    "structured detail belongs in the card, not in a placeholder"
  );
  assert.equal(askPlaceholder({ title: "Run it", detail: "line one\nline two" }), "Run it");
  assert.equal(askPlaceholder({ title: "Run it", detail: "x".repeat(200) }), "Run it");
  assert.equal(askPlaceholder({ title: "Run ls", detail: null }), "Run ls");
}

{
  const models = [
    { model_id: "opus", display_name: "Opus 5", detail: "opus → claude-opus-5" },
    { model_id: "plain", display_name: "Plain" }
  ];
  assert.equal(modelDetail(models, "opus"), "opus → claude-opus-5");
  assert.equal(modelDetail(models, "plain"), null, "a catalog that offers none reads as absent");
  assert.equal(modelDetail(models, "unknown"), null);
  assert.equal(modelDetail(models, null), null);
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
