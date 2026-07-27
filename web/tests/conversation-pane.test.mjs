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
const componentDirectory = new URL("../src/components/conversation/", import.meta.url);

// --- what the components are ----------------------------------------------------------------

const expectedInventory = [
  "AgentCommandMenu.svelte",
  "BackendCard.svelte",
  "ConversationComposer.svelte",
  "ConversationPane.svelte",
  "ConversationRestBar.svelte",
  "ConversationTranscript.svelte",
  "LiveConversation.svelte",
  "MessagePieces.svelte",
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
for (const className of [
  "chat-panel",
  "chat-head",
  "chat-thread",
  "chat-jump",
  "chat-box",
  "chat-ta",
  // The app already has a "/" command menu's styles, anchored above the box. The menu of
  // the agent's own commands is drawn in them rather than in a second set of the same.
  "chat-menu"
]) {
  assert.ok(
    Object.values(sources).some((source) => source.includes(className)),
    `the pane must reuse .${className} rather than forking the stylesheet`
  );
}
assert.match(sources["ConversationTranscript.svelte"], /chat-u|chat-a/);
// The agent's words still go through the markdown renderer; the piece that draws a
// message is where that now lives, because a message is a run of pieces and its words are
// one of them.
assert.match(sources["MessagePieces.svelte"], /MarkdownBlock/);
assert.match(sources["ConversationTranscript.svelte"], /MessagePieces/);
assert.match(sources["ConversationComposer.svelte"], /chat-seg/);
assert.match(sources["ConversationPane.svelte"], /chat-overflow/);

// The pane that came before this one is gone, and nothing may reach for it. The step
// glyphs are the one thing that came across, and they moved here rather than being left
// behind in a package nobody else uses.
for (const [fileName, source] of Object.entries(sources)) {
  assert.doesNotMatch(source, /lib\/acp\/|components\/acp\//, fileName);
}
assert.doesNotMatch(routeSource, /lib\/acp\/|components\/acp\//);
assert.match(sources["ToolCallRow.svelte"], /lib\/conversation\/stepIcons/);

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

// The commands an agent reports are what the composer offers, and they arrive moments
// after its session starts — during its first turn. So a turn stopping is another
// occasion to ask the system about itself, which is how a conversation opened before its
// first turn comes to have them without a page being reloaded. That the re-read happens,
// and that the menu has the commands after it, is asserted in a browser against a real
// server: tests/e2e/test_dev_conversation_pane.py. What is asserted here is only that
// nothing polls for it.
assert.doesNotMatch(
  binderSource,
  /setInterval|setTimeout/,
  "a re-read hangs off something that happened, never off a timer"
);

// A message that reached the conversation is told to whoever mounted the binder, so a
// caller holding more than a conversation can act on it. Only after it was taken: the
// refusal path returns before this, because a message that reached nothing is not one
// anybody should act on.
assert.match(binderSource, /await onMessageAccepted\?\.\(\)/);
const acceptedIsToldAt = binderSource.indexOf("await onMessageAccepted?.()");
const refusalReturnsAt = binderSource.indexOf('if (fate.fate === "refused")');
assert.ok(
  refusalReturnsAt > -1 && refusalReturnsAt < acceptedIsToldAt,
  "a refused send must return before anything is told the message was accepted"
);
assert.equal(
  binderSource.split("onMessageAccepted?.()").length - 1,
  1,
  "there is one place a message is reported accepted, not several to keep in step"
);

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
      'export { default as Transcript } from "../src/components/conversation/ConversationTranscript.svelte";',
      'export { default as Composer } from "../src/components/conversation/ConversationComposer.svelte";',
      'export { default as AskCard } from "../src/components/conversation/PermissionAskCard.svelte";',
      'export { default as BackendCard } from "../src/components/conversation/BackendCard.svelte";',
      'export { default as AskActions } from "../src/components/conversation/PermissionAskActions.svelte";',
      'export { default as TurnAnchor } from "../src/components/conversation/TurnAnchor.svelte";',
      'export { default as WorkGroup } from "../src/components/conversation/WorkGroup.svelte";',
      'export { default as PlanStrip } from "../src/components/conversation/PlanStrip.svelte";',
      'export { default as NewForm } from "../src/components/conversation/NewConversationForm.svelte";',
      'export { default as Pane } from "../src/components/conversation/ConversationPane.svelte";',
      'export { default as CommandMenu } from "../src/components/conversation/AgentCommandMenu.svelte";',
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
  const { AskActions, AskCard, BackendCard, CommandMenu, Composer, NewForm, Pane, PlanStrip, Transcript, TurnAnchor, WorkGroup } = await import(
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

  // A message this browser has sent and the record has not caught up with can be taken
  // back — but only while the system says it is holding it. One whose fate is not known
  // yet may already be on the wire, and one that never came back is not a message
  // anybody can decide about.
  function outgoing(messageId, knownFate) {
    return {
      messageId,
      content: [{ piece: "text", text: `text of ${messageId}` }],
      senderLabel: "owner",
      mode: "run_when_free",
      sentAtUnixMilliseconds: 1_000,
      knownFate
    };
  }

  const waiting = drawn(Pane, {
    conversationId: "c1",
    label: "Worker",
    outgoingMessages: [
      outgoing("held", "waiting_for_the_agent"),
      outgoing("in-flight", "nothing_yet"),
      outgoing("unanswered", "answer_never_came_back")
    ],
    onDiscardHeldPrompt: () => undefined
  });
  assert.match(waiting, /data-conversation-outgoing-discard="held"/);
  assert.doesNotMatch(waiting, /data-conversation-outgoing-discard="in-flight"/);
  assert.doesNotMatch(waiting, /data-conversation-outgoing-discard="unanswered"/);

  // A caller that cannot take a message back is not offered the control at all, rather
  // than offered one that does nothing.
  const noDiscard = drawn(Pane, {
    conversationId: "c1",
    label: "Worker",
    outgoingMessages: [outgoing("held", "waiting_for_the_agent")]
  });
  assert.match(noDiscard, /data-conversation-outgoing="held"/);
  assert.doesNotMatch(noDiscard, /data-conversation-outgoing-discard/);

  // A message that holds a picture draws the picture, on both sides of the thread, and
  // fetches it from the conversation that kept it. A transcript that drew only the words
  // would show a message the record says had a picture in it and show no picture.
  const withAPicture = [
    { piece: "text", text: "look at this" },
    { piece: "image", stored_file_id: "f_1", media_type: "image/png", file_name: "shot.png" }
  ];
  // The agent's side carries only the picture here: its words go through MarkdownBlock,
  // which this server-rendering harness cannot mount, and the browser pass below is where
  // an agent's words are asserted.
  const pictureThread = drawn(Transcript, {
    conversationId: "c1",
    rows: [
      {
        key: "e1",
        kind: "prompt",
        sequence: 1,
        createdAt: 1_000,
        content: withAPicture,
        senderLabel: "owner",
        mode: "run_when_free",
        sentAtUnixMilliseconds: 1_000_000
      },
      {
        key: "e2",
        kind: "agent_message",
        sequence: 2,
        createdAt: 1_001,
        content: [withAPicture[1]]
      }
    ]
  });
  assert.equal(
    (pictureThread.match(/data-conversation-piece="image"/g) ?? []).length,
    2,
    "your picture and the agent's are both drawn"
  );
  assert.match(pictureThread, /src="\/api\/conversation\/conversations\/c1\/files\/f_1"/);
  assert.match(pictureThread, /alt="shot.png"/, "a picture says what it is called");
  // The words beside it go through the markdown renderer, which mounts in a browser and
  // not here, so what they read as is asserted in the browser pass rather than guessed at
  // from an empty host element.
  assert.match(pictureThread, /markdown-host/);


  // What a turn cost is drawn, and where the backend cut the thread is drawn as the seam
  // it is — reusing the stylesheet the old pane drew the same thing with, rather than a
  // second one for the same idea.
  const spentAndCut = drawn(Transcript, {
    conversationId: "c1",
    rows: [
      {
        key: "e1",
        kind: "token_usage",
        sequence: 1,
        createdAt: 1_000,
        inputTokens: 41_000,
        outputTokens: 920,
        cachedInputTokens: null,
        costUsd: 0.42
      },
      { key: "e2", kind: "context_compacted", sequence: 2, createdAt: 1_001 }
    ]
  });
  assert.match(spentAndCut, /data-conversation-row="token_usage"/);
  assert.match(spentAndCut, /41\.0k in · 920 out · \$0\.42/);
  assert.doesNotMatch(spentAndCut, /cached/, "a count the backend never gave is not drawn");
  assert.match(spentAndCut, /data-conversation-row="context_compacted"/);
  assert.match(spentAndCut, /acp-compaction/, "the seam reuses the stylesheet, not a fork");
  assert.match(spentAndCut, /context compacted/);

  // A turn whose backend counted nothing draws no line at all, rather than an empty one.
  const countedNothing = drawn(Transcript, {
    conversationId: "c1",
    rows: [
      {
        key: "e1",
        kind: "token_usage",
        sequence: 1,
        createdAt: 1_000,
        inputTokens: null,
        outputTokens: null,
        cachedInputTokens: null,
        costUsd: null
      }
    ]
  });
  assert.doesNotMatch(countedNothing, /data-conversation-row="token_usage"/);

  // A dead ask is drawn plainly dead, and nothing on it is actionable.
  const deadThread = drawn(Transcript, { conversationId: "c1", rows: [askRow("dead")] });
  assert.match(deadThread, /data-conversation-ask-state="dead"/);
  assert.match(deadThread, /expired with the turn/);
  assert.doesNotMatch(deadThread, /<button/, "a dead ask offers nothing to press");

  const liveThread = drawn(Transcript, { conversationId: "c1", rows: [askRow("live")] });
  assert.match(liveThread, /waiting for you/);
  assert.doesNotMatch(liveThread, /expired with the turn/);

  const answeredThread = drawn(Transcript, { conversationId: "c1", rows: [askRow("answered")] });
  assert.match(answeredThread, /answered · Approve once/);

  // An ask whose turn stopped without an ending is dead in the same way, with its own
  // story — a reader told "expired with the turn" would go looking for an ending that
  // was never written.
  const stoppedThread = drawn(Transcript, {
    conversationId: "c1",
    rows: [
      askRow("dead", "no_ending_recorded"),
      { key: "turn-stopped", kind: "turn_stopped", sequence: 3, createdAt: 1_000 }
    ]
  });
  assert.match(stoppedThread, /data-conversation-ask-state="dead"/);
  assert.match(stoppedThread, /expired — its turn stopped without an ending/);
  assert.match(stoppedThread, /data-conversation-row="turn_stopped"/);
  assert.match(stoppedThread, /turn stopped without an ending/);
  assert.doesNotMatch(stoppedThread, /<button/, "nothing here is actionable either");

  // The thread's other lines: a prompt says how it was sent, a turn ending carries its reason.
  const mixedThread = drawn(Transcript, {
    conversationId: "c1",
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
    /data-conversation-prompt-label/,
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
    conversationId: "c1",
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
      // The title names the tool, the way claude's rows do, so what the backend wrote in
      // the detail is what says which call this was.
      title: `Tool ${index}`,
      toolKind: `Tool ${index}`,
      detail,
      startedDetail: null,
      status,
      progress: null
    };
  }
  const runningWork = drawn(WorkGroup, { entries: [toolRow(1), toolRow(2), toolRow(3)] });
  assert.match(runningWork, /\+2 previous tool calls/);
  assert.equal(
    (runningWork.match(/data-conversation-tool="/g) ?? []).length,
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
  assert.match(settledWork, /data-conversation-turn-settled="true"/);
  assert.equal((settledWork.match(/data-conversation-tool="/g) ?? []).length, 0);
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
    /data-conversation-turn-fold/
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
  assert.match(withSummary, /data-conversation-tool-summary/);
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
  assert.match(bareRunning, /data-conversation-alive/);
  assert.match(bareRunning, /Working/, "it counts from the moment the prompt landed");
  // Being alive is said by the words moving, not by dots beside them — the app's own
  // shimmer, the same one a thought in progress is drawn with.
  assert.doesNotMatch(bareRunning, /c2-alive-dots/);
  assert.match(
    bareRunning,
    /live-text-shimmer/,
    "a turn that has only just begun is already alive: starting is the sign of life"
  );
  assert.doesNotMatch(bareRunning, /data-conversation-turn-fold/, "nothing to fold yet");

  // Settled: the mark is gone and the fold stands in its place.
  const settledAnchor = drawn(TurnAnchor, {
    settled: true,
    toolCallCount: 1,
    durationSeconds: 4
  });
  assert.doesNotMatch(settledAnchor, /data-conversation-alive/, "a finished turn is not thinking");
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
  assert.doesNotMatch(stoppedAnchor, /data-conversation-alive/, "a dead process is not alive");
  assert.match(stoppedAnchor, /Worked</);
  assert.doesNotMatch(stoppedAnchor, /Worked for/);

  // And the whole transcript agrees: a stopped turn shows no thinking mark anywhere.
  const stoppedTurnThread = drawn(Transcript, {
    conversationId: "c1",
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
  assert.doesNotMatch(stoppedTurnThread, /data-conversation-alive/);
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
  assert.doesNotMatch(nothingFolded, /data-conversation-turn-fold/);

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
  assert.doesNotMatch(strip, /data-conversation-plan-list/, "the checklist starts closed");
  assert.equal(drawn(PlanStrip, { entries: [] }).trim(), "", "no plan, no strip");

  // It survives its turn: a settled anchor with no tool calls still carries the plan.
  const settledWithPlan = drawn(TurnAnchor, { settled: true, toolCallCount: 0, plan });
  assert.match(settledWithPlan, /data-conversation-plan/);
  assert.match(settledWithPlan, /1 \/ 3 tasks/);
  assert.doesNotMatch(settledWithPlan, /data-conversation-alive/);
  const runningWithPlan = drawn(TurnAnchor, { settled: false, plan });
  assert.match(runningWithPlan, /data-conversation-alive/);
  assert.match(runningWithPlan, /data-conversation-plan/);

  // Steering is offered to hermes and to nobody else.
  const hermesComposer = drawn(Composer, {
    backendKey: "hermes",
    running: true,
    onSend: async () => true
  });
  assert.match(hermesComposer, /data-conversation-delivery-mode="steer"/);
  for (const backendKey of ["codex", "claude"]) {
    const composer = drawn(Composer, { backendKey, running: true, onSend: async () => true });
    assert.match(composer, /data-conversation-delivery-mode="run_when_free"/);
    assert.match(composer, /data-conversation-delivery-mode="send_now"/);
    assert.doesNotMatch(
      composer,
      /data-conversation-delivery-mode="steer"/,
      `${backendKey} must not be offered steering`
    );
  }

  // Idle, there is nothing to choose: a message runs.
  const idleComposer = drawn(Composer, {
    backendKey: "hermes",
    running: false,
    onSend: async () => true
  });
  assert.doesNotMatch(idleComposer, /data-conversation-delivery/);
  assert.match(idleComposer, /data-conversation-send="true"/);
  assert.match(hermesComposer, /data-conversation-stop="true"/);

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
  assert.match(askedComposer, /data-conversation-taken-over="true"/);
  assert.match(askedComposer, /<textarea[^>]*disabled/);
  assert.match(askedComposer, /placeholder="in ~\/Coding"/);
  assert.doesNotMatch(askedComposer, /data-conversation-send/, "there is no sending past an ask");
  const answerOrder = [...askedComposer.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map(
    (found) => found[1]
  );
  assert.deepEqual(
    answerOrder,
    ["cancel_turn", "o-reject", "o-allow-always", "o-allow"],
    "the answers run in the order of how much they commit to"
  );
  assert.match(askedComposer, /data-conversation-ask-shape="permission"/);
  assert.doesNotMatch(askedComposer, /data-conversation-ask-fallback/);

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
  assert.match(bigDetailComposer, /data-conversation-ask-detail/);
  assert.ok(askedPanel.length > 0);

  // An ask nobody understands still leaves a person three real things to do.
  const genericCard = drawn(AskCard, {
    ask: { askId: "a9", title: "Something the backend did not describe", detail: null, options: [] },
    onAnswer() {}
  });
  assert.match(genericCard, /data-conversation-ask-shape="shapeless"/);
  assert.match(genericCard, /data-conversation-ask-fallback/);
  const genericActions = drawn(AskActions, {
    ask: { options: [] },
    onAnswer() {},
    onCancelTurn() {}
  });
  assert.deepEqual(
    [...genericActions.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((found) => found[1]),
    ["cancel_turn", "reject_once", "allow_once"]
  );
  assert.equal(
    (genericActions.match(/data-conversation-ask-supplied="false"/g) ?? []).length,
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
  assert.match(questionCard, /data-conversation-ask-shape="question"/);
  assert.match(questionCard, /class="c2-ask-eyebrow[^"]*">question</);
  assert.deepEqual(
    [...questionCard.matchAll(/data-conversation-ask-choice="([^"]+)"/g)].map((found) => found[1]),
    ["q-a", "q-b"],
    "the backend's own choices, numbered"
  );
  assert.match(questionCard, /<kbd[^>]*>1<\/kbd>/);
  assert.match(questionCard, /<kbd[^>]*>2<\/kbd>/);
  // The payload does not sit on the face of the card, and it is never raw when shown.
  assert.doesNotMatch(questionCard, /\{"questions"/, "no raw JSON on the card");
  assert.match(questionCard, /data-conversation-ask-raw-toggle/);
  assert.match(questionCard, /Show the request/);
  const questionActions = drawn(AskActions, {
    ask: questionAsk,
    onAnswer() {},
    onCancelTurn() {}
  });
  assert.deepEqual(
    [...questionActions.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((found) => found[1]),
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
    /data-conversation-picker-effort/,
    "no effort options at all means no effort control, zero pixels"
  );
  assert.match(hermesPicker, /data-conversation-picker-model/, "hermes still picks a model");

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
  assert.doesNotMatch(haikuPicker, /data-conversation-picker-effort/);
  assert.match(haikuPicker, /data-conversation-picker-model/);

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
  assert.match(bareEffort, /data-conversation-picker-effort-bare="true"/);
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
  assert.match(claudeFresh, /data-conversation-new-effort-bare="true"/);
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
    /data-conversation-new-effort/,
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
  assert.doesNotMatch(valuedEffort, /data-conversation-picker-effort-bare/);
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
  assert.match(claudePicker, /<select[^>]*data-conversation-picker-model/);
  assert.match(claudePicker, /<select[^>]*data-conversation-picker-effort/);
  assert.doesNotMatch(claudePicker, /data-conversation-picker-toggle/, "no panel to open");
  assert.doesNotMatch(claudePicker, /data-conversation-picker-abandon/);
  // They show what the conversation runs on now.
  assert.match(claudePicker, /<option value="opus"[^>]*selected/);
  assert.match(claudePicker, /<option value="high" selected/);
  // Nothing is armed until something is picked.
  assert.doesNotMatch(claudePicker, /data-conversation-picker-armed/);
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
  assert.match(backendCard, /data-conversation-backend-update="codex"/);
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
  assert.match(updatedCard, /data-conversation-backend-result="unchanged"/);
  assert.doesNotMatch(
    updatedCard,
    /data-conversation-backend-update/,
    "no button is offered for an update Panels cannot run"
  );

  // The command menu draws what the agent reported and nothing else: the name a person
  // types, what the backend said it does, and the argument where it named one.
  const menu = drawn(CommandMenu, {
    commands: [
      { name: "plan", description: "Write the plan", argument_hint: null },
      { name: "compact", description: "Shrink the context", argument_hint: "[instructions]" }
    ],
    activeIndex: 1,
    anyCommandsAtAll: true,
    onChoose() {},
    onHighlight() {}
  });
  assert.deepEqual(
    [...menu.matchAll(/data-conversation-command="([^"]+)"/g)].map((found) => found[1]),
    ["plan", "compact"],
    "the rows are drawn in the order they were offered in"
  );
  assert.match(menu, /\/plan/);
  assert.match(menu, /Write the plan/);
  assert.match(menu, /data-conversation-command-argument[^>]*>\[instructions\]/);
  assert.equal(
    (menu.match(/data-conversation-command-active="true"/g) ?? []).length,
    1,
    "exactly one row is highlighted"
  );

  // Both ways of having nothing to offer are said out loud, and they are different
  // sentences. Nothing to filter says only that: an empty list is an agent that reports no
  // commands and an agent that has not reported yet, and nothing here knows which, so the
  // sentence must be true of both and must not put it on the agent.
  const noCommandsAtAll = drawn(CommandMenu, {
    commands: [],
    anyCommandsAtAll: false,
    onChoose() {},
    onHighlight() {}
  });
  assert.match(noCommandsAtAll, /data-conversation-commands-empty/);
  assert.match(noCommandsAtAll, /No commands here\./);
  assert.doesNotMatch(
    noCommandsAtAll,
    /agent/i,
    "an empty list is not something the agent can be said to have reported"
  );
  const nothingMatched = drawn(CommandMenu, {
    commands: [],
    anyCommandsAtAll: true,
    onChoose() {},
    onHighlight() {}
  });
  assert.match(nothingMatched, /No command matches that\./);

  // And nothing is on screen until a command is being written.
  assert.doesNotMatch(idleComposer, /data-conversation-commands/);

  // --- what happens when a person clicks ------------------------------------------------------

  browserDirectory = await mkdtemp(join(tmpdir(), "panels-conversation-pane-"));
  hostPath = join(webRoot, "tests", `.c2-host-${process.pid}.svelte`);
  mainPath = join(webRoot, "tests", `.c2-main-${process.pid}.ts`);
  indexPath = join(webRoot, "tests", `.c2-index-${process.pid}.html`);

  await writeFile(
    hostPath,
    `
<script lang="ts">
  import ConversationComposer from "../src/components/conversation/ConversationComposer.svelte";
  import ConversationTranscript from "../src/components/conversation/ConversationTranscript.svelte";
  import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";

  const sends: unknown[] = [];
  const answers: string[] = [];
  (window as any).__sends = () => sends;
  (window as any).__answers = () => answers;

  // What the agent reported it can be asked to do. Settable from the test, because an
  // agent that reports none is a state a person must be able to read, not a second pane.
  let availableCommands = $state<any[]>([
    { name: "plan", description: "Write the plan", argument_hint: "[what to plan]" },
    { name: "replan", description: "Start the plan again", argument_hint: null },
    { name: "compact", description: "Shrink the context", argument_hint: null },
    { name: "apply-plan", description: "Do what the plan says", argument_hint: null }
  ]);
  (window as any).__setCommands = (next: any[]) => {
    availableCommands = next;
  };

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
      toolKind: "Tool " + index,
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
      content: [{ piece: "text", text: "go" }],
      senderLabel: "owner",
      mode: "run_when_free" as const,
      sentAtUnixMilliseconds: 1_000_000
    },
    {
      key: "sa1",
      kind: "agent_message" as const,
      sequence: 15,
      createdAt: 1001,
      content: [{ piece: "text", text: "commentary on the way" }]
    },
    toolRow(2, "first output line\\nsecond output line"),
    toolRow(3, null),
    toolRow(4, null),
    {
      key: "sa2",
      kind: "agent_message" as const,
      sequence: 16,
      createdAt: 1011,
      content: [{ piece: "text", text: "the answer itself" }]
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
      content: [{ piece: "text", text: "go" }],
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
      content: [{ piece: "text", text: "found it" }]
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
  {availableCommands}
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
    model = page.locator("[data-conversation-picker-model]")
    effort = page.locator("[data-conversation-picker-effort]")
    assert model.input_value() == "opus", model.input_value()
    # Claude names no default effort, so the control is there but bare — never empty-wide.
    assert effort.input_value() == "", effort.input_value()
    assert effort.get_attribute("data-conversation-picker-effort-bare") == "true"
    bare_width = effort.bounding_box()["width"]
    model_width = model.bounding_box()["width"]
    assert bare_width < model_width, (bare_width, model_width)
    # The owner's rule, in pixels: nothing empty and wide sits in the footer. What is
    # left is the native arrow's own minimum box and nothing more.
    assert bare_width <= 56, bare_width
    options = page.locator("[data-conversation-picker-model] option").all_inner_texts()
    assert options == ["Opus", "Sonnet", "Haiku"], options
    effort_options = [
        value for value in page.locator("[data-conversation-picker-effort] option").all_inner_texts()
        if value.strip()
    ]
    assert effort_options == ["low", "high"], effort_options
    body = page.inner_text("body").lower()
    assert "default" not in body, "the word default must never appear in a selector"
    assert page.locator("[data-conversation-picker-armed]").count() == 0

    # Showing the backend's own value is not choosing it: untouched sends nothing.
    page.locator("[data-conversation-input]").fill("go")
    page.locator("[data-conversation-send]").click()
    page.wait_for_function("window.__sends().length === 1")
    first = page.evaluate("window.__sends()[0]")
    assert first["picked"]["model"] is None, first
    assert first["picked"]["reasoningEffort"] is None, first

    # A pick arms commit-on-send exactly as before, and rides the next message —
    # including a pick made from the bare effort control, which then shows its value.
    model.select_option("sonnet")
    effort.select_option("low")
    assert page.evaluate("window.__sends().length") == 1
    assert page.locator("[data-conversation-picker-armed]").count() == 1
    assert effort.get_attribute("data-conversation-picker-effort-bare") is None, (
        "once it has a value the value is the label again"
    )
    assert effort.bounding_box()["width"] >= bare_width
    page.locator("[data-conversation-input]").fill("again")
    page.locator("[data-conversation-send]").click()
    page.wait_for_function("window.__sends().length === 2")
    second = page.evaluate("window.__sends()[1]")
    assert second["picked"]["model"] == "sonnet", second
    assert second["picked"]["reasoningEffort"] == "low", second
    page.wait_for_function("document.querySelector('[data-conversation-input]').value === ''")
    assert page.locator("[data-conversation-picker-armed]").count() == 0
    assert model.input_value() == "opus", "and it falls back to the value in force"
    assert effort.get_attribute("data-conversation-picker-effort-bare") == "true", (
        "the effort has nothing to show again, so it is bare again"
    )

    # A model that takes no effort takes the effort control with it — and the pick that
    # the new catalog cannot honor goes too, rather than riding out as a value nothing
    # would accept.
    effort.select_option("low")
    assert page.locator("[data-conversation-picker-effort]").count() == 1
    model.select_option("haiku")
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation-picker-effort]').length === 0"
    )
    model.select_option("opus")
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation-picker-effort]').length === 1"
    )
    assert page.locator("[data-conversation-picker-effort]").get_attribute(
        "data-conversation-picker-effort-bare"
    ) == "true", "the dropped pick did not come back"
    model.select_option("opus")

    # FINDING 9 — a settled turn folds to "Worked for X" with no count on the label.
    fold = page.locator("[data-conversation-turn-fold]").first
    label = page.locator("[data-conversation-turn-label]").first.inner_text()
    assert label == "Worked for 12s", label
    assert page.locator("[data-conversation-turn-count]").count() == 0
    settled_thread = page.locator("[data-conversation-transcript]").first
    assert settled_thread.locator("[data-conversation-tool]").count() == 0

    # The turn's commentary is behind the same fold, and its answer is not. A settled turn
    # reads as one paragraph, not as everything it said on the way to one.
    settled_text = settled_thread.inner_text()
    assert "commentary on the way" not in settled_text, settled_text
    assert "the answer itself" in settled_text, settled_text

    # Opening the turn brings its run back, still showing only its newest entry — the
    # turn's fold and the run's own count are two different questions.
    fold.click()
    settled = page.locator("[data-conversation-transcript]").first
    page.wait_for_function(
        "document.querySelector('[data-conversation-transcript]')"
        ".querySelectorAll('[data-conversation-tool]').length === 1"
    )
    assert page.locator("[data-conversation-turn-count]").first.inner_text() == (
        "3 tool calls · 1 message"
    )
    assert settled.locator(".acp-step-title").all_inner_texts() == ["Tool 4"]

    # And the commentary comes back where it happened: after the person's message and
    # before the run of tool calls it sat before, rather than gathered up at the end.
    opened = settled.inner_text()
    assert "commentary on the way" in opened, opened
    assert opened.index("go") < opened.index("commentary on the way") < opened.index("Tool 4"), opened
    assert opened.index("commentary on the way") < opened.index("the answer itself"), opened

    settled.locator("[data-conversation-work-fold]").click()
    page.wait_for_function(
        "document.querySelector('[data-conversation-transcript]')"
        ".querySelectorAll('[data-conversation-tool]').length === 3"
    )

    # A tool row's own output stays behind the row, capped and scrolling.
    page.locator('[data-conversation-tool="t2"]').click()
    page.wait_for_function("document.querySelectorAll('[data-conversation-tool-output]').length === 1")
    output = page.locator("[data-conversation-tool-output]")
    assert "second output line" in output.inner_text(), output.inner_text()
    assert output.bounding_box()["height"] <= 400

    fold.click()
    page.wait_for_function(
        "document.querySelector('[data-conversation-transcript]')"
        ".querySelectorAll('[data-conversation-tool]').length === 0"
    )

    # FINDING 7 — the running turn has a head that counts, with three dots beside it.
    running = page.locator("[data-running-thread]")
    alive = running.locator("[data-conversation-alive]")
    assert alive.count() == 1
    # Seconds, not the hour ago the row was written: the counter is anchored to the instant
    # the person pressed send. Any number of seconds passes, a minute would not.
    assert re.fullmatch(r"Working for \\d{1,2}s", alive.inner_text()), alive.inner_text()

    # FINDING 10 — two batches, each showing only its newest entry, expanded separately.
    groups = running.locator("[data-conversation-work-group]")
    assert groups.count() == 2, groups.count()
    assert running.locator("[data-conversation-tool]").count() == 2
    titles = running.locator(".acp-step-title").all_inner_texts()
    assert titles == ["Batch one B", "Batch two B"], titles

    groups.nth(0).locator("[data-conversation-work-fold]").click()
    page.wait_for_function(
        "document.querySelector('[data-running-thread]')"
        ".querySelectorAll('[data-conversation-tool]').length === 3"
    )
    assert groups.nth(0).get_attribute("data-conversation-work-expanded") == "true"
    assert groups.nth(1).get_attribute("data-conversation-work-expanded") == "false", (
        "opening one batch leaves the other alone"
    )

    # FINDING 8 — the plan is a count that opens into the checklist.
    pill = running.locator("[data-conversation-plan-pill]")
    assert pill.count() == 1
    assert "1 / 2 tasks" in pill.inner_text(), pill.inner_text()
    assert running.locator("[data-conversation-plan-list]").count() == 0
    pill.click()
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation-plan-list]').length === 1"
    )
    marks = running.locator("[data-conversation-plan-status]").all_inner_texts()
    assert "read the code" in marks[0], marks
    pill.click()
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation-plan-list]').length === 0"
    )

    # A question is answered by its number key as well as by its row.
    page.keyboard.press("2")
    page.wait_for_function("window.__answers().length === 1")
    assert page.evaluate("window.__answers()[0]") == "q-b"
    page.locator('[data-conversation-ask-choice="q-a"]').click()
    page.wait_for_function("window.__answers().length === 2")
    assert page.evaluate("window.__answers()[1]") == "q-a"

    # A number typed into a field is the number, not the answer.
    page.locator("[data-conversation-input]").fill("")
    page.locator("[data-conversation-input]").type("1")
    assert page.evaluate("window.__answers().length") == 2

    # The slash puts a command at the front of what is already written, and hands the box
    # back with the cursor just after it — where the command's name goes, and where a
    # person who typed the slash themselves would be. Pressing it again changes nothing:
    # the message already starts with one.
    page.locator("[data-conversation-input]").fill("do the thing")
    page.locator("[data-conversation-slash]").click()
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value === '/do the thing'"
    )
    assert page.evaluate(
        "document.activeElement === document.querySelector('[data-conversation-input]')"
    )
    assert page.evaluate("document.querySelector('[data-conversation-input]').selectionStart") == 1
    page.locator("[data-conversation-slash]").click()
    assert page.locator("[data-conversation-input]").input_value() == "/do the thing"
    assert page.evaluate("document.querySelector('[data-conversation-input]').selectionStart") == 1
    # Pressing it never takes the cursor out of the box, so the menu it opened is still up.
    assert page.evaluate(
        "document.activeElement === document.querySelector('[data-conversation-input]')"
    )

    # --- the commands the agent reports -----------------------------------------------------

    def listed():
        return page.eval_on_selector_all(
            "[data-conversation-command]",
            "rows => rows.map(row => row.dataset.conversationCommand)",
        )

    def highlighted():
        return page.evaluate(
            "document.querySelector('[data-conversation-command-active]')"
            "?.dataset.conversationCommand ?? null"
        )

    # The slash button leaves the cursor inside the command's name, which is the one rule
    # that opens the menu, and what is in it is what this conversation's agent said it can
    # be asked to do.
    assert page.locator("[data-conversation-commands]").count() == 1
    assert listed() == ["apply-plan", "compact", "plan", "replan"], listed()
    assert "[what to plan]" in page.locator("[data-conversation-commands]").inner_text()

    box = page.locator("[data-conversation-input]")
    sent_before_the_menu = page.evaluate("window.__sends().length")

    # An arrow key is a move inside the menu and nothing else, so the menu the button
    # opened is still open after one and Enter takes the row it landed on. Anything less
    # loses the message: Enter would fall through and send what was being written.
    page.keyboard.press("ArrowDown")
    page.wait_for_function(
        "document.querySelector('[data-conversation-command-active]')"
        ".dataset.conversationCommand === 'compact'"
    )
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value === '/compact the thing'"
    )
    assert page.evaluate("window.__sends().length") == sent_before_the_menu

    # Typing narrows it: what starts with the letters first, then what merely contains
    # them, alphabetically within each.
    box.fill("")
    box.type("/pl")
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 3")
    assert listed() == ["plan", "apply-plan", "replan"], listed()
    assert highlighted() == "plan", highlighted()

    # Up from the top wraps to the bottom, and down comes back.
    page.keyboard.press("ArrowUp")
    page.wait_for_function(
        "document.querySelector('[data-conversation-command-active]')"
        ".dataset.conversationCommand === 'replan'"
    )
    page.keyboard.press("ArrowDown")
    page.wait_for_function(
        "document.querySelector('[data-conversation-command-active]')"
        ".dataset.conversationCommand === 'plan'"
    )

    # Down moves the highlight and Enter takes that row: what goes in is the text a person
    # would have typed, the cursor lands after it, the menu closes, and nothing is sent.
    page.keyboard.press("ArrowDown")
    page.wait_for_function(
        "document.querySelector('[data-conversation-command-active]')"
        ".dataset.conversationCommand === 'apply-plan'"
    )
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value === '/apply-plan '"
    )
    assert page.locator("[data-conversation-commands]").count() == 0
    assert page.evaluate("document.querySelector('[data-conversation-input]').selectionStart") == (
        len("/apply-plan ")
    )
    assert page.evaluate("window.__sends().length") == sent_before_the_menu

    # Escape closes it and leaves what was typed exactly where it was.
    box.fill("")
    box.type("/pl")
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 3")
    page.keyboard.press("Escape")
    page.wait_for_function("document.querySelectorAll('[data-conversation-commands]').length === 0")
    assert box.input_value() == "/pl"

    # A space is the person moving on to what they are asking for, so the menu goes.
    box.fill("")
    box.type("/pl")
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 3")
    page.keyboard.type(" ")
    page.wait_for_function("document.querySelectorAll('[data-conversation-commands]').length === 0")

    # The mouse highlights and takes rows exactly as the keys do.
    box.fill("")
    box.type("/")
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 4")
    page.locator('[data-conversation-command="compact"]').hover()
    page.wait_for_function(
        "document.querySelector('[data-conversation-command-active]')"
        ".dataset.conversationCommand === 'compact'"
    )
    page.locator('[data-conversation-command="compact"]').click()
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value === '/compact '"
    )

    # A command is a line that starts with a slash, not a message that does: one written
    # under something already written is offered the same menu, and replaces only itself.
    box.fill("")
    box.type("first line")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("/co")
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 1")
    assert listed() == ["compact"], listed()
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelector('[data-conversation-input]').value.endsWith('/compact ')"
    )
    assert page.evaluate("document.querySelector('[data-conversation-input]').value") == (
        "first line\\n/compact "
    )

    # A filter nothing matches says so, rather than vanishing as if nothing were typed.
    box.fill("")
    box.type("/zzz")
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation-commands-empty]').length === 1"
    )
    assert page.locator("[data-conversation-command]").count() == 0
    matched_nothing = page.locator("[data-conversation-commands-empty]").inner_text()
    assert "No command matches" in matched_nothing, matched_nothing

    # The list is the agent's own, and an agent can genuinely report the same name twice —
    # a project command shadowing a user one. Both rows are drawn, rather than the menu
    # throwing on a repeated name and taking the composer down with it.
    page.evaluate(
        'window.__setCommands(['
        '{ name: "review", description: "the project one", argument_hint: null },'
        '{ name: "review", description: "the one in your home directory", argument_hint: null }'
        '])'
    )
    box.fill("")
    box.type("/rev")
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 2")
    assert listed() == ["review", "review"], listed()
    assert "the one in your home directory" in page.locator(
        "[data-conversation-commands]"
    ).inner_text()

    # The menu belongs to the box. Somebody who has clicked away to read the thread is not
    # writing a command, so it stops floating over what they went there to read — and what
    # they had typed is still in the box when they come back to it.
    page.locator("[data-conversation-alive]").click()
    page.wait_for_function("document.querySelectorAll('[data-conversation-commands]').length === 0")
    assert box.input_value() == "/rev"
    box.click()
    page.wait_for_function("document.querySelectorAll('[data-conversation-command]').length === 2")

    # Nothing to offer says only that. An empty list is an agent that reports none — codex
    # reports none — and an agent that has not reported yet, and this page cannot tell them
    # apart, so it does not say which of the two it is.
    page.evaluate("window.__setCommands([])")
    box.fill("")
    box.type("/")
    page.wait_for_function(
        "document.querySelectorAll('[data-conversation-commands-empty]').length === 1"
    )
    nothing_to_offer = page.locator("[data-conversation-commands-empty]").inner_text()
    assert "No commands here." in nothing_to_offer, nothing_to_offer
    assert "agent" not in nothing_to_offer.lower(), nothing_to_offer
    assert page.evaluate("window.__sends().length") == sent_before_the_menu

    browser.close()

print("conversation pane runtime assertions passed")
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
  assert.match(output, /conversation pane runtime assertions passed/);
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

console.log("conversation-pane.test.mjs: all assertions passed");
