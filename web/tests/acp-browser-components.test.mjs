import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { compile } from "svelte/compiler";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const componentDirectory = new URL("../src/components/acp/", import.meta.url);
const expectedInventory = [
  "AcpComposer.svelte",
  "AcpConversationPane.svelte",
  "ConversationComposer.svelte",
  "ConversationStatus.svelte",
  "DiffView.svelte",
  "PermissionPrompt.svelte",
  "StanzaView.svelte",
  "StepView.svelte",
  "TaskProgressStrip.svelte",
  "TranscriptView.svelte",
];
const inventory = (await readdir(componentDirectory)).filter((name) => name.endsWith(".svelte")).sort();
assert.deepEqual(inventory, expectedInventory);

const sources = {};
for (const fileName of inventory) {
  const source = await readFile(new URL(fileName, componentDirectory), "utf8");
  sources[fileName] = source;
  const compiled = compile(source, { filename: fileName, generate: "client", modernAst: true });
  assert.deepEqual(compiled.warnings, [], `${fileName} must compile without Svelte warnings`);
  assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b|\brgb\(|\bhsl\(|gradient\(|box-shadow|acp-ui|\bReact\b/i);
  for (const style of source.matchAll(/<style>([\s\S]*?)<\/style>/g)) {
    assert.doesNotMatch(
      style[1],
      /(?:padding|margin|gap|border-radius|font-size|transition-duration):\s*\d+(?:\.\d+)?(?:px|rem|em|ms|s)\b/,
      `${fileName} styling must use existing tokens`,
    );
  }
}

assert.match(sources["TranscriptView.svelte"], /MarkdownBlock/);
assert.match(sources["TranscriptView.svelte"], /FilePreview/);
assert.match(sources["TranscriptView.svelte"], /data-acp-programmatic-prompt/);
assert.match(sources["TranscriptView.svelte"], /System message · \{item\.source\}/);
// The compaction seam is a centred, flat divider whose lowercase mono label is
// built from the payload state and trigger (no token counts, no summary).
assert.match(sources["TranscriptView.svelte"], /context compacted · \$\{payload\.trigger\}/);
assert.match(sources["TranscriptView.svelte"], /context compaction failed · \$\{payload\.reason \?\? payload\.trigger\}/);
assert.doesNotMatch(sources["TranscriptView.svelte"], /compaction\.payload\.summary|onCompactionExpanded|aria-controls=`acp-compaction/);
assert.doesNotMatch(sources["AcpConversationPane.svelte"], /setCompactionExpanded|onCompactionExpanded/);
assert.match(sources["AcpComposer.svelte"], /ConversationComposer/);
assert.doesNotMatch(sources["ConversationComposer.svelte"], /clarification|pauseMode|skillSelect/i);
assert.doesNotMatch(sources["AcpConversationPane.svelte"], /WebSocket|backend|\/api\//i);

const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-acp-components-"));
const runtimeMainPath = join(webRoot, "tests", `.acp-component-runtime-${process.pid}.ts`);
const runtimeIndexPath = join(webRoot, "tests", `.acp-component-runtime-${process.pid}.html`);
const runtimePermissionProbePath = join(webRoot, "tests", `.acp-permission-runtime-${process.pid}.py`);
const runtimeReplayProbePath = join(webRoot, "tests", `.acp-replay-runtime-${process.pid}.py`);
let serverProcess;

try {
  await writeFile(runtimeMainPath, `
import { mount } from "svelte";
import AcpConversationPane from "../src/components/acp/AcpConversationPane.svelte";
import { createConversationController, type ConversationController } from "../src/lib/acp/conversationController";
import type { ConversationSnapshot } from "../src/lib/acp/conversationState";

const plan = [{ content: "Ship runtime proof", status: "in_progress", priority: "high" }];
const unbrokenToken = "width".repeat(180);
const tool = {
  toolCallId: "tool-runtime",
  title: "Runtime tool",
  kind: "edit",
  status: "completed",
  expanded: false,
  content: [
    {
      type: "diff", path: "runtime-" + unbrokenToken + ".txt", oldText: "before-" + unbrokenToken, newText: "after-" + unbrokenToken,
      _meta: {
        "https://panels.local/acp/codex-file-edit/v1": {
          operation: "update", detailState: "complete", oldStartLine: 999,
          newStartLine: 999, oldLineCount: 1, newLineCount: 1,
        },
        "https://panels.local/acp/codex-file-edit/v1#9007199254740992": {
          operation: "update", detailState: "complete", oldStartLine: 777,
          newStartLine: 777, oldLineCount: 1, newLineCount: 1,
        },
        "https://panels.local/acp/codex-file-edit/v1#9007199254740993": {
          operation: "update", detailState: "truncated", oldStartLine: 41,
          newStartLine: 51, oldLineCount: null, newLineCount: null,
        },
      },
    },
    {
      type: "diff", path: "added-newline.txt", oldText: null, newText: "one\\n",
      _meta: { "https://panels.local/acp/codex-file-edit/v1": {
        operation: "add", detailState: "complete", oldStartLine: null,
        newStartLine: 7, oldLineCount: 0, newLineCount: 1,
      } },
    },
    {
      type: "diff", path: "deleted-newline.txt", oldText: "one\\n", newText: "",
      _meta: { "https://panels.local/acp/codex-file-edit/v1": {
        operation: "delete", detailState: "complete", oldStartLine: 9,
        newStartLine: null, oldLineCount: 1, newLineCount: 0,
      } },
    },
    { type: "terminal", terminalId: "terminal-runtime" },
  ],
  locations: [],
  rawInput: { command: "runtime-input" },
  rawOutput: { result: "runtime-output" },
};
const longMessages = Array.from({ length: 18 }, (_, index) => ({
  id: "long-runtime-" + index,
  role: index % 2 === 0 ? "user" : "agent",
  timestamp: index + 3,
  parts: [{
    type: "content",
    content: [{
      type: "text",
      text: "Long transcript line " + (index + 1) + " keeps earlier conversation available while permission is pending.",
    }],
  }],
}));
const worstCaseMarkdown = [
  "A long link must wrap inside the shared conversation pane:",
  "https://example.test/" + unbrokenToken,
  "",
  "| output | value |",
  "| --- | --- |",
  "| tool | " + unbrokenToken + " |",
  "",
  "~~~text",
  unbrokenToken,
  "~~~",
].join("\\n");
const messages = [
  {
    id: "human-runtime",
    role: "user",
    timestamp: 1,
    parts: [{ type: "content", content: [
      { type: "text", text: "Human runtime message " + unbrokenToken },
      { type: "resource_link", uri: "https://example.test/" + unbrokenToken, name: unbrokenToken },
      { type: "resource_link", uri: "/files/tickets/t_runtime/runtime-preview.png", name: "Runtime image preview" },
    ] }],
  },
  {
    id: "agent-runtime",
    role: "agent",
    timestamp: 2,
    parts: [
      { type: "thought", thought: [{ type: "text", text: "Private typed thought" }], expanded: false },
      { type: "tool_calls", toolCalls: [tool] },
      { type: "content", content: [
        { type: "text", text: "Agent runtime answer\\n\\n" + worstCaseMarkdown },
        { type: "resource_link", uri: "/files/tickets/t_runtime/runtime-preview.md", name: "Runtime Markdown preview" },
        { type: "resource_link", uri: "/files/tickets/t_runtime/runtime-preview.html", name: "Runtime HTML preview" },
        { type: "image", mimeType: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" },
      ] },
      { type: "plan", plan },
    ],
  },
];
const initialSnapshot = {
  employeeId: "employee-runtime",
  cursor: {
    entityKind: "ticket",
    entityId: "ticket-runtime",
    acpSessionId: "session-runtime",
    bindingGeneration: 1,
    sequence: 22,
  },
  session: {
    messages,
    isStreaming: true,
    pendingToolCalls: { "tool-runtime": tool },
    pendingPermissions: [],
    plan,
    planMessageId: "agent-runtime",
    usage: { used: 42, size: 100 },
    configOptions: [],
    availableCommands: [{ name: "help", description: "Show help" }],
  },
  timeline: [
    { kind: "message", messageId: "human-runtime" },
    { kind: "message", messageId: "agent-runtime" },
    { kind: "compaction", compactionBoundaryId: "compaction-runtime" },
    { kind: "compaction", compactionBoundaryId: "compaction-failed-runtime" },
    { kind: "delivery", deliveryClientMessageId: "older-key" },
    { kind: "delivery", deliveryClientMessageId: "newer-key" },
  ],
  activity: { state: "waiting_for_permission", detail: "Waiting for permission", sequence: 22 },
  connection: { state: "ready", detail: "Ready", supportsSteer: false },
  recoverableConnectionError: null,
  protocolRejections: {},
  unsupportedAgentContent: [],
  currentModeId: null,
  sessionInfo: { title: "Runtime conversation" },
  compactions: {
    "compaction-runtime": {
      payload: {
        boundaryId: "compaction-runtime",
        state: "compacted",
        trigger: "explicit",
      },
    },
    "compaction-failed-runtime": {
      payload: {
        boundaryId: "compaction-failed-runtime",
        state: "failed",
        trigger: "automatic",
        reason: "Exact runtime compaction failure",
      },
    },
  },
  receipts: {
    "older-key": {
      clientMessageId: "older-key",
      choice: "send_now",
      state: "rejected",
      reason: "latest older-key update",
    },
    "newer-key": {
      clientMessageId: "newer-key",
      choice: "queue",
      state: "queued",
      queuePosition: 2,
    },
  },
  latestReceiptClientMessageId: "older-key",
  latestReceiptSequence: 22,
  queue: [{
    clientMessageId: "queue-runtime",
    prompt: { sessionId: "session-runtime", prompt: [{ type: "text", text: "Queued runtime prompt" }] },
    enqueueSequence: 1,
    enqueuedAt: 1,
  }],
  optimisticHumans: {},
  permissions: {
    "permission-runtime": {
      request: {
        requestId: "permission-runtime",
        employeeId: "employee-runtime",
        backendKey: "runtime",
        request: {
          sessionId: "session-runtime",
          toolCall: { toolCallId: "tool-runtime", title: "Approve runtime tool" },
          options: [
            { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
            { optionId: "allow-always", name: "Always allow", kind: "allow_always" },
            { optionId: "reject-once", name: "Reject", kind: "reject_once" },
          ],
        },
        lifecycle: "pending",
        deadlineAt: 1000,
        openedSequence: 22,
      },
      submittingOptionId: null,
    },
  },
  terminalStates: {
    "terminal-runtime": {
      terminalId: "terminal-runtime",
      lifecycle: "released",
      terminalOutput: {
        output: "runtime output " + unbrokenToken,
        truncated: true,
        exitStatus: { exitCode: 0 },
      },
    },
  },
  disposed: false,
} as unknown as ConversationSnapshot;

let current = initialSnapshot;
const listeners = new Set<(snapshot: ConversationSnapshot) => void>();
const actions: Record<string, unknown>[] = [];
const promptAttempts: Record<string, unknown>[] = [];
const revokedObjectUrls: string[] = [];
const revokeObjectURL = URL.revokeObjectURL.bind(URL);
URL.revokeObjectURL = (url: string) => {
  revokedObjectUrls.push(url);
  revokeObjectURL(url);
};
const emit = (next: ConversationSnapshot) => {
  current = next;
  for (const listener of listeners) listener(current);
};
const updateAgentParts = (transform: (part: any, index: number) => any) => current.session.messages.map((message) => (
  message.id === "agent-runtime" ? { ...message, parts: message.parts.map(transform) } : message
));
const controller: ConversationController = {
  attach() { actions.push({ type: "attach" }); },
  snapshot() { return current; },
  subscribe(listener) {
    listeners.add(listener);
    listener(current);
    return () => listeners.delete(listener);
  },
  prompt(blocks, choice) {
    const result = !current.cursor
      ? { ok: false, reason: "Conversation is not ready" }
      : choice === "steer" && !current.connection.supportsSteer
        ? { ok: false, reason: "Steer is unavailable for this employee" }
        : { ok: true, clientMessageId: "runtime-client" };
    const action = {
      type: "prompt",
      text: blocks.filter((block) => block.type === "text").map((block) => block.text).join(""),
      blocks: blocks.map((block) => ({ ...block })),
      choice,
    };
    promptAttempts.push({ ...action, result });
    if (result.ok) {
      actions.push({ type: action.type, text: action.text, choice: action.choice });
    }
    return result;
  },
  cancelActive() { actions.push({ type: "cancelActive" }); return { ok: true }; },
  cancelQueued(clientMessageId) { actions.push({ type: "cancelQueued", clientMessageId }); return { ok: true }; },
  newConversation() { actions.push({ type: "newConversation" }); return { ok: true }; },
  respondToPermission(requestId, optionId) {
    actions.push({ type: "permission", requestId, optionId });
    emit({
      ...current,
      permissions: {
        ...current.permissions,
        [requestId]: { ...current.permissions[requestId], submittingOptionId: optionId },
      },
    });
    return { ok: true };
  },
  setThoughtExpanded(_messageId, partIndex, expanded) {
    emit({
      ...current,
      session: {
        ...current.session,
        messages: updateAgentParts((part, index) => index === partIndex ? { ...part, expanded } : part),
      },
    });
  },
  setToolExpanded(toolCallId, expanded) {
    const nextTool = { ...current.session.pendingToolCalls[toolCallId], expanded };
    emit({
      ...current,
      session: {
        ...current.session,
        pendingToolCalls: { ...current.session.pendingToolCalls, [toolCallId]: nextTool },
        messages: updateAgentParts((part) => part.type === "tool_calls"
          ? { ...part, toolCalls: part.toolCalls.map((item) => item.toolCallId === toolCallId ? nextTool : item) }
          : part),
      },
    });
  },
  dispose() { actions.push({ type: "dispose" }); },
};

(window as any).__acpActions = actions;
(window as any).__acpPromptAttempts = promptAttempts;
(window as any).__acpRevokedObjectUrls = revokedObjectUrls;
(window as any).__setHostWidth = (width: number) => {
  document.getElementById("app")!.style.width = width + "px";
};
(window as any).__loadProductionStyles = () => Promise.all([
  import("../../assets/tokens.css"),
  import("../../assets/app.css"),
]);
(window as any).__setConversationDeliveryState = (
  ready: boolean,
  supportsSteer: boolean,
  active: boolean,
) => emit({
  ...current,
  cursor: ready ? initialSnapshot.cursor : null,
  activity: active ? { state: "working", detail: "Working", sequence: 23 } : null,
  connection: {
    state: ready ? "ready" : "connecting",
    detail: ready ? "Ready" : "Connecting",
    supportsSteer,
  },
} as ConversationSnapshot);
(window as any).__setConversationPlan = (statuses: Array<"pending" | "in_progress" | "completed">) => emit({
  ...current,
  session: {
    ...current.session,
    plan: statuses.map((status, index) => ({
      content: "Runtime task " + (index + 1),
      priority: "high",
      status,
    })),
  },
} as ConversationSnapshot);
(window as any).__raiseConnectionError = () => emit({
  ...current,
  recoverableConnectionError: "Runtime connection failed",
});
(window as any).__raiseProtocolError = () => emit({
  ...current,
  recoverableConnectionError: null,
  protocolRejections: {
    ...current.protocolRejections,
    "30": {
      sequence: 30,
      rejectedSessionUpdate: "future_runtime_update",
      reason: "Runtime unsupported update",
      status: "Agent sent an unsupported update",
    },
  },
  timeline: [...current.timeline, { kind: "protocol_rejection", protocolRejectionSequence: 30 }],
});
(window as any).__showPermissionNonDiffContent = () => emit({
  ...current,
  permissions: {
    ...current.permissions,
    "permission-runtime": {
      ...current.permissions["permission-runtime"],
      request: {
        ...current.permissions["permission-runtime"].request,
        request: {
          ...current.permissions["permission-runtime"].request.request,
          toolCall: {
            ...current.permissions["permission-runtime"].request.request.toolCall,
            content: [{
              type: "content",
              content: { type: "text", text: "NON-DIFF PERMISSION CONTENT MUST STAY HIDDEN" },
            }],
          },
        },
      },
    },
  },
});
(window as any).__showPermissionDiff = () => emit({
  ...current,
  session: {
    ...current.session,
    messages: [...current.session.messages, ...longMessages],
  },
  timeline: [
    ...current.timeline,
    ...longMessages.map((message) => ({ kind: "message", messageId: message.id })),
  ],
  permissions: {
    ...current.permissions,
    "permission-runtime": {
      ...current.permissions["permission-runtime"],
      request: {
        ...current.permissions["permission-runtime"].request,
        request: {
          ...current.permissions["permission-runtime"].request.request,
          toolCall: {
            ...current.permissions["permission-runtime"].request.request.toolCall,
            content: [
              {
                type: "content",
                content: { type: "text", text: "ARBITRARY PERMISSION CONTENT MUST STAY HIDDEN" },
              },
              {
                type: "diff",
                path: "/workspace/first.txt",
                oldText: ["first before", ...Array.from({ length: 80 }, (_, index) => "shared " + index)].join("\\n") + "\\n",
                newText: ["first after", ...Array.from({ length: 80 }, (_, index) => "shared " + index)].join("\\n") + "\\n",
                _meta: { "https://panels.local/acp/codex-file-edit/v1": {
                  operation: "update", detailState: "truncated", oldStartLine: 101,
                  newStartLine: 201, oldLineCount: null, newLineCount: null,
                } },
              },
              { type: "terminal", terminalId: "permission-terminal-must-stay-hidden" },
              {
                type: "diff",
                path: "/workspace/second.txt",
                oldText: null,
                newText: "",
                _meta: { "https://panels.local/acp/codex-file-edit/v1": {
                  operation: "add", detailState: "omitted", oldStartLine: null,
                  newStartLine: null, oldLineCount: null, newLineCount: null,
                } },
              },
              {
                type: "diff", path: "/workspace/added-newline.txt",
                oldText: null, newText: "one\\n",
                _meta: { "https://panels.local/acp/codex-file-edit/v1": {
                  operation: "add", detailState: "complete", oldStartLine: null,
                  newStartLine: 7, oldLineCount: 0, newLineCount: 1,
                } },
              },
              {
                type: "diff", path: "/workspace/deleted-newline.txt",
                oldText: "one\\n", newText: "",
                _meta: { "https://panels.local/acp/codex-file-edit/v1": {
                  operation: "delete", detailState: "complete", oldStartLine: 9,
                  newStartLine: null, oldLineCount: 1, newLineCount: 0,
                } },
              },
            ],
          },
        },
      },
    },
  },
});

mount(AcpConversationPane, {
  target: document.getElementById("app")!,
  props: { controller, employeeLabel: "Runtime employee" },
});

class ManualReplayTransport {
  callbacks: any;
  sent: any[] = [];
  constructor(callbacks: any) { this.callbacks = callbacks; }
  open() {}
  send(action: any) { this.sent.push(action); return { ok: true as const }; }
  close() {}
  detach() {}
}
let replayTransport: ManualReplayTransport;
const replayEnvelope = (sequence: number, type: string, payload: any) => ({
  wireVersion: 1,
  employeeId: "employee-replay-runtime",
  entityKind: "ticket",
  entityId: "ticket-replay-runtime",
  acpSessionId: "session-replay-runtime",
  bindingGeneration: 1,
  sequence,
  type,
  payload,
});
(window as any).__mountReplay = () => {
  document.body.innerHTML = '<div id="app-replay"></div>';
  const replayController = createConversationController({
    employeeId: "employee-replay-runtime",
    transportFactory: (callbacks) => (replayTransport = new ManualReplayTransport(callbacks)),
    reconnectDelayMs: 25,
    setTimer: (callback) => window.setTimeout(callback, 25),
    clearTimer: (timer) => window.clearTimeout(timer as number),
    now: () => 1000,
    fallbackId: (turn, role, segment) => "replay-fallback-" + turn + "-" + role + "-" + segment,
    clientMessageId: () => "replay-client",
  });
  mount(AcpConversationPane, {
    target: document.getElementById("app-replay")!,
    props: { controller: replayController, employeeLabel: "Replay runtime employee" },
  });
};
(window as any).__openReplay = () => replayTransport.callbacks.onOpen();
(window as any).__feedReplayReset = () => replayTransport.callbacks.onEnvelope(replayEnvelope(1, "connection", {
  state: "reset", detail: "Loaded", supportsSteer: true, resetBindingGeneration: 1,
}));
(window as any).__feedReplayMessage = (sequence: number) => replayTransport.callbacks.onEnvelope(replayEnvelope(
  sequence,
  "human_echo",
  {
    clientMessageId: "replay-human-" + sequence,
    prompt: {
      sessionId: "session-replay-runtime",
      prompt: [{ type: "text", text: "Replay line " + sequence + " contains enough text to occupy visible transcript space." }],
    },
  },
));
(window as any).__feedReplayReady = (sequence: number) => replayTransport.callbacks.onEnvelope(replayEnvelope(
  sequence, "connection", { state: "ready", detail: "Ready", supportsSteer: true },
));
`);
  await writeFile(runtimeIndexPath, `<!doctype html><html><head><style>html, body { margin: 0; } #app { display: flex; width: 100%; height: 620px; min-width: 0; min-height: 0; } #app-replay { display: flex; width: 100%; height: 320px; min-width: 0; min-height: 0; }</style></head><body><div id="app"></div><script type="module" src="./${runtimeMainPath.split("/").at(-1)}"></script></body></html>`);
  await build({
    root: webRoot,
    base: "./",
    configFile: false,
    logLevel: "silent",
    plugins: [svelte()],
    build: {
      emptyOutDir: true,
      outDir: temporaryDirectory,
      rollupOptions: { input: { index: runtimeIndexPath } },
    },
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" },
  );
  const builtHtml = (await readdir(temporaryDirectory, { recursive: true }))
    .find((path) => path.endsWith(".html"));
  assert.ok(builtHtml, "the runtime component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtHtml}`;
  await waitUntilReady(url);
  const browserProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    [join(repositoryRoot, "tests", "support", "acp_component_runtime.py"), url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  browserProbe.stdout.on("data", (chunk) => { output += chunk; });
  browserProbe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => browserProbe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /mounted interactions and accessibility assertions passed/);

  await writeFile(runtimePermissionProbePath, `
from playwright.sync_api import sync_playwright
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="networkidle")
    page.evaluate("window.__loadProductionStyles()")

    permission = page.locator("[data-acp-permission='permission-runtime']")
    # No diff content on the wire yet, so the sunken diff well is absent.
    assert permission.locator("[data-acp-diff]").count() == 0
    assert permission.locator(".acp-permission-title").inner_text() == "Approve runtime tool"
    # Redesign: reject options gather left as ghost buttons, allow options right,
    # the last allow is the filled primary. No status line anywhere.
    option_group = permission.get_by_role("group", name="Permission options")
    options = option_group.get_by_role("button")
    assert options.all_inner_texts() == ["Reject", "Allow once", "Always allow"]
    assert [options.nth(index).get_attribute("data-permission-kind") for index in range(3)] == [
        "reject_once", "allow_once", "allow_always"
    ]
    assert permission.locator(".acp-permission-reject").all_inner_texts() == ["Reject"]
    assert permission.locator(".acp-permission-allow.primary").inner_text() == "Always allow"
    assert permission.locator(".acp-permission-status").count() == 0

    page.evaluate("window.__showPermissionNonDiffContent()")
    # Non-diff tool content stays out of the prompt entirely.
    assert permission.locator("[data-acp-diff]").count() == 0
    assert page.get_by_text("NON-DIFF PERMISSION CONTENT MUST STAY HIDDEN").count() == 0

    page.evaluate("window.__showPermissionDiff()")
    diffs = permission.locator("[data-acp-diff]")
    assert diffs.count() == 4
    assert diffs.locator("figcaption").all_inner_texts() == [
        "/workspace/first.txt", "/workspace/second.txt",
        "/workspace/added-newline.txt", "/workspace/deleted-newline.txt"
    ]
    first = diffs.nth(0)
    assert first.get_by_role("table", name="Line changes for /workspace/first.txt").count() == 1
    assert first.get_by_role("cell", name="Deleted", exact=True).count() == 1
    assert first.get_by_role("cell", name="Added", exact=True).count() == 1
    assert first.get_by_text("first before", exact=True).count() == 1
    assert first.get_by_text("first after", exact=True).count() == 1
    assert first.get_by_text("Some edit detail was truncated", exact=True).count() == 1
    assert first.locator("[role='row']").nth(0).locator("[role='cell']").nth(0).inner_text() == "101"
    assert first.locator("[role='row']").nth(1).locator("[role='cell']").nth(1).inner_text() == "201"
    second = diffs.nth(1)
    assert second.get_by_role("table", name="Line changes for /workspace/second.txt").count() == 1
    assert second.locator("[role='row']").count() == 0
    assert second.get_by_text("Edit detail was omitted", exact=True).count() == 1
    added = diffs.nth(2)
    deleted = diffs.nth(3)
    assert added.locator("[role='row']").count() == 1
    assert added.get_by_role("cell", name="Added", exact=True).count() == 1
    assert deleted.locator("[role='row']").count() == 1
    assert deleted.get_by_role("cell", name="Deleted", exact=True).count() == 1
    assert page.get_by_text("ARBITRARY PERMISSION CONTENT MUST STAY HIDDEN").count() == 0
    assert page.get_by_text("permission-terminal-must-stay-hidden").count() == 0
    geometry = page.evaluate("""() => {
        const pane = document.querySelector('[data-acp-conversation-pane]');
        const thread = document.querySelector('[data-chat-messages]');
        return {
            viewport: document.documentElement.clientWidth,
            documentScroll: document.documentElement.scrollWidth,
            paneClient: pane.clientWidth,
            paneScroll: pane.scrollWidth,
            threadClient: thread.clientWidth,
            threadScroll: thread.scrollWidth,
        };
    }""")
    assert geometry["documentScroll"] <= geometry["viewport"], geometry
    assert geometry["paneScroll"] <= geometry["paneClient"], geometry
    assert geometry["threadScroll"] <= geometry["threadClient"], geometry

    # A long permission diff is ordinary content in the existing transcript
    # scroller. It must not collapse the usable chat viewport in this fixed-height
    # pane, and the transcript must remain scrollable while approval is pending.
    thread = page.locator("[data-chat-messages]")
    assert thread.evaluate("element => element.clientHeight") >= 160
    assert thread.evaluate("element => element.scrollHeight > element.clientHeight")
    assert page.evaluate("""() => document.querySelector('[data-chat-messages]').contains(
        document.querySelector('[data-acp-permission="permission-runtime"]')
    )""")

    # Selecting the filled primary (last allow) disables every option while the
    # decision is in flight, with opacity only — no status text appears.
    options = option_group.get_by_role("button")
    primary = permission.locator(".acp-permission-allow.primary")
    primary.scroll_into_view_if_needed()
    assert primary.is_visible()
    primary.click()
    assert primary.get_attribute("aria-pressed") == "true"
    assert all(options.nth(index).is_disabled() for index in range(3))
    assert permission.locator(".acp-permission-status").count() == 0
    assert {
        "type": "permission",
        "requestId": "permission-runtime",
        "optionId": "allow-always",
    } in page.evaluate("window.__acpActions")
    browser.close()

print("acp_permission_runtime.py: diff and no-diff assertions passed")
`);
  const permissionProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    [runtimePermissionProbePath, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let permissionOutput = "";
  permissionProbe.stdout.on("data", (chunk) => { permissionOutput += chunk; });
  permissionProbe.stderr.on("data", (chunk) => { permissionOutput += chunk; });
  const permissionExitCode = await new Promise((resolve) => permissionProbe.on("close", resolve));
  assert.equal(permissionExitCode, 0, permissionOutput);
  assert.match(permissionOutput, /diff and no-diff assertions passed/);

  await writeFile(runtimeReplayProbePath, `
from playwright.sync_api import sync_playwright
import sys

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(5_000)
    page.goto(sys.argv[1], wait_until="networkidle")
    page.evaluate("window.__loadProductionStyles()")
    page.evaluate("window.__mountReplay()")
    thread = page.locator("#app-replay [data-chat-messages]")

    def settle():
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

    def dimensions():
        return thread.evaluate("element => ({ height: element.scrollHeight, client: element.clientHeight, bottom: element.scrollHeight - element.scrollTop - element.clientHeight })")

    page.evaluate("window.__openReplay()")
    page.evaluate("window.__feedReplayReset()")
    settle()
    initial = dimensions()
    pre_ready_increases = []
    previous = initial["height"]
    for sequence in range(2, 32):
        page.evaluate("sequence => window.__feedReplayMessage(sequence)", sequence)
        settle()
        current = dimensions()
        if current["height"] > previous:
            pre_ready_increases.append((previous, current["height"], current["bottom"]))
        previous = current["height"]

    assert pre_ready_increases == [], f"RED: replay painted before ready: {pre_ready_increases}"
    page.evaluate("window.__feedReplayReady(32)")
    settle()
    ready = dimensions()
    assert ready["height"] > initial["height"]
    assert ready["height"] > ready["client"], "production styles must create a genuinely overflowing transcript"
    assert ready["bottom"] <= 1, ready
    assert thread.locator("[data-acp-message]").count() == 30

    page.evaluate("window.__feedReplayMessage(33)")
    settle()
    live = dimensions()
    assert live["height"] > ready["height"], "post-ready live rendering remains incremental"
    assert live["bottom"] <= 1, live
    browser.close()

print("acp_replay_runtime.py: atomic replay presentation assertions passed")
`);
  const replayProbe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    [runtimeReplayProbePath, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let replayOutput = "";
  replayProbe.stdout.on("data", (chunk) => { replayOutput += chunk; });
  replayProbe.stderr.on("data", (chunk) => { replayOutput += chunk; });
  const replayExitCode = await new Promise((resolve) => replayProbe.on("close", resolve));
  assert.equal(replayExitCode, 0, replayOutput);
  assert.match(replayOutput, /atomic replay presentation assertions passed/);
} finally {
  serverProcess?.kill();
  await rm(runtimeMainPath, { force: true });
  await rm(runtimeIndexPath, { force: true });
  await rm(runtimePermissionProbePath, { force: true });
  await rm(runtimeReplayProbePath, { force: true });
  await rm(temporaryDirectory, { recursive: true, force: true });
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

console.log("acp-browser-components.test.mjs: all assertions passed");
