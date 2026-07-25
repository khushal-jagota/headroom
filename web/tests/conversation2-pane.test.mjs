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
  "NewConversationForm.svelte",
  "PermissionAskActions.svelte",
  "PermissionAskCard.svelte",
  "ToolCallRow.svelte",
  "WorkLog.svelte"
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

// Nothing in the new pane reaches into the components serving production today.
const ALLOWED_ACP_LEAF = "../../lib/acp/stepIcons";
for (const [fileName, source] of Object.entries(sources)) {
  const reaches = [...source.matchAll(/["'][^"']*(?:lib\/acp\/|components\/acp\/)[^"']*["']/g)]
    .map((found) => found[0].slice(1, -1))
    .filter((specifier) => specifier !== ALLOWED_ACP_LEAF);
  assert.deepEqual(reaches, [], `${fileName} may reuse only the wire-agnostic step glyphs`);
}
assert.doesNotMatch(routeSource, /lib\/acp\/|components\/acp\//);

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

// Reconnecting is the ordinary read, not a whole-screen refetch.
assert.match(routeSource, /visibilitychange/);
assert.match(routeSource, /stream\?\.connect\(\)/);

// The pane never shows a turn running on the rows alone: it reconciles them with what
// the system says about itself, and asks again every time it reconnects.
assert.match(routeSource, /conversationLiveness/);
assert.doesNotMatch(
  routeSource,
  /conversationIsRunning/,
  "the rows alone cannot say a turn stopped without an ending"
);
assert.match(routeSource, /turnStoppedWithoutAnEnding/);
assert.match(routeSource, /\(\) => void refreshView\(\)/);

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
      'export { default as WorkLog } from "../src/components/conversation2/WorkLog.svelte";',
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
  const { AskActions, AskCard, BackendCard, Composer, Transcript, WorkLog } = await import(
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
        mode: "steer"
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
  assert.match(mixedThread, /worked for 3s · 1 tool call/);
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
          mode: "run_when_free"
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

  const completedThread = drawn(Transcript, {
    rows: [
      { key: "e1", kind: "turn_ended", sequence: 1, createdAt: 1_000, ending: "completed", errorSummary: null }
    ]
  });
  assert.doesNotMatch(completedThread, /turn complete/, "a turn that simply finished says nothing");

  // FINDING 6: while a turn runs, one line is what is happening; the rest wait behind a count.
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
      status,
      progress: null
    };
  }
  const runningWork = drawn(WorkLog, { entries: [toolRow(1), toolRow(2), toolRow(3)], settled: false });
  assert.match(runningWork, /\+2 previous tool calls/);
  assert.equal(
    (runningWork.match(/data-conversation2-tool="/g) ?? []).length,
    1,
    "exactly one activity line while the turn runs"
  );
  assert.match(runningWork, /Tool 3/, "and it is the newest one");
  assert.doesNotMatch(runningWork, /Tool 1/);

  const oneEntryWork = drawn(WorkLog, { entries: [toolRow(1)], settled: false });
  assert.doesNotMatch(oneEntryWork, /previous tool call/, "nothing is hidden when nothing is behind");

  // Settled: the whole log folds to a line, and no tool row is in the thread at all.
  const settledWork = drawn(WorkLog, {
    entries: [toolRow(1), toolRow(2)],
    settled: true,
    durationSeconds: 80
  });
  assert.match(settledWork, /worked for 1m 20s · 2 tool calls/);
  assert.match(settledWork, /data-conversation2-work-settled="true"/);
  assert.equal((settledWork.match(/data-conversation2-tool="/g) ?? []).length, 0);

  // A row's own output is behind the row, capped, and never pasted into the thread.
  const withOutput = drawn(WorkLog, {
    entries: [toolRow(1, "completed", "line one\nline two\nline three")],
    settled: false
  });
  assert.match(withOutput, /aria-expanded="false"/, "a tool row starts closed");
  assert.doesNotMatch(withOutput, /line three/, "its output is not in the thread");
  const withSummary = drawn(WorkLog, {
    entries: [toolRow(1, "completed", "ls -la /tmp")],
    settled: false
  });
  assert.match(withSummary, /data-conversation2-tool-summary/);
  assert.match(withSummary, /ls -la \/tmp/, "a one-line detail is the summary itself");

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
  assert.doesNotMatch(hermesPicker, /data-conversation2-picker-effort/);
  assert.match(hermesPicker, /data-conversation2-picker-model/, "hermes still picks a model");

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
      mode: "run_when_free" as const
    },
    toolRow(2, "first output line\\nsecond output line"),
    toolRow(3, null),
    toolRow(4, null),
    {
      key: "e9",
      kind: "turn_ended" as const,
      sequence: 9,
      createdAt: 1012,
      ending: "completed" as const,
      errorSummary: null
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
  current={{ model: "opus", reasoningEffort: "high" }}
  models={[{ model_id: "opus", display_name: "Opus" }, { model_id: "sonnet", display_name: "Sonnet" }]}
  effortOptions={["low", "high"]}
  {onSend}
/>

<ConversationTranscript rows={settledRows} ownSenderLabel="owner" />

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
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="networkidle")

    # FINDING 4 — the selectors are inline, and they show what the conversation runs on.
    model = page.locator("[data-conversation2-picker-model]")
    effort = page.locator("[data-conversation2-picker-effort]")
    assert model.input_value() == "opus", model.input_value()
    assert effort.input_value() == "high", effort.input_value()
    assert page.locator("[data-conversation2-picker-armed]").count() == 0

    # Browsing them changes nothing at all: no send, just a pending change that says so.
    model.select_option("sonnet")
    effort.select_option("low")
    assert page.evaluate("window.__sends().length") == 0
    assert page.locator("[data-conversation2-picker-armed]").count() == 1

    # Re-selecting what it already runs on disarms it — abandoning still costs nothing.
    model.select_option("opus")
    effort.select_option("high")
    assert page.evaluate("window.__sends().length") == 0
    assert page.locator("[data-conversation2-picker-armed]").count() == 0

    # Picked again, the change rides the next message and then stops being pending.
    model.select_option("sonnet")
    page.locator("[data-conversation2-input]").fill("go")
    page.locator("[data-conversation2-send]").click()
    page.wait_for_function("window.__sends().length === 1")
    first = page.evaluate("window.__sends()[0]")
    assert first["text"] == "go", first
    assert first["mode"] == "run_when_free", first
    assert first["picked"]["model"] == "sonnet", first

    page.wait_for_function("document.querySelector('[data-conversation2-input]').value === ''")
    assert page.locator("[data-conversation2-picker-armed]").count() == 0
    assert model.input_value() == "opus", "the select falls back to what the conversation runs on"

    # A second message carries no change, because none is pending any more.
    page.locator("[data-conversation2-input]").fill("again")
    page.locator("[data-conversation2-send]").click()
    page.wait_for_function("window.__sends().length === 2")
    second = page.evaluate("window.__sends()[1]")
    assert second["picked"]["model"] is None, second

    # FINDING 6 — a settled turn's work is one line until it is asked for.
    fold = page.locator("[data-conversation2-work-fold]")
    assert fold.count() == 1
    assert "worked for 12s" in fold.inner_text(), fold.inner_text()
    assert page.locator("[data-conversation2-tool]").count() == 0
    assert page.locator('[data-conversation2-row="agent_message"]').count() == 0

    fold.click()
    page.wait_for_function("document.querySelectorAll('[data-conversation2-tool]').length === 3")

    # A tool row's own output is behind the row, and comes back capped and scrolling.
    assert page.locator("[data-conversation2-tool-output]").count() == 0
    page.locator('[data-conversation2-tool="t2"]').click()
    page.wait_for_function("document.querySelectorAll('[data-conversation2-tool-output]').length === 1")
    output = page.locator("[data-conversation2-tool-output]")
    assert "second output line" in output.inner_text(), output.inner_text()
    box = output.bounding_box()
    assert box["height"] <= 400, box

    fold.click()
    page.wait_for_function("document.querySelectorAll('[data-conversation2-tool]').length === 0")

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
