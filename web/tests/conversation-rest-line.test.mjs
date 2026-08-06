/** The one line above the composer at rest, asked directly.
 *
 * The line is a pure reading of the rows a reader already holds, so it is tested the way
 * the rest of the pane's decisions are: the module is transpiled and asked, rather than
 * rendered and scraped. What is being pinned down here is the priority order — a waiting
 * ask over a running turn over whatever happened last — and, underneath it, which rows
 * count as a thing that happened at all.
 *
 * transpileModule keeps extensionless specifiers, which Node ESM will not resolve, so the
 * surviving value imports are rewritten to .mjs before they are written.
 */

import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const compilerOptions = {
  module: ts.ModuleKind.ES2022,
  target: ts.ScriptTarget.ES2022,
  verbatimModuleSyntax: true
};

const directory = await mkdtemp(join(tmpdir(), "planner-rest-line-"));

async function transpile(sourcePath) {
  const source = await readFile(
    new URL(`../src/lib/conversation/${sourcePath}`, import.meta.url),
    "utf8"
  );
  return ts
    .transpileModule(source, { compilerOptions })
    .outputText.replace(/from\s+["']\.\/(wire|feed|transcript|restLine)["']/g, 'from "./$1.mjs"');
}

const transpiledModules = [
  ["wire.ts", "wire.mjs"],
  ["feed.ts", "feed.mjs"],
  ["transcript.ts", "transcript.mjs"],
  ["threadLayout/index.ts", "threadLayout.mjs"],
  ["conversationDetail.ts", "conversationDetail.mjs"],
  ["toolCallPresentation/index.ts", "toolCallPresentation.mjs"],
  ["taskProgress.ts", "taskProgress.mjs"],
  ["restLine.ts", "restLine.mjs"]
];

for (const [sourcePath, outputName] of transpiledModules) {
  let output = await transpile(sourcePath);
  output = output
    .replace(/from\s+["']\.\.\/transcript["']/g, 'from "./transcript.mjs"')
    .replace(
      /from\s+["']\.\.\/conversationDetail["']/g,
      'from "./conversationDetail.mjs"'
    )
    .replace(/from\s+["']\.\.\/wire["']/g, 'from "./wire.mjs"')
    .replace(
      /from\s+["']\.\/threadLayout["']/g,
      'from "./threadLayout.mjs"'
    )
    .replace(
      /from\s+["']\.\/toolCallPresentation["']/g,
      'from "./toolCallPresentation.mjs"'
    )
    .replace(
      /from\s+["']\.\/conversationDetail["']/g,
      'from "./conversationDetail.mjs"'
    )
    .replace(
      /from\s+["']\.\/taskProgress["']/g,
      'from "./taskProgress.mjs"'
    );
  await writeFile(join(directory, outputName), output, "utf8");
}

const { emptyConversationFeed, feedWithCommittedEvents } = await import(
  join(directory, "feed.mjs")
);
const { transcriptRows } = await import(join(directory, "transcript.mjs"));
const { restLineFrom, REST_LINE_MAXIMUM_CHARACTERS } = await import(
  join(directory, "restLine.mjs")
);

// --- the rows a reader holds ------------------------------------------------------------------

let nextSequence = 0;
function row(kind, fields) {
  nextSequence += 1;
  return { key: `e${nextSequence}`, kind, sequence: nextSequence, createdAt: 1_000 + nextSequence, ...fields };
}

const SENT_AT = 1_700_000_000_000;

function prompt(text, senderLabel = "owner") {
  return row("prompt", {
    content: [{ piece: "text", text }],
    senderLabel,
    mode: "run_when_free",
    sentAtUnixMilliseconds: SENT_AT
  });
}

function agentMessage(text) {
  return row("agent_message", { content: [{ piece: "text", text }] });
}

function toolCall(title, toolKind, startedDetail = null, status = "completed") {
  nextSequence += 1;
  return {
    key: `e${nextSequence}`,
    kind: "tool_call",
    sequence: nextSequence,
    createdAt: 1_000 + nextSequence,
    toolCallId: `t${nextSequence}`,
    title,
    toolKind,
    detail: null,
    startedDetail,
    status,
    progress: null
  };
}

function permissionAsk(title, state = "live", detail = null) {
  return row("permission_ask", {
    askId: "a1",
    title,
    detail,
    options: [],
    state,
    deadReason: state === "dead" ? "turn_ended" : null,
    answeredOptionLabel: null
  });
}

function turnEnded(ending = "completed", errorSummary = null) {
  return row("turn_ended", { ending, errorSummary });
}

const PLAN = [
  { text: "read the code", status: "completed" },
  { text: "write it", status: "in_progress" },
  { text: "test it", status: "pending" }
];

// --- 1. an ask that is waiting outranks everything --------------------------------------------

{
  // With the conversation closed this is the only thing on the screen that could say a
  // request needs answering, so nothing the turn is doing gets in front of it.
  const line = restLineFrom(
    [prompt("go"), row("plan_updated", { entries: PLAN }), toolCall("Bash", "Bash", '{"command": "rm -rf /tmp/x"}', "running"), permissionAsk("Run rm -rf")],
    "owner"
  );
  assert.equal(line.waiting, true);
  assert.equal(line.text, "Run rm -rf", "the ask's own title is the line");
  assert.equal(line.who, null, "an ask speaks for itself");
  assert.equal(line.aside, null);
  assert.equal(line.taskProgress, null);
  assert.equal(
    line.workingSinceUnixMilliseconds,
    null,
    "counting seconds beside a question is not what the person is being asked"
  );
}

{
  // A plan without a step in progress does not replace the current turn fallback.
  const line = restLineFrom(
    [
      prompt("go"),
      row("plan_updated", { entries: [{ text: "later", status: "pending" }] }),
      toolCall("Read", "read")
    ],
    "owner"
  );
  assert.equal(line.text, "Read");
  assert.equal(line.taskProgress, null);
}

{
  // A backend that titled its ask with nothing still has to be answerable, so what it did
  // say is what the line says.
  const line = restLineFrom([prompt("go"), permissionAsk("", "live", "in ~/Coding")], "owner");
  assert.equal(line.waiting, true);
  assert.equal(line.text, "in ~/Coding");
}

{
  // An ask that has been answered, and one that died with its turn, are decisions that
  // are over. Neither leaves the line saying somebody is being waited on.
  for (const state of ["answered", "dead"]) {
    const line = restLineFrom(
      [prompt("go"), permissionAsk("Run rm -rf", state), agentMessage("done"), turnEnded()],
      "owner"
    );
    assert.equal(line.waiting, false, `an ${state} ask is not waiting on anybody`);
    assert.equal(line.text, "done");
  }
}

// --- 2. a turn that is running -----------------------------------------------------------------

{
  const line = restLineFrom(
    [
      prompt("go"),
      row("plan_updated", { entries: PLAN }),
      toolCall("Read", "read"),
      toolCall("Bash", "Bash", '{"command": "ls -la /tmp"}', "running")
    ],
    "owner"
  );
  assert.equal(line.text, "write it", "the current plan step replaces tool activity");
  assert.equal(line.who, null);
  assert.equal(line.aside, null);
  assert.equal(line.taskProgress.currentPosition, 2);
  assert.equal(line.taskProgress.completedCount, 1);
  assert.equal(line.waiting, false);
  assert.equal(
    line.workingSinceUnixMilliseconds,
    SENT_AT,
    "the instant the turn began, which is what the turn head counts from"
  );
}

{
  // A turn that has not called anything yet says the message that started it, so the line
  // is never empty while something is running.
  const line = restLineFrom([prompt("go and do the thing")], "owner");
  assert.equal(line.text, "go and do the thing");
  assert.equal(line.who, "you");
  assert.equal(line.workingSinceUnixMilliseconds, SENT_AT);
  assert.equal(line.aside, null, "no plan, nothing beside it");
  assert.equal(line.taskProgress, null);
}

{
  // Text still arriving is the newest thing the turn has produced.
  const line = restLineFrom(
    [prompt("go"), row("streaming_agent_message", { text: "here is what I found\nand more" })],
    "owner"
  );
  assert.equal(line.text, "here is what I found");
  assert.equal(line.who, null);
  assert.equal(line.workingSinceUnixMilliseconds, SENT_AT);
}

{
  // The turn before this one made tool calls; this one has not. A line showing that call
  // under a counter saying this turn is working would be a lie about what is happening.
  const line = restLineFrom(
    [
      prompt("first"),
      toolCall("Bash", "Bash", '{"command": "ls -la /tmp"}'),
      agentMessage("all done"),
      turnEnded(),
      prompt("second")
    ],
    "owner"
  );
  assert.equal(line.text, "second");
  assert.equal(line.who, "you");
  assert.equal(line.workingSinceUnixMilliseconds, SENT_AT);
}

{
  // A plan outlives the turn that stated it, and so does its progress beside the line.
  const line = restLineFrom(
    [
      prompt("first"),
      row("plan_updated", { entries: PLAN }),
      turnEnded(),
      prompt("second"),
      toolCall("Read", "read")
    ],
    "owner"
  );
  assert.equal(line.text, "write it");
  assert.equal(line.taskProgress.currentPosition, 2);
}

// --- 3. whatever happened last ------------------------------------------------------------------

{
  // The agent's answer, at its first line. Its own words need no name in front of them.
  const line = restLineFrom(
    [prompt("go"), agentMessage("Here is the summary\n\nand then the rest of it"), turnEnded()],
    "owner"
  );
  assert.deepEqual(line, {
    who: null,
    text: "Here is the summary",
    aside: null,
    taskProgress: null,
    waiting: false,
    workingSinceUnixMilliseconds: null
  });
}

{
  // A turn that simply finished is not news; what it said before finishing is. This is
  // the rule the transcript already keeps, and the line keeps it too.
  const line = restLineFrom([prompt("go"), agentMessage("done"), turnEnded("completed")], "owner");
  assert.equal(line.text, "done");
}

{
  // A turn that failed or was interrupted is news, and the reason travels with it.
  assert.equal(
    restLineFrom([prompt("go"), agentMessage("half"), turnEnded("failed", "the child stopped")], "owner")
      .text,
    "turn failed · the child stopped"
  );
  assert.equal(
    restLineFrom([prompt("go"), turnEnded("interrupted")], "owner").text,
    "turn interrupted"
  );
}

{
  // A turn nobody saw the end of counts too — the pane says the ending the record will
  // never contain, and that is the last thing that happened.
  const line = restLineFrom(
    [prompt("go"), toolCall("Read", "read"), row("turn_stopped", {})],
    "owner"
  );
  assert.equal(line.text, "turn stopped without an ending");
  assert.equal(line.workingSinceUnixMilliseconds, null, "a stopped turn is not running");
}

{
  // Your own message is a thing that happened, and it is the one thing on this line that
  // has to say whose it is: with one thing on the line there are no sides to read it by.
  const yours = restLineFrom([prompt("look at this"), turnEnded()], "owner");
  assert.equal(yours.who, "you");
  assert.equal(yours.text, "look at this");

  const somebodyElses = restLineFrom([prompt("go", "the automatic loop"), turnEnded()], "owner");
  assert.equal(somebodyElses.who, "the automatic loop");
}

{
  // A message that never reached anything is the one thing at rest that nothing else
  // would ever say.
  const refused = restLineFrom(
    [
      row("prompt_refused", {
        content: [{ piece: "text", text: "held text" }],
        senderLabel: "owner",
        reason: "backend_did_not_start",
        sentence: "the backend would not start"
      })
    ],
    "owner"
  );
  assert.equal(refused.who, "you");
  assert.equal(refused.text, "not delivered · the backend would not start");
  assert.equal(refused.workingSinceUnixMilliseconds, null, "a refusal started no turn");

  const discarded = restLineFrom(
    [
      row("prompt_discarded", {
        content: [{ piece: "text", text: "held text" }],
        senderLabel: "owner"
      })
    ],
    "owner"
  );
  assert.equal(discarded.who, "you");
  assert.equal(discarded.text, "discarded without being delivered");
}

{
  // What the record keeps for its own sake is not a thing that happened: what a turn
  // cost, what it runs on, where the thread was cut, and the plan — which is read as
  // progress beside the line rather than as the line.
  const rows = [
    prompt("go"),
    agentMessage("the answer"),
    row("plan_updated", { entries: PLAN }),
    turnEnded(),
    row("token_usage", { inputTokens: 41_000, outputTokens: 920, cachedInputTokens: null, costUsd: 0.42 }),
    row("model_changed", { model: "sonnet", reasoningEffort: "low" }),
    row("context_compacted", {})
  ];
  const line = restLineFrom(rows, "owner");
  assert.equal(line.text, "the answer");
  assert.equal(line.aside, null, "the plan is a fact about a turn that is running, and none is");
  assert.equal(line.taskProgress, null);
}

{
  // A message that carried only a picture has no first line, so what a person would name
  // as the last thing that happened is the thing before it.
  const line = restLineFrom(
    [
      prompt("here it is"),
      turnEnded(),
      row("agent_message", {
        content: [{ piece: "image", stored_file_id: "f1", media_type: "image/png" }]
      })
    ],
    "owner"
  );
  assert.equal(line.text, "here it is");
  assert.equal(line.who, "you");
}

// --- 4. nothing has happened ---------------------------------------------------------------------

{
  assert.equal(restLineFrom([], "owner"), null, "nothing at all draws nothing at all");
  assert.equal(
    restLineFrom([row("model_changed", { model: "opus", reasoningEffort: null })], "owner"),
    null,
    "and neither does a record holding only its own bookkeeping"
  );
}

// --- one line's worth --------------------------------------------------------------------------

{
  const long = "x".repeat(REST_LINE_MAXIMUM_CHARACTERS * 2);
  const line = restLineFrom([prompt(`${long}\nand a second line`), turnEnded()], "owner");
  assert.equal(line.text.length, REST_LINE_MAXIMUM_CHARACTERS);
  assert.match(line.text, /…$/, "what will not fit says that it did not fit");
  assert.doesNotMatch(line.text, /second line/, "and only the first line was ever a candidate");
}

// --- the same readings, from the rows a live conversation actually produces -----------------------

{
  function event(sequence, kind, payload) {
    return { conversation_id: "c1", sequence, kind, payload, created_at: 1_700_000_000 };
  }
  const feed = feedWithCommittedEvents(emptyConversationFeed(), [
    event(1, "prompt", {
      text: "go",
      sender_label: "owner",
      mode: "run_when_free",
      sent_at_unix_milliseconds: 1_700_000_000_400
    }),
    event(2, "plan_updated", { entries: PLAN }),
    event(3, "tool_call_started", {
      tool_call_id: "t1",
      title: "Bash",
      tool_kind: "Bash",
      detail: '{"command": "ls -la /tmp"}'
    })
  ]);

  const running = restLineFrom(transcriptRows(feed), "owner");
  assert.equal(running.text, "write it");
  assert.equal(running.taskProgress.currentPosition, 2);
  assert.equal(running.workingSinceUnixMilliseconds, 1_700_000_000_400);

  // The same conversation with an ask on it: the ask is what the line says.
  const asked = feedWithCommittedEvents(feed, [
    event(4, "permission_asked", { ask_id: "a1", title: "Run rm -rf", detail: null, options: [] })
  ]);
  const waiting = restLineFrom(transcriptRows(asked), "owner");
  assert.equal(waiting.waiting, true);
  assert.equal(waiting.text, "Run rm -rf");

  // And the same conversation once the server has said the turn is not running any more
  // without ever having written its ending: the ask died with it, and the line says the
  // ending the record will never contain.
  const stopped = restLineFrom(
    transcriptRows(asked, { turnStoppedWithoutAnEnding: true }),
    "owner"
  );
  assert.equal(stopped.waiting, false, "an ask nobody can answer is not waiting on anybody");
  assert.equal(stopped.text, "turn stopped without an ending");
  assert.equal(stopped.workingSinceUnixMilliseconds, null);
}

console.log("conversation-rest-line.test.mjs: all assertions passed");
