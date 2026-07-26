/** The conversation pane as it actually draws and behaves.
 *
 * Three passes. The first reads the components: the inventory, that each compiles without
 * a Svelte warning, that their styles are written in tokens, and that they reuse the
 * conversation styles the app already has rather than forking a second set. The second
 * renders them on the server and reads the markup, which is where the rules about what
 * must be visible are checked — a dead ask, a steer that only hermes is offered, an ask
 * taking the composer over, and the card that makes an ask nobody understands answerable.
 * The third opens the composer in a real browser, because "browsing a picker changes
 * nothing" is a claim about what happens when a person clicks.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { compile } from "svelte/compiler";
import { render } from "svelte/server";
import { build } from "vite";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const componentDirectory = new URL("../src/components/conversation2/", import.meta.url);

// --- what the components are ----------------------------------------------------------------

const expectedInventory = [
  "BackendCard.svelte",
  "ConversationComposer.svelte",
  "ConversationPane.svelte",
  "ConversationTranscript.svelte",
  "LiveConversation.svelte",
  "NewConversationForm.svelte",
  "PermissionAskActions.svelte",
  "PermissionAskCard.svelte",
  "PlanStrip.svelte",
  "ToolCallRow.svelte",
  "TurnAnchor.svelte",
  "WorkGroup.svelte"
];
const inventory = (await readdir(componentDirectory))
  .filter((name) => name.endsWith(".svelte"))
  .sort();
assert.deepEqual(inventory, expectedInventory);

const sources = {};
for (const fileName of inventory) {
  const source = await readFile(new URL(fileName, componentDirectory), "utf8");
  sources[fileName] = source;
  assert.deepEqual(
    compile(source, { filename: fileName, generate: "client", modernAst: true }).warnings,
    [],
    `${fileName} must compile without Svelte warnings`
  );
  assert.doesNotMatch(
    source,
    /#[0-9a-f]{3,8}\b|\brgb\(|\bhsl\(|gradient\(|box-shadow/i,
    `${fileName} must take its colours from tokens`
  );
  for (const style of source.matchAll(/<style>([\s\S]*?)<\/style>/g)) {
    assert.doesNotMatch(
      style[1],
      /(?:padding|margin|gap|border-radius|font-size|transition-duration):\s*\d+(?:\.\d+)?(?:px|rem|em|ms|s)\b/,
      `${fileName} must take its spacing, radii and type from tokens`
    );
  }
}

const routeSource = await readFile(
  new URL("../src/routes/DevConversationRoute.svelte", import.meta.url),
  "utf8"
);
const appSource = await readFile(new URL("../src/App.svelte", import.meta.url), "utf8");

// The pane is an adaptation of the conversation styles the app already has, not a second set.
for (const className of ["chat-panel", "chat-head", "chat-thread", "chat-jump", "chat-box", "chat-ta"]) {
  assert.ok(
    Object.values(sources).some((source) => source.includes(className)),
    `the pane must reuse .${className} rather than forking the stylesheet`
  );
}
assert.match(sources["ConversationTranscript.svelte"], /chat-u|chat-a/);
assert.match(sources["ConversationTranscript.svelte"], /MarkdownBlock/);
assert.match(sources["ConversationComposer.svelte"], /chat-seg/);
assert.match(sources["ConversationPane.svelte"], /chat-overflow/);

// The pane that came before this one is gone, and nothing may reach for it. The step
// glyphs are the one thing that came across, and they moved here rather than being left
// behind in a package nobody else uses.
for (const [fileName, source] of Object.entries(sources)) {
  assert.doesNotMatch(source, /lib\/acp\/|components\/acp\//, fileName);
}
assert.doesNotMatch(routeSource, /lib\/acp\/|components\/acp\//);
assert.match(sources["ToolCallRow.svelte"], /lib\/conversation2\/stepIcons/);

// The dev route exists, is reachable by hash, and is in no navigation.
assert.match(appSource, /DevConversationRoute/);
assert.match(appSource, /route\.name === "dev"/);
assert.doesNotMatch(appSource, /href="#\/dev/);
assert.match(routeSource, /#\/dev\/conversation\?id=/);

// New kills the old conversation before it mints a fresh id.
assert.match(routeSource, /killConversation/);
const newConversationBody = routeSource.slice(
  routeSource.indexOf("async function newConversation"),
  routeSource.indexOf("async function loadBackends")
);
assert.ok(
  newConversationBody.indexOf("killConversation") < newConversationBody.indexOf("mintConversationId"),
  "New must kill the old conversation before it mints a fresh id"
);

// Being live belongs to the binder, so that a Ticket and this page are live the same way
// rather than each in their own. These are assertions about that one piece of code.
const binderSource = sources["LiveConversation.svelte"];

// Reconnecting is the ordinary read, not a whole-screen refetch.
assert.match(binderSource, /visibilitychange/);
assert.match(binderSource, /stream\?\.connect\(\)/);

// The pane never shows a turn running on the rows alone: it reconciles them with what
// the system says about itself, and asks again every time it reconnects.
assert.match(binderSource, /conversationLiveness/);
assert.doesNotMatch(
  binderSource,
  /conversationIsRunning/,
  "the rows alone cannot say a turn stopped without an ending"
);
assert.match(binderSource, /turnStoppedWithoutAnEnding/);
assert.match(binderSource, /\(\) => void refreshView\(\)/);

// The route holds no conversation wiring of its own any more: one binder, two callers.
for (const owned of ["createConversationStream", "openConversationTail", "sendPrompt"]) {
  assert.doesNotMatch(
    routeSource,
    new RegExp(owned),
    `${owned} belongs to the binder, not to a route`
  );
}

// --- what the components draw ------------------------------------------------------------------

const ssrDirectory = await mkdtemp(join(webRoot, "tests", ".c2ssr-"));
const ssrEntry = join(webRoot, "tests", `.c2-ssr-entry-${process.pid}.js`);
let browserDirectory = null;
let hostPath = null;
let mainPath = null;
let indexPath = null;
let serverProcess = null;

try {
  await writeFile(
    ssrEntry,
    [
      'export { default as Transcript } from "../src/components/conversation2/ConversationTranscript.svelte";',
      'export { default as Composer } from "../src/components/conversation2/ConversationComposer.svelte";',
      'export { default as AskCard } from "../src/components/conversation2/PermissionAskCard.svelte";',
      'export { default as BackendCard } from "../src/components/conversation2/BackendCard.svelte";',
      'export { default as AskActions } from "../src/components/conversation2/PermissionAskActions.svelte";',
      'export { default as TurnAnchor } from "../src/components/conversation2/TurnAnchor.svelte";',
      'export { default as WorkGroup } from "../src/components/conversation2/WorkGroup.svelte";',
      'export { default as PlanStrip } from "../src/components/conversation2/PlanStrip.svelte";',
      'export { default as NewForm } from "../src/components/conversation2/NewConversationForm.svelte";',
      ""
    ].join("\n"),
    "utf8"
  );
  await build({
    root: webRoot,
    configFile: false,
    logLevel: "silent",
    plugins: [svelte()],
    build: {
      ssr: ssrEntry,
      outDir: ssrDirectory,
      emptyOutDir: true,
      rollupOptions: { output: { entryFileNames: "entry.mjs" } }
    }
  });
  const { AskActions, AskCard, BackendCard, Composer, NewForm, PlanStrip, Transcript, TurnAnchor, WorkGroup } = await import(
    join(ssrDirectory, "entry.mjs")
  );

  function drawn(component, props) {
    return render(component, { props }).body.replace(/<!--[\s\S]*?-->/g, "");
  }

  function askRow(state, deadReason = state === "dead" ? "turn_ended" : null) {
    return {
      key: "e2",
      kind: "permission_ask",
      sequence: 2,
      createdAt: 1_000,
      askId: "a1",
      title: "Run rm -rf",
      detail: "in ~/Coding",
      options: [],
      state,
      deadReason,
      answeredOptionLabel: state === "answered" ? "Approve once" : null
    };
  }

  // A dead ask is drawn plainly dead, and nothing on it is actionable.
  const deadThread = drawn(Transcript, { rows: [askRow("dead")] });
  assert.match(deadThread, /data-conversation2-ask-state="dead"/);
  assert.match(deadThread, /expired with the turn/);
  assert.doesNotMatch(deadThread, /<button/, "a dead ask offers nothing to press");

  const liveThread = drawn(Transcript, { rows: [askRow("live")] });
  assert.match(liveThread, /waiting for you/);
  assert.doesNotMatch(liveThread, /expired with the turn/);

  const answeredThread = drawn(Transcript, { rows: [askRow("answered")] });
  assert.match(answeredThread, /answered · Approve once/);

  // An ask whose turn stopped without an ending is dead in the same way, with its own
  // story — a reader told "expired with the turn" would go looking for an ending that
  // was never written.
  const stoppedThread = drawn(Transcript, {
    rows: [
      askRow("dead", "no_ending_recorded"),
      { key: "turn-stopped", kind: "turn_stopped", sequence: 3, createdAt: 1_000 }
    ]
  });
  assert.match(stoppedThread, /data-conversation2-ask-state="dead"/);
  assert.match(stoppedThread, /expired — its turn stopped without an ending/);
  assert.match(stoppedThread, /data-conversation2-row="turn_stopped"/);
  assert.match(stoppedThread, /turn stopped without an ending/);
  assert.doesNotMatch(stoppedThread, /<button/, "nothing here is actionable either");

  // The thread's other lines: a prompt says how it was sent, a turn ending carries its reason.
  const mixedThread = drawn(Transcript, {
    rows: [
      {
        key: "e1",
        kind: "prompt",
        sequence: 1,
        createdAt: 1_000,
        text: "go",
        senderLabel: "the automatic loop",
        mode: "steer",
        sentAtUnixMilliseconds: 1_000_000
      },
      {
        key: "e2",
        kind: "tool_call",
        sequence: 2,
        createdAt: 1_001,
        toolCallId: "t1",
        title: "Read file",
        toolKind: "read",
        detail: null,
        status: "failed",
        progress: null
      },
      {
        key: "e3",
        kind: "model_changed",
        sequence: 3,
        createdAt: 1_002,
        model: "sonnet",
        reasoningEffort: "low"
      },
      {
        key: "e4",
        kind: "turn_ended",
        sequence: 4,
        createdAt: 1_003,
        ending: "failed",
        errorSummary: "the child stopped"
      }
    ]
  });
  assert.match(mixedThread, /steered/);
  assert.match(mixedThread, /the automatic loop/, "somebody else's message says who sent it");
  assert.match(mixedThread, /now running on sonnet · low/);
  assert.match(mixedThread, /turn failed · the child stopped/);
  // The turn is over, so its one tool call is behind the fold rather than in the thread.
  assert.match(mixedThread, /Worked for 3s/);
  assert.doesNotMatch(mixedThread, /tool call/, "the count belongs inside the expansion");
  assert.doesNotMatch(mixedThread, /acp-step/);

  // FINDING 2: your own messages are not labelled as yours.
  function promptThread(senderLabel, ownSenderLabel) {
    return drawn(Transcript, {
      ownSenderLabel,
      rows: [
        {
          key: "e1",
          kind: "prompt",
          sequence: 1,
          createdAt: 1_000,
          text: "go",
          senderLabel,
          mode: "run_when_free",
          sentAtUnixMilliseconds: 1_000_000
        }
      ]
    });
  }
  assert.doesNotMatch(
    promptThread("owner", "owner"),
    /data-conversation2-prompt-label/,
    "the pane's own messages carry no label"
  );
  assert.match(promptThread("the automatic loop", "owner"), /the automatic loop/);
  assert.match(promptThread("owner", null), /owner/, "with no pane label nothing is suppressed");

  // And a message somebody else sent is a different kind of thing on the page, because a
  // thread is scanned by shape before it is read by label.
  assert.match(
    promptThread("owner", "owner"),
    /class="chat-u"/,
    "the reader's own message is drawn as theirs"
  );
  assert.match(
    promptThread("the automatic loop", "owner"),
    /class="chat-system"/,
    "a message the loop sent is not drawn in the reader's own voice"
  );

  const completedThread = drawn(Transcript, {
    rows: [
      { key: "e1", kind: "turn_ended", sequence: 1, createdAt: 1_000, ending: "completed", errorSummary: null }
    ]
  });
  assert.doesNotMatch(completedThread, /turn complete/, "a turn that simply finished says nothing");

  // FINDING 6: while a turn runs, one line is what is happening; the rest wait behind a count.
  // (toolRow is declared above, hoisted, so the finding-7 block can use it too.)
  function toolRow(index, status = "completed", detail = null) {
    return {
      key: `t${index}`,
      kind: "tool_call",
      sequence: index,
      createdAt: 1_000 + index,
      toolCallId: `t${index}`,
      title: `Tool ${index}`,
      toolKind: "read",
      detail,
      startedDetail: null,
      status,
      progress: null
    };
  }
  const runningWork = drawn(WorkGroup, { entries: [toolRow(1), toolRow(2), toolRow(3)] });
  assert.match(runningWork, /\+2 previous tool calls/);
  assert.equal(
    (runningWork.match(/data-conversation2-tool="/g) ?? []).length,
    1,
    "exactly one activity line while the turn runs"
  );
  assert.match(runningWork, /Tool 3/, "and it is the newest one");
  assert.doesNotMatch(runningWork, /Tool 1/);

  const oneEntryWork = drawn(WorkGroup, { entries: [toolRow(1)] });
  assert.doesNotMatch(oneEntryWork, /previous tool call/, "nothing is hidden when nothing is behind");

  // Settled: the head becomes the fold, and the runs are behind it.
  const settledWork = drawn(TurnAnchor, {
    settled: true,
    toolCallCount: 2,
    durationSeconds: 80
  });
  assert.match(settledWork, /Worked for 1m 20s/);
  assert.match(settledWork, /data-conversation2-turn-settled="true"/);
  assert.equal((settledWork.match(/data-conversation2-tool="/g) ?? []).length, 0);
  assert.doesNotMatch(settledWork, /2 tool calls/, "the count shows when it is opened");
  assert.match(
    drawn(TurnAnchor, { settled: true, toolCallCount: 2, durationSeconds: 80, expanded: true }),
    /2 tool calls/
  );
  // The fold holds the turn's commentary too, so the label counts that as well.
  assert.match(
    drawn(TurnAnchor, {
      settled: true,
      toolCallCount: 2,
      foldedMessageCount: 3,
      durationSeconds: 80,
      expanded: true
    }),
    /2 tool calls · 3 messages/
  );
  // And a turn that only ever talked folds like any other: five paragraphs are exactly as
  // long to scroll past as five tool calls.
  assert.match(
    drawn(TurnAnchor, { settled: true, toolCallCount: 0, foldedMessageCount: 3, durationSeconds: 9 }),
    /data-conversation2-turn-fold/
  );
  const hiddenRun = drawn(WorkGroup, { entries: [toolRow(1)], hidden: true });
  assert.equal(hiddenRun.trim(), "", "a settled run draws nothing until its turn is opened");

  // A row's own output is behind the row, capped, and never pasted into the thread.
  const withOutput = drawn(WorkGroup, {
    entries: [toolRow(1, "completed", "line one\nline two\nline three")]
  });
  assert.match(withOutput, /aria-expanded="false"/, "a tool row starts closed");
  assert.doesNotMatch(withOutput, /line three/, "its output is not in the thread");
  const withSummary = drawn(WorkGroup, {
    entries: [toolRow(1, "completed", "ls -la /tmp")]
  });
  assert.match(withSummary, /data-conversation2-tool-summary/);
  assert.match(withSummary, /ls -la \/tmp/, "a one-line detail is the summary itself");

  // A claude row: the backend titles it with the tool's name and hands over the call's
  // arguments as one JSON object, which is why this row used to read "Bash" and nothing
  // else. It now says what happened and which call it was.
  const claudeCall = drawn(WorkGroup, {
    entries: [
      {
        ...toolRow(1, "completed", "total 0\ndrwxr-xr-x  12 khushaljagota  staff  384 ."),
        title: "Bash",
        toolKind: "Bash",
        startedDetail: '{"command": "ls -la /tmp", "description": "List the temp directory"}'
      }
    ]
  });
  assert.match(claudeCall, /Ran/);
  assert.match(claudeCall, /ls -la \/tmp/);
  assert.doesNotMatch(claudeCall, /Bash/, "the tool's name was never the point");
  assert.doesNotMatch(claudeCall, /drwxr-xr-x/, "and what it gave back is still behind it");

  // FINDING 7 — a running turn has one mark that says the model is alive, from the moment
  // it starts. It is there before there is any work to put under it, which is the whole
  // point: the wait before the first tool call is the silence it exists to fill.
  const bareRunning = drawn(TurnAnchor, { startedAtUnixMilliseconds: Date.now() });
  assert.match(bareRunning, /data-conversation2-alive/);
  assert.match(bareRunning, /Working/, "it counts from the moment the prompt landed");
  assert.match(bareRunning, /c2-alive-dots/, "three dots, as T3 has");
  assert.doesNotMatch(bareRunning, /data-conversation2-turn-fold/, "nothing to fold yet");

  // Settled: the mark is gone and the fold stands in its place.
  const settledAnchor = drawn(TurnAnchor, {
    settled: true,
    toolCallCount: 1,
    durationSeconds: 4
  });
  assert.doesNotMatch(settledAnchor, /data-conversation2-alive/, "a finished turn is not thinking");
  assert.match(settledAnchor, /Worked for 4s/);

  // The latest turn a person stopped says who stopped it.
  assert.match(
    drawn(TurnAnchor, {
      settled: true,
      toolCallCount: 1,
      durationSeconds: 9,
      ending: "interrupted",
      isLatest: true
    }),
    /You stopped after 9s/
  );

  // A turn that stopped without an ending has no mark and claims no length.
  const stoppedAnchor = drawn(TurnAnchor, {
    settled: true,
    stopped: true,
    toolCallCount: 1,
    durationSeconds: null
  });
  assert.doesNotMatch(stoppedAnchor, /data-conversation2-alive/, "a dead process is not alive");
  assert.match(stoppedAnchor, /Worked</);
  assert.doesNotMatch(stoppedAnchor, /Worked for/);

  // And the whole transcript agrees: a stopped turn shows no thinking mark anywhere.
  const stoppedTurnThread = drawn(Transcript, {
    rows: [
      {
        key: "p1",
        kind: "prompt",
        sequence: 1,
        createdAt: 1_000,
        text: "go",
        senderLabel: "owner",
        mode: "run_when_free",
        sentAtUnixMilliseconds: 1_000_000
      },
      { key: "turn-stopped", kind: "turn_stopped", sequence: 2, createdAt: 1_005 }
    ]
  });
  assert.doesNotMatch(stoppedTurnThread, /data-conversation2-alive/);
  assert.match(stoppedTurnThread, /turn stopped without an ending/);

  // A settled turn's commentary going behind its fold, and coming back in the place it
  // happened when the fold opens, is asserted in the browser pass below — an agent message
  // is drawn through MarkdownBlock, which this server-rendering harness cannot mount.

  // A settled turn that folded nothing away still says how long it took, in the place it
  // has held since the turn began — the head is one line all the way through, and one that
  // removed itself on settling would move everything under it for nothing. There is simply
  // nothing to open.
  const nothingFolded = drawn(TurnAnchor, {
    settled: true,
    toolCallCount: 0,
    durationSeconds: 3
  });
  assert.match(nothingFolded, /Worked for 3s/);
  assert.doesNotMatch(nothingFolded, /data-conversation2-turn-fold/);

  // A turn that stopped without an ending is the exception: no length anybody can claim,
  // and its own row already says what happened.
  assert.equal(drawn(TurnAnchor, { settled: true, stopped: true, toolCallCount: 0 }).trim(), "");

  // FINDING 8 — the plan reads as a count you can open, in the old strip's own vocabulary.
  const plan = [
    { text: "read the code", status: "completed" },
    { text: "write it", status: "in_progress" },
    { text: "test it", status: "pending" }
  ];
  const strip = drawn(PlanStrip, { entries: plan });
  assert.match(strip, /1 \/ 3 tasks/);
  assert.match(strip, /aria-label="1 of 3 tasks complete"/);
  assert.doesNotMatch(strip, /data-conversation2-plan-list/, "the checklist starts closed");
  assert.equal(drawn(PlanStrip, { entries: [] }).trim(), "", "no plan, no strip");

  // It survives its turn: a settled anchor with no tool calls still carries the plan.
  const settledWithPlan = drawn(TurnAnchor, { settled: true, toolCallCount: 0, plan });
  assert.match(settledWithPlan, /data-conversation2-plan/);
  assert.match(settledWithPlan, /1 \/ 3 tasks/);
  assert.doesNotMatch(settledWithPlan, /data-conversation2-alive/);
  const runningWithPlan = drawn(TurnAnchor, { settled: false, plan });
  assert.match(runningWithPlan, /data-conversation2-alive/);
  assert.match(runningWithPlan, /data-conversation2-plan/);

  // Steering is offered to hermes and to nobody else.
  const hermesComposer = drawn(Composer, {
    backendKey: "hermes",
    running: true,
    onSend: async () => true
  });
  assert.match(hermesComposer, /data-conversation2-delivery-mode="steer"/);
  for (const backendKey of ["codex", "claude"]) {
    const composer = drawn(Composer, { backendKey, running: true, onSend: async () => true });
    assert.match(composer, /data-conversation2-delivery-mode="run_when_free"/);
    assert.match(composer, /data-conversation2-delivery-mode="send_now"/);
    assert.doesNotMatch(
      composer,
      /data-conversation2-delivery-mode="steer"/,
      `${backendKey} must not be offered steering`
    );
  }

  // Idle, there is nothing to choose: a message runs.
  const idleComposer = drawn(Composer, {
    backendKey: "hermes",
    running: false,
    onSend: async () => true
  });
  assert.doesNotMatch(idleComposer, /data-conversation2-delivery/);
  assert.match(idleComposer, /data-conversation2-send="true"/);
  assert.match(hermesComposer, /data-conversation2-stop="true"/);

  // The ask takes the composer over: the input is shut and the ask's own words replace it.
  const askedComposer = drawn(Composer, {
    backendKey: "codex",
    running: true,
    ask: {
      askId: "a1",
      title: "Run rm -rf",
      detail: "in ~/Coding",
      options: [
        { option_id: "o-allow-always", label: "Always allow this session", option_kind: "allow_always" },
        { option_id: "o-allow", label: "Approve once", option_kind: "allow_once" },
        { option_id: "o-reject", label: "Decline", option_kind: "reject_once" }
      ]
    },
    onSend: async () => true
  });
  assert.match(askedComposer, /data-conversation2-taken-over="true"/);
  assert.match(askedComposer, /<textarea[^>]*disabled/);
  assert.match(askedComposer, /placeholder="in ~\/Coding"/);
  assert.doesNotMatch(askedComposer, /data-conversation2-send/, "there is no sending past an ask");
  const answerOrder = [...askedComposer.matchAll(/data-conversation2-ask-action="([^"]+)"/g)].map(
    (found) => found[1]
  );
  assert.deepEqual(
    answerOrder,
    ["cancel_turn", "o-reject", "o-allow-always", "o-allow"],
    "the answers run in the order of how much they commit to"
  );
  assert.match(askedComposer, /data-conversation2-ask-shape="permission"/);
  assert.doesNotMatch(askedComposer, /data-conversation2-ask-fallback/);

  // FINDING 5: the ask is a band on top of a composer that keeps its resting height, and
  // everything the backend sent is bounded and scrolls inside it. That is the whole of
  // why the card is the composer's size rather than the request's size.
  const askedPanel = askedComposer.slice(askedComposer.indexOf("c2-ask"));
  assert.match(askedComposer, /class="chat-box has-ask"/);
  assert.match(askedComposer, /<textarea/, "the input stays where it was, at its own height");
  assert.match(askedComposer, /pending approval/, "an eyebrow, as T3 has");
  const bigDetailComposer = drawn(Composer, {
    backendKey: "codex",
    running: true,
    ask: {
      askId: "a1",
      title: "Run something enormous",
      detail: "x".repeat(20_000),
      options: []
    },
    onSend: async () => true
  });
  const askStyles = sources["PermissionAskCard.svelte"];
  assert.match(askStyles, /max-height:[^;]+;\s*\n\s*overflow: auto;/, "the detail is bounded and scrolls");
  assert.match(askStyles, /white-space: pre-wrap;/);
  assert.match(askStyles, /overflow-wrap: anywhere;/, "so a single enormous line cannot widen it");
  assert.match(bigDetailComposer, /data-conversation2-ask-detail/);
  assert.ok(askedPanel.length > 0);

  // An ask nobody understands still leaves a person three real things to do.
  const genericCard = drawn(AskCard, {
    ask: { askId: "a9", title: "Something the backend did not describe", detail: null, options: [] },
    onAnswer() {}
  });
  assert.match(genericCard, /data-conversation2-ask-shape="shapeless"/);
  assert.match(genericCard, /data-conversation2-ask-fallback/);
  const genericActions = drawn(AskActions, {
    ask: { options: [] },
    onAnswer() {},
    onCancelTurn() {}
  });
  assert.deepEqual(
    [...genericActions.matchAll(/data-conversation2-ask-action="([^"]+)"/g)].map((found) => found[1]),
    ["cancel_turn", "reject_once", "allow_once"]
  );
  assert.equal(
    (genericActions.match(/data-conversation2-ask-supplied="false"/g) ?? []).length,
    2,
    "the answers Panels had to supply say so"
  );

  // FINDING 6: a question is answered by picking, not by approving.
  const questionAsk = {
    askId: "q1",
    title: "Which way should this go?",
    detail: '{"questions":[{"header":"plan","question":"Which way should this go?"}]}',
    options: [
      { option_id: "q-a", label: "Rewrite it", option_kind: "choice" },
      { option_id: "q-b", label: "Leave it", option_kind: "choice" }
    ]
  };
  const questionCard = drawn(AskCard, { ask: questionAsk, onAnswer() {} });
  assert.match(questionCard, /data-conversation2-ask-shape="question"/);
  assert.match(questionCard, /class="c2-ask-eyebrow[^"]*">question</);
  assert.deepEqual(
    [...questionCard.matchAll(/data-conversation2-ask-choice="([^"]+)"/g)].map((found) => found[1]),
    ["q-a", "q-b"],
    "the backend's own choices, numbered"
  );
  assert.match(questionCard, /<kbd[^>]*>1<\/kbd>/);
  assert.match(questionCard, /<kbd[^>]*>2<\/kbd>/);
  // The payload does not sit on the face of the card, and it is never raw when shown.
  assert.doesNotMatch(questionCard, /\{"questions"/, "no raw JSON on the card");
  assert.match(questionCard, /data-conversation2-ask-raw-toggle/);
  assert.match(questionCard, /Show the request/);
  const questionActions = drawn(AskActions, {
    ask: questionAsk,
    onAnswer() {},
    onCancelTurn() {}
  });
  assert.deepEqual(
    [...questionActions.matchAll(/data-conversation2-ask-action="([^"]+)"/g)].map((found) => found[1]),
    ["cancel_turn"],
    "a question offers no approving language, only the escape hatch"
  );

  // The effort picker exists only where the backend has such a setting.
  const hermesPicker = drawn(Composer, {
    backendKey: "hermes",
    running: false,
    effortOptions: [],
    models: [{ model_id: "m1", display_name: "One" }],
    onSend: async () => true
  });
  assert.doesNotMatch(
    hermesPicker,
    /data-conversation2-picker-effort/,
    "no effort options at all means no effort control, zero pixels"
  );
  assert.match(hermesPicker, /data-conversation2-picker-model/, "hermes still picks a model");

  // A model that takes no effort gets no effort control either, even where the backend
  // has a list of its own.
  const haikuPicker = drawn(Composer, {
    backendKey: "claude",
    running: false,
    effortOptions: ["low", "high"],
    models: [
      { model_id: "haiku", display_name: "Haiku", reasoning_effort_options: [] },
      { model_id: "opus", display_name: "Opus" }
    ],
    current: { model: "haiku", reasoningEffort: null },
    onSend: async () => true
  });
  assert.doesNotMatch(haikuPicker, /data-conversation2-picker-effort/);
  assert.match(haikuPicker, /data-conversation2-picker-model/);

  // Options exist but nothing to show: the control keeps its capability and gives up
  // its width rather than sitting there empty and wide.
  const bareEffort = drawn(Composer, {
    backendKey: "claude",
    running: false,
    effortOptions: ["low", "high"],
    models: [{ model_id: "opus", display_name: "Opus" }],
    current: { model: "opus", reasoningEffort: null },
    onSend: async () => true
  });
  assert.match(bareEffort, /data-conversation2-picker-effort-bare="true"/);
  assert.match(bareEffort, /class="[^"]*is-bare/);
  assert.match(bareEffort, /aria-label="Reasoning effort"/, "and it still says what it is");
  assert.match(bareEffort, /<option value="low">low<\/option>/, "opening it offers the real ones");

  // The empty state obeys the same rule: never a wide empty select.
  function newForm(snapshot) {
    return drawn(NewForm, { conversationId: "dev-1", backends: [snapshot], backendKey: snapshot.backend_key });
  }
  const claudeFresh = newForm({
    backend_key: "claude",
    installed: true,
    available_models: [{ model_id: "opus", display_name: "Opus" }],
    reasoning_effort_options: ["low", "high"],
    default_model_id: "opus",
    default_reasoning_effort: null,
    diagnoses: []
  });
  assert.match(claudeFresh, /data-conversation2-new-effort-bare="true"/);
  assert.match(claudeFresh, /is-bare/);
  const hermesFresh = newForm({
    backend_key: "hermes",
    installed: true,
    available_models: [{ model_id: "gpt-5.5", display_name: "GPT-5.5" }],
    reasoning_effort_options: [],
    default_model_id: "gpt-5.5",
    default_reasoning_effort: null,
    diagnoses: []
  });
  assert.doesNotMatch(
    hermesFresh,
    /data-conversation2-new-effort/,
    "a backend with no efforts gets no effort control in the empty state either"
  );

  // Once there is a value, the value is the label again.
  const valuedEffort = drawn(Composer, {
    backendKey: "claude",
    running: false,
    effortOptions: ["low", "high"],
    models: [{ model_id: "opus", display_name: "Opus" }],
    current: { model: "opus", reasoningEffort: "high" },
    onSend: async () => true
  });
  assert.doesNotMatch(valuedEffort, /data-conversation2-picker-effort-bare/);
  assert.doesNotMatch(valuedEffort, /is-bare/);
  assert.match(valuedEffort, /<option value="high" selected/);

  // FINDING 4: the selectors are in the footer, in place. There is nowhere to navigate to.
  const claudePicker = drawn(Composer, {
    backendKey: "claude",
    running: false,
    effortOptions: ["low", "high"],
    models: [{ model_id: "opus", display_name: "Opus 5", detail: "opus → claude-opus-5" }],
    current: { model: "opus", reasoningEffort: "high" },
    onSend: async () => true
  });
  assert.match(claudePicker, /<select[^>]*data-conversation2-picker-model/);
  assert.match(claudePicker, /<select[^>]*data-conversation2-picker-effort/);
  assert.doesNotMatch(claudePicker, /data-conversation2-picker-toggle/, "no panel to open");
  assert.doesNotMatch(claudePicker, /data-conversation2-picker-abandon/);
  // They show what the conversation runs on now.
  assert.match(claudePicker, /<option value="opus"[^>]*selected/);
  assert.match(claudePicker, /<option value="high" selected/);
  // Nothing is armed until something is picked.
  assert.doesNotMatch(claudePicker, /data-conversation2-picker-armed/);
  // FINDING 3: what an alias reaches rides along on the option and on the select's face.
  assert.match(claudePicker, /title="opus → claude-opus-5"/);
  assert.match(claudePicker, /title="Opus 5 — opus → claude-opus-5"/);

  // A backend card says what is known and names the terminal command when signing in is due.
  const backendCard = drawn(BackendCard, {
    snapshot: {
      backend_key: "codex",
      installed: true,
      executable_path: "/usr/local/bin/codex",
      version: "0.145.0",
      identity: {
        status: "unauthenticated",
        account_label: null,
        detail: null,
        login_command: "codex login"
      },
      available_models: [],
      reasoning_effort_options: [],
      update_advisory: {
        install_method: "npm_global",
        update_command: "npm install -g @openai/codex@latest",
        latest_version: "0.146.0",
        update_available: true,
        detail: "Version 0.146.0 is available."
      },
      diagnoses: ["`codex` is not signed in. Run `codex login` in a terminal."]
    },
    onUpdate() {}
  });
  assert.match(backendCard, /not signed in · run codex login in a terminal/);
  assert.match(backendCard, /Version 0\.146\.0 is available\./);
  assert.match(backendCard, /data-conversation2-backend-update="codex"/);
  assert.match(backendCard, /is not signed in\. Run `codex login` in a terminal\./);

  const updatedCard = drawn(BackendCard, {
    snapshot: {
      backend_key: "hermes",
      installed: true,
      executable_path: "/opt/hermes",
      version: "0.18.2",
      identity: null,
      available_models: [{ model_id: "m1", display_name: "One" }],
      reasoning_effort_options: [],
      update_advisory: {
        install_method: "manual_only",
        update_command: null,
        latest_version: null,
        update_available: false,
        detail: "Hermes is installed from its own checkout."
      },
      diagnoses: []
    },
    result: { outcome: "unchanged", detail: "still 0.18.2", output_tail: "" },
    onUpdate() {}
  });
  assert.match(updatedCard, /no account to sign in to/);
  assert.match(updatedCard, /this backend has no such setting/);
  assert.match(updatedCard, /data-conversation2-backend-result="unchanged"/);
  assert.doesNotMatch(
    updatedCard,
    /data-conversation2-backend-update/,
    "no button is offered for an update Panels cannot run"
  );

  // --- what happens when a person clicks ------------------------------------------------------

  browserDirectory = await mkdtemp(join(tmpdir(), "panels-conversation2-pane-"));
  hostPath = join(webRoot, "tests", `.c2-host-${process.pid}.svelte`);
  mainPath = join(webRoot, "tests", `.c2-main-${process.pid}.ts`);
  indexPath = join(webRoot, "tests", `.c2-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    `
<script lang="ts">
  import ConversationComposer from "../src/components/conversation2/ConversationComposer.svelte";
  import ConversationTranscript from "../src/components/conversation2/ConversationTranscript.svelte";
  import PermissionAskCard from "../src/components/conversation2/PermissionAskCard.svelte";

  const sends: unknown[] = [];
  const answers: string[] = [];
  (window as any).__sends = () => sends;
  (window as any).__answers = () => answers;

  async function onSend(text: string, mode: string, picked: unknown): Promise<boolean> {
    sends.push({ text, mode, picked });
    return true;
  }

  function toolRow(index: number, detail: string | null) {
    return {
      key: "t" + index,
      kind: "tool_call" as const,
      sequence: index,
      createdAt: 1000 + index,
      toolCallId: "t" + index,
      title: "Tool " + index,
      toolKind: "read",
      detail,
      startedDetail: null,
      status: "completed" as const,
      progress: null
    };
  }

  const settledRows = [
    {
      key: "p1",
      kind: "prompt" as const,
      sequence: 1,
      createdAt: 1000,
      text: "go",
      senderLabel: "owner",
      mode: "run_when_free" as const,
      sentAtUnixMilliseconds: 1_000_000
    },
    {
      key: "sa1",
      kind: "agent_message" as const,
      sequence: 15,
      createdAt: 1001,
      text: "commentary on the way"
    },
    toolRow(2, "first output line\\nsecond output line"),
    toolRow(3, null),
    toolRow(4, null),
    {
      key: "sa2",
      kind: "agent_message" as const,
      sequence: 16,
      createdAt: 1011,
      text: "the answer itself"
    },
    {
      key: "e9",
      kind: "turn_ended" as const,
      sequence: 9,
      createdAt: 1012,
      ending: "completed" as const,
      errorSummary: null
    }
  ];

  const runningRows = [
    {
      key: "rp1",
      kind: "prompt" as const,
      // An hour apart on purpose, and only one of them can be what the counter counts
      // from. The row was written an hour ago; the person pressed send a moment ago. A
      // counter anchored to the row would say an hour, so what it says settles which of
      // the two it is using.
      createdAt: Math.floor(Date.now() / 1000) - 3600,
      text: "go",
      senderLabel: "owner",
      mode: "run_when_free" as const,
      sentAtUnixMilliseconds: Date.now() - 400
    },
    { ...toolRow(21, null), key: "r21", toolCallId: "r21", title: "Batch one A" },
    { ...toolRow(22, null), key: "r22", toolCallId: "r22", title: "Batch one B" },
    {
      key: "ra1",
      kind: "agent_message" as const,
      sequence: 23,
      createdAt: 1023,
      text: "found it"
    },
    { ...toolRow(24, null), key: "r24", toolCallId: "r24", title: "Batch two A" },
    { ...toolRow(25, null), key: "r25", toolCallId: "r25", title: "Batch two B" },
    {
      key: "rplan",
      kind: "plan_updated" as const,
      sequence: 26,
      createdAt: 1026,
      entries: [
        { text: "read the code", status: "completed" as const },
        { text: "write it", status: "in_progress" as const }
      ]
    }
  ];

  const question = {
    askId: "q1",
    title: "Which way should this go?",
    detail: null,
    options: [
      { option_id: "q-a", label: "Rewrite it", option_kind: "choice" },
      { option_id: "q-b", label: "Leave it", option_kind: "choice" }
    ]
  };
</script>

<ConversationComposer
  backendKey="claude"
  running={false}
  current={{ model: null, reasoningEffort: null }}
  defaultModelId="opus"
  models={[
    { model_id: "opus", display_name: "Opus" },
    { model_id: "sonnet", display_name: "Sonnet" },
    { model_id: "haiku", display_name: "Haiku", reasoning_effort_options: [] }
  ]}
  effortOptions={["low", "high"]}
  {onSend}
/>

<ConversationTranscript rows={settledRows} ownSenderLabel="owner" />

<div data-running-thread>
  <ConversationTranscript rows={runningRows} ownSenderLabel="owner" />
</div>

<PermissionAskCard ask={question} onAnswer={(optionId) => answers.push(optionId)} />
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    `
import { mount } from "svelte";
import Host from "./${hostPath.split("/").at(-1)}";
mount(Host, { target: document.getElementById("app")! });
`,
    "utf8"
  );
  await writeFile(
    indexPath,
    `<!doctype html><html><body><div id="app"></div><script type="module" src="./${mainPath
      .split("/")
      .at(-1)}"></script></body></html>`,
    "utf8"
  );

  await build({
    root: webRoot,
    base: "./",
    configFile: false,
    logLevel: "silent",
    plugins: [svelte()],
    build: {
      emptyOutDir: true,
      outDir: browserDirectory,
      rollupOptions: { input: { index: indexPath } }
    }
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", browserDirectory],
    { cwd: repositoryRoot, stdio: "ignore" }
  );
  const builtIndex = (await readdir(browserDirectory, { recursive: true })).find((path) =>
    path.endsWith(".html")
  );
  assert.ok(builtIndex, "the component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = `
from playwright.sync_api import sync_playwright
import re
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="domcontentloaded")

    # FINDING 11 — the selectors show the concrete value already in force, unlabelled,
    # and the word "default" appears nowhere in them.
    model = page.locator("[data-conversation2-picker-model]")
    effort = page.locator("[data-conversation2-picker-effort]")
    assert model.input_value() == "opus", model.input_value()
    # Claude names no default effort, so the control is there but bare — never empty-wide.
    assert effort.input_value() == "", effort.input_value()
    assert effort.get_attribute("data-conversation2-picker-effort-bare") == "true"
    bare_width = effort.bounding_box()["width"]
    model_width = model.bounding_box()["width"]
    assert bare_width < model_width, (bare_width, model_width)
    # The owner's rule, in pixels: nothing empty and wide sits in the footer. What is
    # left is the native arrow's own minimum box and nothing more.
    assert bare_width <= 56, bare_width
    options = page.locator("[data-conversation2-picker-model] option").all_inner_texts()
    assert options == ["Opus", "Sonnet", "Haiku"], options
    effort_options = [
        value for value in page.locator("[data-conversation2-picker-effort] option").all_inner_texts()
        if value.strip()
    ]
    assert effort_options == ["low", "high"], effort_options
    body = page.inner_text("body").lower()
    assert "default" not in body, "the word default must never appear in a selector"
    assert page.locator("[data-conversation2-picker-armed]").count() == 0

    # Showing the backend's own value is not choosing it: untouched sends nothing.
    page.locator("[data-conversation2-input]").fill("go")
    page.locator("[data-conversation2-send]").click()
    page.wait_for_function("window.__sends().length === 1")
    first = page.evaluate("window.__sends()[0]")
    assert first["picked"]["model"] is None, first
    assert first["picked"]["reasoningEffort"] is None, first

    # A pick arms commit-on-send exactly as before, and rides the next message —
    # including a pick made from the bare effort control, which then shows its value.
    model.select_option("sonnet")
    effort.select_option("low")
    assert page.evaluate("window.__sends().length") == 1
    assert page.locator("[data-conversation2-picker-armed]").count() == 1
    assert effort.get_attribute("data-conversation2-picker-effort-bare") is None, (
        "once it has a value the value is the label again"
    )
    assert effort.bounding_box()["width"] >= bare_width
    page.locator("[data-conversation2-input]").fill("again")
    page.locator("[data-conversation2-send]").click()
    page.wait_for_function("window.__sends().length === 2")
    second = page.evaluate("window.__sends()[1]")
    assert second["picked"]["model"] == "sonnet", second
    assert second["picked"]["reasoningEffort"] == "low", second
    page.wait_for_function("document.querySelector('[data-conversation2-input]').value === ''")
    assert page.locator("[data-conversation2-picker-armed]").count() == 0
    assert model.input_value() == "opus", "and it falls back to the value in force"
    assert effort.get_attribute("data-conversation2-picker-effort-bare") == "true", (
        "the effort has nothing to show again, so it is bare again"
    )

    # A model that takes no effort takes the effort control with it — and the pick that
    # the new catalog cannot honor goes too, rather than riding out as a value nothing
    # would accept.
    effort.select_option("low")
    assert page.locator("[data-conversation2-picker-effort]").count() == 1
    model.select_option("haiku")
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation2-picker-effort]').length === 0"
    )
    model.select_option("opus")
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation2-picker-effort]').length === 1"
    )
    assert page.locator("[data-conversation2-picker-effort]").get_attribute(
        "data-conversation2-picker-effort-bare"
    ) == "true", "the dropped pick did not come back"
    model.select_option("opus")

    # FINDING 9 — a settled turn folds to "Worked for X" with no count on the label.
    fold = page.locator("[data-conversation2-turn-fold]").first
    label = page.locator("[data-conversation2-turn-label]").first.inner_text()
    assert label == "Worked for 12s", label
    assert page.locator("[data-conversation2-turn-count]").count() == 0
    settled_thread = page.locator("[data-conversation2-transcript]").first
    assert settled_thread.locator("[data-conversation2-tool]").count() == 0

    # The turn's commentary is behind the same fold, and its answer is not. A settled turn
    # reads as one paragraph, not as everything it said on the way to one.
    settled_text = settled_thread.inner_text()
    assert "commentary on the way" not in settled_text, settled_text
    assert "the answer itself" in settled_text, settled_text

    # Opening the turn brings its run back, still showing only its newest entry — the
    # turn's fold and the run's own count are two different questions.
    fold.click()
    settled = page.locator("[data-conversation2-transcript]").first
    page.wait_for_function(
        "document.querySelector('[data-conversation2-transcript]')"
        ".querySelectorAll('[data-conversation2-tool]').length === 1"
    )
    assert page.locator("[data-conversation2-turn-count]").first.inner_text() == (
        "3 tool calls · 1 message"
    )
    assert settled.locator(".acp-step-title").all_inner_texts() == ["Tool 4"]

    # And the commentary comes back where it happened: after the person's message and
    # before the run of tool calls it sat before, rather than gathered up at the end.
    opened = settled.inner_text()
    assert "commentary on the way" in opened, opened
    assert opened.index("go") < opened.index("commentary on the way") < opened.index("Tool 4"), opened
    assert opened.index("commentary on the way") < opened.index("the answer itself"), opened

    settled.locator("[data-conversation2-work-fold]").click()
    page.wait_for_function(
        "document.querySelector('[data-conversation2-transcript]')"
        ".querySelectorAll('[data-conversation2-tool]').length === 3"
    )

    # A tool row's own output stays behind the row, capped and scrolling.
    page.locator('[data-conversation2-tool="t2"]').click()
    page.wait_for_function("document.querySelectorAll('[data-conversation2-tool-output]').length === 1")
    output = page.locator("[data-conversation2-tool-output]")
    assert "second output line" in output.inner_text(), output.inner_text()
    assert output.bounding_box()["height"] <= 400

    fold.click()
    page.wait_for_function(
        "document.querySelector('[data-conversation2-transcript]')"
        ".querySelectorAll('[data-conversation2-tool]').length === 0"
    )

    # FINDING 7 — the running turn has a head that counts, with three dots beside it.
    running = page.locator("[data-running-thread]")
    alive = running.locator("[data-conversation2-alive]")
    assert alive.count() == 1
    # Seconds, not the hour ago the row was written: the counter is anchored to the instant
    # the person pressed send. Any number of seconds passes, a minute would not.
    assert re.fullmatch(r"Working for \\d{1,2}s", alive.inner_text()), alive.inner_text()

    # FINDING 10 — two batches, each showing only its newest entry, expanded separately.
    groups = running.locator("[data-conversation2-work-group]")
    assert groups.count() == 2, groups.count()
    assert running.locator("[data-conversation2-tool]").count() == 2
    titles = running.locator(".acp-step-title").all_inner_texts()
    assert titles == ["Batch one B", "Batch two B"], titles

    groups.nth(0).locator("[data-conversation2-work-fold]").click()
    page.wait_for_function(
        "document.querySelector('[data-running-thread]')"
        ".querySelectorAll('[data-conversation2-tool]').length === 3"
    )
    assert groups.nth(0).get_attribute("data-conversation2-work-expanded") == "true"
    assert groups.nth(1).get_attribute("data-conversation2-work-expanded") == "false", (
        "opening one batch leaves the other alone"
    )

    # FINDING 8 — the plan is a count that opens into the checklist.
    pill = running.locator("[data-conversation2-plan-pill]")
    assert pill.count() == 1
    assert "1 / 2 tasks" in pill.inner_text(), pill.inner_text()
    assert running.locator("[data-conversation2-plan-list]").count() == 0
    pill.click()
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation2-plan-list]').length === 1"
    )
    marks = running.locator("[data-conversation2-plan-status]").all_inner_texts()
    assert "read the code" in marks[0], marks
    pill.click()
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation2-plan-list]').length === 0"
    )

    # A question is answered by its number key as well as by its row.
    page.keyboard.press("2")
    page.wait_for_function("window.__answers().length === 1")
    assert page.evaluate("window.__answers()[0]") == "q-b"
    page.locator('[data-conversation2-ask-choice="q-a"]').click()
    page.wait_for_function("window.__answers().length === 2")
    assert page.evaluate("window.__answers()[1]") == "q-a"

    # A number typed into a field is the number, not the answer.
    page.locator("[data-conversation2-input]").fill("")
    page.locator("[data-conversation2-input]").type("1")
    assert page.evaluate("window.__answers().length") == 2

    # The slash aims what is already written at a skill, and hands the box back with the
    # cursor at the end so the next thing typed is the skill's name. Pressing it again
    # changes nothing: the message is already aimed.
    page.locator("[data-conversation2-input]").fill("do the thing")
    page.locator("[data-conversation2-slash]").click()
    page.wait_for_function(
        "document.querySelector('[data-conversation2-input]').value === '/do the thing'"
    )
    assert page.evaluate(
        "document.activeElement === document.querySelector('[data-conversation2-input]')"
    )
    assert page.evaluate("document.querySelector('[data-conversation2-input]').selectionStart") == (
        len("/do the thing")
    )
    page.locator("[data-conversation2-slash]").click()
    assert page.locator("[data-conversation2-input]").input_value() == "/do the thing"

    browser.close()

print("conversation2 pane runtime assertions passed")
`;
  const browserProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] }
  );
  let output = "";
  browserProbe.stdout.on("data", (chunk) => {
    output += chunk;
  });
  browserProbe.stderr.on("data", (chunk) => {
    output += chunk;
  });
  const exitCode = await new Promise((resolve) => browserProbe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /conversation2 pane runtime assertions passed/);
} finally {
  serverProcess?.kill();
  await rm(ssrDirectory, { recursive: true, force: true });
  await rm(ssrEntry, { force: true });
  if (browserDirectory) await rm(browserDirectory, { recursive: true, force: true });
  if (hostPath) await rm(hostPath, { force: true });
  if (mainPath) await rm(mainPath, { force: true });
  if (indexPath) await rm(indexPath, { force: true });
}

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const port = address.port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`component runtime server did not become ready: ${url}`);
}

console.log("conversation2-pane.test.mjs: all assertions passed");
