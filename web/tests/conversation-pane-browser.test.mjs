/**
 * Browser-only conversation layer contracts.
 *
 * This builds a small Svelte host around the production ConversationPane, then serves
 * only those static assets. No Panels server or API participates. The host supplies the
 * page-owned outside-dismiss gesture that TicketRoute gives the pane in production.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";
import { scratchDirectory } from "./support/scratch.mjs";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = join(webRoot, "..");
const temporaryDirectory = await mkdtemp(join(tmpdir(), "panels-conversation-layer-"));
const scratchRoot = await scratchDirectory();
const hostPath = join(scratchRoot, `conversation-layer-host-${process.pid}.svelte`);
const mainPath = join(scratchRoot, `conversation-layer-main-${process.pid}.ts`);
const indexPath = join(scratchRoot, `conversation-layer-index-${process.pid}.html`);
let serverProcess;

try {
  await writeFile(
    hostPath,
    String.raw`<script lang="ts">
  import { tick } from "svelte";
  import ConversationPane from "../src/components/conversation/ConversationPane.svelte";
  import { discardHeldPrompt, promoteHeldPrompt } from "../src/lib/conversation/wire";
  import type { ConversationState } from "../src/lib/conversation/conversationState";

  const words = (index: number) =>
    ("Line " + index + ": the browser keeps this distinct paragraph anchored while the conversation changes height. ").repeat(3);
  let rows = $state<any[]>(
    Array.from({ length: 28 }, (_, index) => ({
      key: "message-" + index,
      kind: "agent_message",
      sequence: index + 1,
      createdAt: 1_000 + index,
      content: [{ piece: "text", text: words(index) }]
    }))
  );
  let visibleRows = $state<any[] | null>(null);
  let nextRowIndex = 28;
  let scrollReplacementGeneration = 0;
  let running = $state(false);
  let ownerReadThroughSequence = $state(0);
  let heldPromptRows = $state<any[]>([]);
  let supportsSteer = $state(false);
  let outgoingMessages = $state<any[]>([]);
  let composerDisabled = $state(false);
  let ask = $state<any>(null);

  // The connected test proves real writers and automatic SSE. This former E2E
  // matrix keeps its mock responses, exact request modes, and visibility refresh.
  let queueMatrixEnabled = $state(false);
  async function refreshQueueMatrix(): Promise<void> {
    if (!queueMatrixEnabled) return;
    const response = await fetch("/api/test/queue-matrix");
    heldPromptRows = await response.json();
  }
  async function actOnQueueMatrix(heldPromptId: string, mode: "discard" | "steer" | "send_now"): Promise<void> {
    if (mode === "discard") await discardHeldPrompt("browser-fixture", heldPromptId);
    else await promoteHeldPrompt("browser-fixture", heldPromptId, mode);
    await refreshQueueMatrix();
  }
  document.addEventListener("visibilitychange", () => { void refreshQueueMatrix(); });
  (window as any).__loadQueueMatrix = async () => {
    queueMatrixEnabled = true;
    supportsSteer = true;
    running = true;
    conversationState = "opened";
    await refreshQueueMatrix();
  };

  // Enough command entries to fill the menu, and enough shared letters to narrow it.
  const composerCatalog = [
    { kind: "command", display_text: "/recap", insertion_text: "/recap ",
      description: "Recap the ticket", argument_hint: null },
    { kind: "command", display_text: "/release", insertion_text: "/release ",
      description: "Cut a release", argument_hint: null },
    { kind: "command", display_text: "/rename", insertion_text: "/rename ",
      description: "Rename the thing", argument_hint: null },
    { kind: "command", display_text: "/reset", insertion_text: "/reset ",
      description: "Reset local state", argument_hint: null },
    { kind: "command", display_text: "/review", insertion_text: "/review ",
      description: "Review the current diff", argument_hint: null },
    { kind: "command", display_text: "/start", insertion_text: "/start ",
      description: "Start the worker", argument_hint: null },
    { kind: "command", display_text: "/status", insertion_text: "/status ",
      description: "Show the status", argument_hint: null },
    { kind: "command", display_text: "/stop", insertion_text: "/stop ",
      description: "Stop the worker", argument_hint: null }
  ];
  let conversationState = $state<ConversationState>("rest");
  let lens = $state<"focus" | "full">("focus");

  async function captureSend(content: any[], mode: string, picked: any): Promise<boolean> {
    (window as any).__sentModes = [...((window as any).__sentModes ?? []), mode];
    (window as any).__sentRuns = [...((window as any).__sentRuns ?? []), picked];
    heldPromptRows = [{
      key: "composer-fallback",
      heldPromptId: "composer-fallback",
      content,
      state: "held",
      queueReason: picked.model === null ? "steer_refused" : "run_change"
    }];
    return true;
  }

  function dismissConversation(): void {
    conversationState = "rest";
  }

  function dismissBesidePane(event: MouseEvent): void {
    const pressed = event.target;
    if (!(pressed instanceof Element)) return;
    if (pressed.closest("[data-conversation-pane]") !== null) return;
    dismissConversation();
  }

  (window as any).__appendConversationLine = () => {
    const index = nextRowIndex++;
    rows = [
      ...rows,
      {
        key: "message-" + index,
        kind: "agent_message",
        sequence: index + 1,
        createdAt: 1_000 + index,
        content: [{ piece: "text", text: "Newest appended line " + index }]
      }
    ];
  };
  // Controlled content replay. Responses stay pending while the reader chooses a
  // position, then browser-owned image/Markdown completion changes the same rows.
  (window as any).__setupScrollRegression = (kind: string) => {
    conversationState = "peeked";
    lens = "full";
    running = false;
    const paragraphs = Array.from({ length: 32 }, (_, index) =>
      "Tall paragraph " + index + ": " + words(index)).join("\n\n");
    rows = Array.from({ length: 28 }, (_, index) => ({
      key: "regression-" + index,
      kind: "agent_message",
      sequence: index + 1,
      createdAt: 1_000 + index,
      content: index === 12 && kind !== "plain"
        ? [
            kind === "image"
              ? {piece: "image", stored_file_id: "delayed.png", file_name: "delayed.png"}
              : {piece: "text", text: "[Delayed Markdown](/files/tickets/t_scrollregression/delayed.md)"},
            {piece: "text", text: paragraphs}
          ]
        : [{piece: "text", text: words(index)}]
    }));
    visibleRows = null;
  };
  (window as any).__replaceAllScrollRows = (count = rows.length) => {
    scrollReplacementGeneration += 1;
    rows = rows.slice(0, count).map((row, index) => ({...row, key: "replacement-" + scrollReplacementGeneration + "-" + index,
      content: [{piece: "text", text: "Replacement " + words(index)}]}));
  };
  (window as any).__sendScrollRegression = (moveLayer: boolean) => {
    outgoingMessages = [{messageId: "scroll-send", content: [{piece: "text", text: "Regression prompt"}],
      senderLabel: "owner", mode: "queue", sentAtUnixMilliseconds: 20_000, knownFate: "nothing_yet"}];
    if (moveLayer) conversationState = "opened";
  };
  (window as any).__answerScrollRegression = (paragraphCount: number) => {
    const prompt = {key: "scroll-prompt", kind: "prompt", sequence: 1_000, createdAt: 20_000,
      content: [{piece: "text", text: "Regression prompt"}], senderLabel: "owner", mode: "queue",
      senderMessageId: "scroll-send", sentAtUnixMilliseconds: 20_000};
    rows = [...rows.filter(row => row.sequence < 1_000), prompt,
      {key: "scroll-reply", kind: "agent_message", sequence: 1_001, createdAt: 20_001,
        content: [{piece: "text", text: Array.from({length:paragraphCount},(_,index) => words(index)).join("\n\n")}]}];
    outgoingMessages = [];
  };
  (window as any).__proseDelta = (index: number) => {
    rows = rows.map((row, at) => at === rows.length - 1
      ? {...row, content: [{piece: "text", text: row.content[0].text + "\n\nControlled prose delta " + index + ": " + words(index)}]}
      : row);
  };
  (window as any).__dropFirstConversationLines = (count: number) => {
    rows = rows.slice(count);
  };
  (window as any).__growFirstConversationLine = () => {
    rows = rows.map((row, index) =>
      index === 0
        ? {
            ...row,
            content: [{
              piece: "text",
              text: ("This existing line became much taller above the reader. ").repeat(45)
            }]
          }
        : row
    );
  };
  (window as any).__rememberConversationInput = () => {
    (window as any).__conversationInput = document.querySelector("[data-conversation-input]");
  };
  (window as any).__conversationInputSurvived = () =>
    (window as any).__conversationInput === document.querySelector("[data-conversation-input]");
  (window as any).__showActivePlan = () => {
    rows = [
      ...rows,
      {
        key: "active-prompt",
        kind: "prompt",
        sequence: 1_000,
        createdAt: 2_000,
        content: [{ piece: "text", text: "Continue" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 2_000
      },
      {
        key: "active-plan",
        kind: "plan_updated",
        sequence: 1_001,
        createdAt: 2_001,
        entries: [
          { text: "Inspect", status: "completed" },
          { text: "Implement", status: "in_progress" },
          { text: "Verify", status: "pending" }
        ]
      }
    ];
    running = true;
  };
  (window as any).__showHeldPrompt = (supported: boolean) => {
    supportsSteer = supported;
    heldPromptRows = [{
      key: "held-1",
      heldPromptId: "held-1",
      content: [{ piece: "text", text: "waiting guidance" }],
      state: "held",
      queueReason: "steer_refused"
    }];
  };
  (window as any).__showUnansweredSend = () => {
    supportsSteer = true;
    heldPromptRows = [{
      key: "local:sent-into-the-dark",
      heldPromptId: null,
      senderMessageId: "sent-into-the-dark",
      content: [{ piece: "text", text: "did this arrive" }],
      senderLabel: "owner",
      sentAtUnixMilliseconds: 10,
      state: "unknown",
      queueReason: null
    }];
  };
  (window as any).__setComposerDisabled = (disabled: boolean) => {
    composerDisabled = disabled;
  };
  (window as any).__settle = async () => {
    await tick();
    await tick();
  };
  (window as any).__showEveryMark = () => {
    ownerReadThroughSequence = 3_000;
    running = true;
    rows = [
      {
        key: "mark-reply",
        kind: "agent_message",
        sequence: 3_001,
        createdAt: 4_000,
        content: [{ piece: "text", text: "the reply nobody has read" }],
        toOwner: true
      },
      {
        key: "mark-end",
        kind: "turn_ended",
        sequence: 3_002,
        createdAt: 4_001,
        ending: "failed",
        errorSummary: "backend exited",
        automaticCompactionResult: null
      }
    ];
    visibleRows = rows;
  };
  (window as any).__showQuietComposer = () => {
    running = false;
    rows = [];
    visibleRows = rows;
  };
  (window as any).__showWorkRunning = () => {
    running = true;
  };
  (window as any).__showRunningAndNeedsYou = () => {
    ownerReadThroughSequence = 3_000;
    running = true;
    (window as any).__permissionAnswers = [];
    ask = {
      askId: "ask-mark",
      title: "may I run this",
      detail: "Write the permission exercise file",
      options: [
        { option_id: "allow-file", label: "Allow once", option_kind: "allow_once" },
        { option_id: "deny-file", label: "Deny once", option_kind: "reject_once" }
      ]
    };
    rows = [
      {
        key: "mark-ask",
        kind: "permission_ask",
        sequence: 3_020,
        createdAt: 4_020,
        askId: "ask-mark",
        title: "may I run this",
        detail: null,
        state: "live",
        deadReason: null,
        chosenOptionId: null
      }
    ];
    visibleRows = rows;
  };
  (window as any).__showOrdinaryOutputOnly = () => {
    ownerReadThroughSequence = 3_000;
    running = false;
    ask = null;
    rows = [
      {
        key: "chatter",
        kind: "agent_message",
        sequence: 3_010,
        createdAt: 4_010,
        content: [{ piece: "text", text: "ordinary output, not a reply" }],
        toOwner: false
      }
    ];
    visibleRows = rows;
  };
  (window as any).__showSettledFocusRestLine = () => {
    rows = [
      {
        key: "settled-prompt",
        kind: "prompt",
        sequence: 2_000,
        createdAt: 3_000,
        content: [{ piece: "text", text: "finish this" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 3_000
      },
      {
        key: "settled-reply",
        kind: "agent_message",
        sequence: 2_001,
        createdAt: 3_001,
        content: [{ piece: "text", text: "settled reply" }]
      },
      {
        key: "settled-end",
        kind: "turn_ended",
        sequence: 2_002,
        createdAt: 3_002,
        ending: "completed",
        errorSummary: null,
        automaticCompactionResult: null
      }
    ];
    visibleRows = rows.slice(0, -1);
    lens = "focus";
    running = false;
    conversationState = "rest";
  };
  (window as any).__showSettledTurn = () => {
    const tool = (index: number) => ({
      key: "settled-tool-" + index,
      kind: "tool_call",
      sequence: 3_100 + index,
      createdAt: 4_100 + index,
      toolCallId: "settled-tool-" + index,
      title: "Tool " + index,
      toolKind: "read",
      detail: null,
      startedDetail: null,
      status: "completed",
      progress: null
    });
    rows = [
      {
        key: "full-prompt",
        kind: "prompt",
        sequence: 3_000,
        createdAt: 4_000,
        content: [{ piece: "text", text: "show everything" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 4_000
      },
      {
        key: "full-commentary",
        kind: "agent_message",
        sequence: 3_001,
        createdAt: 4_001,
        content: [{ piece: "text", text: "hidden commentary" }]
      },
      ...[1, 2, 3, 4].map(tool),
      {
        key: "full-answer",
        kind: "agent_message",
        sequence: 3_200,
        createdAt: 4_200,
        content: [{ piece: "text", text: "final answer" }]
      },
      {
        key: "full-end",
        kind: "turn_ended",
        sequence: 3_201,
        createdAt: 4_201,
        ending: "completed",
        errorSummary: null,
        automaticCompactionResult: null
      }
    ];
    visibleRows = null;
    lens = "focus";
    conversationState = "opened";
  };
  (window as any).__showTurnEndings = () => {
    rows = [
      {
        key: "failed-prompt",
        kind: "prompt",
        sequence: 4_000,
        createdAt: 5_000,
        content: [{ piece: "text", text: "run the backend" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 5_000
      },
      {
        key: "failed-end",
        kind: "turn_ended",
        sequence: 4_001,
        createdAt: 5_001,
        ending: "failed",
        errorSummary: "backend exited",
        automaticCompactionResult: null
      },
      {
        key: "lost-prompt",
        kind: "prompt",
        sequence: 4_002,
        createdAt: 5_002,
        content: [{ piece: "text", text: "turn that loses its ending" }],
        senderLabel: "owner",
        mode: "queue",
        sentAtUnixMilliseconds: 5_002
      },
      {
        key: "turn-stopped",
        kind: "turn_stopped",
        sequence: 4_003,
        createdAt: 5_003
      }
    ];
    visibleRows = null;
    lens = "full";
    running = false;
    conversationState = "opened";
  };
</script>

<main class="fixture-ticket" class:ticket-screen={queueMatrixEnabled}>
  <section class="fixture-document">
    <button type="button" data-ticket-behind onclick={dismissConversation}>Ticket content</button>
  </section>
  <section
    class="conversation-layer"
    data-conversation-layer-host
    onclickcapture={dismissBesidePane}
  >
    <div class="conversation-column">
      <ConversationPane
        bind:conversationState
        bind:lens
        conversationId="browser-fixture"
        conversationExists
        {rows}
        {visibleRows}
        {running}
        {ask}
        onAnswer={(optionId: string) => {
          (window as any).__permissionAnswers.push({ askId: ask.askId, optionId });
          ask = null;
        }}
        {ownerReadThroughSequence}
        {heldPromptRows}
        {supportsSteer}
        backendKey="claude"
        current={{ model: "opus", reasoningEffort: "high" }}
        models={[
          { model_id: "opus", display_name: "Opus", enabled: true },
          { model_id: "sonnet", display_name: "Sonnet", enabled: true,
            reasoning_effort_options: [] }
        ]}
        effortOptions={["high"]}
        {outgoingMessages}
        ownSenderLabel="owner"
        composerDisabled={composerDisabled}
        {composerCatalog}
        onSend={captureSend}
        onDiscardHeldPrompt={(heldPromptId: string) => actOnQueueMatrix(heldPromptId, "discard")}
        onPromoteHeldPrompt={(heldPromptId: string, mode: "steer" | "send_now") => actOnQueueMatrix(heldPromptId, mode)}
        onStopDrawingHeldPrompt={(senderMessageId: string) => {
          (window as any).__stoppedDrawing = senderMessageId;
        }}
        onSendHeldPromptAgain={(senderMessageId: string) => {
          (window as any).__sentAgain = senderMessageId;
        }}
      />
    </div>
  </section>
</main>

<style>
  :global(html), :global(body), :global(#app) {
    height: 100%;
    margin: 0;
  }
  .fixture-ticket {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    height: 720px;
    padding: 16px;
  }
  .fixture-document {
    flex: 1;
    min-height: 0;
  }
  .fixture-document button {
    position: fixed;
    inset: 4px auto auto 4px;
    z-index: 3;
  }
  .conversation-layer {
    align-items: center;
  }
  .fixture-ticket:not(.ticket-screen) .conversation-column {
    box-sizing: border-box;
    width: min(680px, calc(100% - 96px));
  }
  .fixture-ticket.ticket-screen {
    height: 100%;
    padding: 0;
  }
</style>
`,
    "utf8"
  );
  await writeFile(
    mainPath,
    `import "../../assets/tokens.css";\nimport "../../assets/app.css";\nimport { mount } from "svelte";\nimport Host from "./${hostPath.split("/").at(-1)}";\nmount(Host, { target: document.getElementById("app")! });\n`,
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
      outDir: temporaryDirectory,
      rollupOptions: { input: { index: indexPath } }
    }
  });

  const port = await availablePort();
  serverProcess = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", temporaryDirectory],
    { cwd: repositoryRoot, stdio: "ignore" }
  );
  const builtIndex = (await readdir(temporaryDirectory, { recursive: true })).find((path) =>
    path.endsWith(".html")
  );
  assert.ok(builtIndex, "the component build must emit an HTML entry");
  const url = `http://127.0.0.1:${port}/${builtIndex}`;
  await waitUntilReady(url);

  const browserScript = String.raw`
from playwright.sync_api import sync_playwright
from contextlib import contextmanager
from tests.e2e.harness import browser_failure_evidence
import os
import sys
import traceback

# Set this to keep a picture of what the assertions checked. The test proves the same
# thing with or without it.
screenshot_dir = os.environ.get("UNANSWERED_SEND_SCREENSHOT_DIR")
if screenshot_dir:
    os.makedirs(screenshot_dir, exist_ok=True)

PANE = "[data-conversation-pane]"
INPUT = "[data-conversation-input]"
THREAD = "[data-conversation-thread]"

def state(page, expected):
    page.wait_for_selector(f'{PANE}[data-conversation-state="{expected}"]')

def draft(page):
    return page.locator(INPUT).evaluate("""box => ({
        text: box.value,
        start: box.selectionStart,
        end: box.selectionEnd
    })""")

def first_visible_line(page):
    return page.locator(THREAD).evaluate("""thread => {
        const top = thread.getBoundingClientRect().top;
        const rows = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')];
        const row = rows.find(candidate => candidate.getBoundingClientRect().bottom > top + 1);
        return row ? {
          text: row.textContent.trim(),
          top: Math.round(row.getBoundingClientRect().top - top),
          scrollTop: Math.round(thread.scrollTop)
        } : null;
    }""")

def wait_for_anchor(page, held):
    page.wait_for_function("""held => {
      const thread = document.querySelector('[data-conversation-thread]');
      const row = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')]
        .find(candidate => candidate.textContent.trim() === held.text);
      if (!row) return false;
      const top = Math.round(row.getBoundingClientRect().top - thread.getBoundingClientRect().top);
      return Math.abs(top - held.top) <= 3;
    }""", arg=held)
    restored = first_visible_line(page)
    assert restored is not None
    assert restored["text"] == held["text"]
    assert abs(restored["top"] - held["top"]) <= 3, (held, restored)
    return restored

def settled(page):
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

def newest_reading(page):
    return page.locator(THREAD).evaluate("""thread => {
      const row = [...thread.querySelectorAll('[data-conversation-row]')].at(-1);
      const bounds = thread.getBoundingClientRect();
      return {bottom: row.getBoundingClientRect().bottom - bounds.top,
        height: thread.clientHeight, scrollTop: thread.scrollTop,
        latest: !!document.querySelector('button[aria-label="Jump to latest message"]')};
    }""")

def assert_newest(page):
    settled(page)
    reading = newest_reading(page)
    assert -3 <= reading['bottom'] <= reading['height'] + 3, reading
    assert not reading['latest'], reading
    return reading

def visible_paragraph(page):
    return page.locator(THREAD).evaluate("""thread => {
      const top = thread.getBoundingClientRect().top;
      const paragraph = [...thread.querySelectorAll('[data-conversation-row] p')]
        .find(node => node.getBoundingClientRect().bottom > top + 1 && node.getBoundingClientRect().top < top + thread.clientHeight);
      if (!paragraph) throw Error('fixture needs a visible paragraph');
      window.__heldParagraph = paragraph;
      return {text: paragraph.textContent, top: paragraph.getBoundingClientRect().top - top};
    }""")

def assert_paragraph(page, held):
    settled(page)
    actual = page.evaluate("""() => ({connected: window.__heldParagraph.isConnected,
      text: window.__heldParagraph.textContent,
      top: window.__heldParagraph.getBoundingClientRect().top - document.querySelector('[data-conversation-thread]').getBoundingClientRect().top})""")
    assert actual['connected'] and actual['text'] == held['text'], (held, actual)
    assert abs(actual['top'] - held['top']) <= 3, (held, actual)
    assert page.locator('button[aria-label="Jump to latest message"]').is_visible()
    return actual

def prepare_scroll_case(page, url, kind, mode):
    page.goto(url, wait_until='domcontentloaded')
    page.evaluate('(kind) => window.__setupScrollRegression(kind)', kind)
    state(page, 'peeked')
    page.locator(INPUT).fill('\n'.join('Draft line '+str(n) for n in range(6)))
    settled(page)
    if mode == 'scrollback':
        thread = page.locator(THREAD)
        if kind != 'plain':
            # Read INSIDE a message taller than the viewport. Anchoring its outer
            # article would leave this paragraph displaced when its preview grows.
            distance = thread.evaluate("""thread => {
              const paragraph = [...thread.querySelectorAll('p')].find(node => node.textContent.startsWith('Tall paragraph 14:'));
              return paragraph.getBoundingClientRect().top - thread.getBoundingClientRect().top + 9;
            }""")
            thread.hover()
            page.mouse.wheel(0, distance)
        else:
            thread.hover()
            page.mouse.wheel(0, -550)
        page.evaluate("window.__lastRegressionWheel = performance.now()")
        page.wait_for_timeout(80)
        settled(page)
        page.locator('button[aria-label="Jump to latest message"]').wait_for()
        return visible_paragraph(page)
    assert_newest(page)
    return None

def watch_draft(page):
    page.evaluate("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      const input = document.querySelector('[data-conversation-input]');
      const newest = [...thread.querySelectorAll('[data-conversation-row]')].at(-1);
      window.__draftSamples = [];
      let active = true;
      const sample = () => window.__draftSamples.push({scroll:thread.scrollTop,
        newest:newest.getBoundingClientRect().bottom - thread.getBoundingClientRect().top,
        height:input.getBoundingClientRect().height});
      const observer = new ResizeObserver(sample);
      observer.observe(thread);
      thread.addEventListener('scroll', sample);
      const frame = () => {if (active) {sample(); requestAnimationFrame(frame);}};
      frame();
      window.__stopDraftWatch = () => {active=false;sample();observer.disconnect();thread.removeEventListener('scroll',sample);return window.__draftSamples;};
    }""")

def same_line_edits(page):
    box = page.locator(INPUT)
    # Focus directly, so the browser does not scroll the transcript to a locator.
    box.evaluate('input => {input.focus({preventScroll:true});input.setSelectionRange(input.value.length,input.value.length)}')
    for _ in range(3):
        page.keyboard.type('x')
        settled(page)
        page.keyboard.press('Backspace')
        settled(page)

def draft_measurement_case(page, url):
    for mode in ['following', 'scrollback']:
        held = prepare_scroll_case(page, url, 'plain', mode)
        before = page.locator(INPUT).bounding_box()['height']
        watch_draft(page)
        same_line_edits(page)
        samples = page.evaluate('window.__stopDraftWatch()')
        after = page.locator(INPUT).bounding_box()['height']
        assert abs(after - before) <= 0.1, (before, after)
        assert max(item['height'] for item in samples)-min(item['height'] for item in samples) <= 0.1, samples
        assert max(item['scroll'] for item in samples)-min(item['scroll'] for item in samples) <= 3, samples
        assert max(item['newest'] for item in samples)-min(item['newest'] for item in samples) <= 3, samples
        assert page.locator(INPUT).evaluate('input => document.activeElement === input && input.selectionStart === input.value.length && input.selectionEnd === input.value.length')
        if held: assert_paragraph(page, held)
        else: assert_newest(page)

def prose_delta_case(page, url):
    for mode in ['following', 'scrollback']:
        held = prepare_scroll_case(page, url, 'plain', mode)
        for index in range(3):
            same_line_edits(page)
            page.evaluate('(index) => window.__proseDelta(index)', index)
            settled(page)
            if held: assert_paragraph(page, held)
            else: assert_newest(page)

def missing_anchors_case(page, url):
    prepare_scroll_case(page, url, 'plain', 'scrollback')
    before = newest_reading(page)['scrollTop']
    page.evaluate("window.__replaceAllScrollRows()")
    settled(page)
    after = newest_reading(page)
    assert abs(after['scrollTop'] - before) <= 3, (before, after)
    assert after['latest'], after
    held = visible_paragraph(page)
    page.evaluate("window.__proseDelta(0)")
    assert_paragraph(page, held)
    # All original candidates disappear again, and the saved position now exceeds
    # the shortened transcript's range. Clamp there, then retain scrollback intent.
    before = newest_reading(page)['scrollTop']
    page.evaluate('window.__replaceAllScrollRows(20)')
    settled(page)
    limit = page.locator(THREAD).evaluate('thread => thread.scrollHeight-thread.clientHeight')
    actual = newest_reading(page)['scrollTop']
    assert before > limit and abs(actual-limit) <= 3, (before,limit,actual)
    held = visible_paragraph(page)
    page.evaluate('window.__appendConversationLine();window.__appendConversationLine();window.__appendConversationLine()')
    assert_paragraph(page,held)

def layer_send_case(page, url):
    held = prepare_scroll_case(page, url, 'plain', 'scrollback')
    page.evaluate('window.__sendScrollRegression(true)')
    state(page, 'opened')
    assert_paragraph(page, held)
    # A later viewport resize cannot replay the send that lost placement to layer.
    page.locator(INPUT).fill('one\ntwo\nthree\nfour\nfive')
    assert_paragraph(page, held)
    page.evaluate('window.__answerScrollRegression(1)')
    assert_paragraph(page, held)

def answer_room_reading(page):
    return page.locator(THREAD).evaluate("""thread => {
      const prompt = thread.querySelector('[data-conversation-outgoing], [data-conversation-row="prompt"]');
      const room = thread.querySelector('[data-conversation-reserved-space]');
      return {room:room?.getBoundingClientRect().height ?? 0,
        promptTop:prompt.getBoundingClientRect().top-thread.getBoundingClientRect().top,
        viewport:thread.clientHeight,scroll:thread.scrollTop};
    }""")

def answer_room_case(page, url):
    page.goto(url,wait_until='domcontentloaded')
    page.locator(INPUT).click()
    page.locator('[data-conversation-expand]').click()
    state(page,'opened')
    settled(page)
    page.evaluate('window.__sendScrollRegression(false)')
    settled(page)
    sent = answer_room_reading(page)
    assert 0 <= sent['promptTop'] <= 32, sent
    assert sent['room'] > 200, sent
    page.evaluate('window.__answerScrollRegression(1)')
    settled(page)
    partial = answer_room_reading(page)
    assert 0 < partial['room'] < sent['room'] - 30, (sent,partial)
    page.locator(INPUT).fill('\n'.join('Draft line '+str(n) for n in range(6)))
    settled(page)
    smaller = answer_room_reading(page)
    assert smaller['viewport'] < partial['viewport'] - 50, (partial,smaller)
    assert smaller['room'] <= partial['room'] - 50, (partial,smaller)
    assert abs(smaller['promptTop'] - partial['promptTop']) <= 3, (partial,smaller)
    page.evaluate('window.__answerScrollRegression(20)')
    settled(page)
    exhausted = answer_room_reading(page)
    assert exhausted['room'] == 0, exhausted
    page.locator(INPUT).fill('')
    settled(page)
    grown = answer_room_reading(page)
    assert grown['room'] == 0, (exhausted,grown)
    assert_newest(page)

def install_controlled_image(page):
    # A capture listener gives the browser genuine input in the same dispatch as
    # raw content growth, before ResizeObserver can publish its pending notice.
    page.locator(THREAD).evaluate("""thread => {
      const image = document.createElement('img');
      image.alt = 'Controlled geometry race';
      image.style.cssText = 'display:block;width:1px;height:0px';
      thread.querySelector('[data-conversation-row]').prepend(image);
      window.__raceImage = image;
    }""")
    settled(page)

def native_before_observer_case(page, url):
    prepare_scroll_case(page,url,'plain','scrollback')
    install_controlled_image(page)
    before = newest_reading(page)['scrollTop']
    page.locator(THREAD).evaluate("""thread => thread.addEventListener('wheel', () => {
      window.__raceImage.style.height = '180px';
      window.__nativeRaceBefore = thread.scrollTop;
    }, {capture:true, once:true})""")
    page.locator(THREAD).hover()
    page.mouse.wheel(0,120)
    page.wait_for_timeout(100)
    settled(page)
    after = newest_reading(page)
    assert abs(page.evaluate('window.__nativeRaceBefore') - before) <= 3
    assert abs(after['scrollTop'] - before - 120) <= 3, (before,after)
    held = visible_paragraph(page)
    page.evaluate("window.__raceImage.style.height = '220px'")
    assert_paragraph(page, held)

def short_message_growth_case(page, url):
    prepare_scroll_case(page,url,'plain','scrollback')
    page.locator(THREAD).focus()
    page.keyboard.press('Home')
    page.wait_for_function("document.querySelector('[data-conversation-thread]').scrollTop <= 1")
    settled(page)
    install_controlled_image(page)
    row_height = page.locator('[data-conversation-row]').first.bounding_box()['height']
    assert row_height < page.locator(THREAD).evaluate('thread => thread.clientHeight'), row_height
    held = visible_paragraph(page)
    page.evaluate("window.__raceImage.style.height = '180px'")
    assert_paragraph(page,held)

def descendant_keyboard_case(page, url):
    for key in ['PageDown','Home','End','Control+Home','Control+End']:
        prepare_scroll_case(page,url,'plain','scrollback')
        install_controlled_image(page)
        page.evaluate("""() => {
          const link = document.createElement('a');
          link.href = '#keyboard-proof';link.textContent = ' Read';
          window.__heldParagraph.append(link);
          link.focus({preventScroll:true});
        }""")
        settled(page)
        before = newest_reading(page)['scrollTop']
        page.keyboard.press(key)
        # Chromium animates native key scrolling. Wait for its actual endpoint
        # before the independent geometry change, so the held paragraph is settled.
        if key.endswith('Home'):
            page.wait_for_function("document.querySelector('[data-conversation-thread]').scrollTop <= 1")
        elif key.endswith('End'):
            page.wait_for_function("""() => {
              const thread=document.querySelector('[data-conversation-thread]');
              return thread.scrollHeight-thread.scrollTop-thread.clientHeight <= 1;
            }""")
        else:
            page.wait_for_timeout(250)
        settled(page)
        after = newest_reading(page)
        if key == 'PageDown': assert after['scrollTop'] > before+50, (key,before,after)
        if key.endswith('Home'): assert after['scrollTop'] <= 1, (key,before,after)
        if key.endswith('End'): assert_newest(page)
        held = visible_paragraph(page)
        if key.endswith('Home'):
            # Growth below the reader must retain the native Home baseline.
            page.evaluate('window.__proseDelta(0)')
        else:
            page.evaluate("window.__raceImage.style.height = '180px'")
        if key.endswith('End'): assert_newest(page)
        else: assert_paragraph(page,held)
    # Plain buttons still allow native arrow scrolling, while Space activates.
    prepare_scroll_case(page,url,'plain','scrollback')
    install_controlled_image(page)
    page.evaluate("""() => {
      const button=document.createElement('button');button.textContent='Read';
      button.addEventListener('click',()=>window.__buttonClicks=(window.__buttonClicks??0)+1);
      window.__heldParagraph.append(button);button.focus({preventScroll:true});
    }""")
    settled(page)
    for key, amount in [('ArrowDown',180),('ArrowUp',220)]:
        before = newest_reading(page)['scrollTop']
        page.keyboard.press(key)
        page.wait_for_timeout(250)
        settled(page)
        after = newest_reading(page)['scrollTop']
        assert after > before+10 if key == 'ArrowDown' else after < before-10, (key,before,after)
        held = visible_paragraph(page)
        page.evaluate('(amount) => window.__raceImage.style.height = amount+"px"',amount)
        assert_paragraph(page,held)
    before = newest_reading(page)['scrollTop']
    page.keyboard.press('Space')
    settled(page)
    assert page.evaluate('window.__buttonClicks') == 1
    assert abs(newest_reading(page)['scrollTop'] - before) <= 3
    # An editable nested scroller owns its navigation and scroll position.
    prepare_scroll_case(page,url,'plain','scrollback')
    page.evaluate("""() => {
      const box = document.createElement('textarea');
      const bounds=document.querySelector('[data-conversation-thread]').getBoundingClientRect();
      box.rows=1;box.style.cssText='position:fixed;left:'+bounds.left+'px;top:'+(bounds.top+20)+'px;width:120px;height:24px';
      box.value=('nested text\\n').repeat(20);
      window.__heldParagraph.append(box);box.focus({preventScroll:true});
      box.setSelectionRange(0,0);window.__nestedEditor=box;
    }""")
    settled(page)
    before = newest_reading(page)['scrollTop']
    page.keyboard.press('Control+End')
    settled(page)
    assert page.evaluate('window.__nestedEditor.selectionStart === window.__nestedEditor.value.length && window.__nestedEditor.scrollTop > 0'), page.evaluate('({start:window.__nestedEditor.selectionStart,length:window.__nestedEditor.value.length,scroll:window.__nestedEditor.scrollTop})')
    assert abs(newest_reading(page)['scrollTop'] - before) <= 3, (before,newest_reading(page))

def delayed_case(page, url, kind, mode):
    pending = []
    pattern = '**/delayed.png' if kind == 'image' else '**/files/tickets/t_scrollregression/delayed.md'
    page.route(pattern, lambda route: pending.append(route))
    held = prepare_scroll_case(page, url, kind, mode)
    page.wait_for_function("() => document.querySelector('[data-conversation-row]') !== null")
    assert len(pending) == 1, (kind, len(pending))
    row_count = page.locator('[data-conversation-row]').count()
    page.locator('[data-conversation-row="agent_message"]').nth(12).evaluate('row => window.__delayedRow = row')
    before = newest_reading(page)
    if mode == 'scrollback':
        assert page.evaluate("performance.now() - window.__lastRegressionWheel") < 400
    # This is response release, with no row assignment, replaced message, or app event.
    if kind == 'image':
        import struct, zlib
        def chunk(name, payload):
            return struct.pack('!I',len(payload))+name+payload+struct.pack('!I',zlib.crc32(name+payload)&0xffffffff)
        image = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',500,310,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\x80\xa0\xb0'*500)*310))+chunk(b'IEND',b'')
        pending[0].fulfill(status=200, content_type='image/png', body=image)
        page.wait_for_function("() => {const image=document.querySelector('[data-conversation-piece=image]');return image?.complete && image.naturalHeight === 310}")
    else:
        pending[0].fulfill(status=200, content_type='text/markdown', body='\n\n'.join('Delayed preview paragraph '+str(n)+': '+('Loaded Markdown changes real content geometry. '*3) for n in range(6)))
        page.get_by_text('Delayed preview paragraph 5:', exact=False).wait_for()
    settled(page)
    assert page.evaluate('window.__delayedRow.isConnected')
    assert page.locator('[data-conversation-row]').count() == row_count
    if held:
        assert held['text'].startswith('Tall paragraph'), held
        actual = assert_paragraph(page, held)
        assert newest_reading(page)['scrollTop'] > before['scrollTop'] + 100, (before, newest_reading(page))
    else:
        actual = assert_newest(page)
        assert actual['scrollTop'] > before['scrollTop'] + 100, (before, actual)
    print('DELAYED_GEOMETRY', kind, mode, {'before': before, 'held': held, 'after': actual}, flush=True)
    evidence = os.environ.get('SCROLL_REGRESSION_EVIDENCE_DIR')
    if evidence:
        os.makedirs(evidence, exist_ok=True)
        page.screenshot(path=os.path.join(evidence,'delayed-'+kind+'-'+mode+'.png'))

@contextmanager
def conversation_browser(playwright):
    browser = playwright.chromium.launch(headless=True)
    try:
        with browser_failure_evidence(browser, "conversation-pane-browser"):
            yield browser
    finally:
        browser.close()

with sync_playwright() as playwright, conversation_browser(playwright) as browser:
    # Named cases share this host and run independently. Selection permits every
    # regression to demonstrate its own failure against the unfixed source.
    cases = {
      'draft-measurement': draft_measurement_case,
      'prose-deltas': prose_delta_case,
      'missing-anchors': missing_anchors_case,
      'layer-send': layer_send_case,
      'answer-room': answer_room_case,
      'native-before-observer': native_before_observer_case,
      'descendant-keyboard': descendant_keyboard_case,
      'short-message-growth': short_message_growth_case,
      **{f'delayed-{kind}-{mode}': (lambda page, url, kind=kind, mode=mode: delayed_case(page,url,kind,mode))
         for kind in ['image','markdown'] for mode in ['following','scrollback']}
    }
    selected = os.environ.get('CONVERSATION_SCROLL_CASE')
    if selected and selected not in cases:
        raise AssertionError('Unknown CONVERSATION_SCROLL_CASE: '+selected)
    failures = []
    for name, run in cases.items():
        if selected and selected != name: continue
        regression_page = browser.new_page(viewport={'width':1000,'height':760})
        regression_page.set_default_timeout(5_000)
        try:
            run(regression_page, sys.argv[1])
            print('SCROLL_CASE PASS '+name, flush=True)
        except Exception as error:
            failures.append(name+': '+str(error))
            print('SCROLL_CASE FAIL '+name+': '+str(error), flush=True)
            print(traceback.format_exc(),flush=True)
            evidence = os.environ.get('SCROLL_REGRESSION_EVIDENCE_DIR')
            if evidence:
                os.makedirs(evidence, exist_ok=True)
                regression_page.screenshot(path=os.path.join(evidence,name+'.png'))
        finally:
            regression_page.close()
    assert not failures, '\n'.join(failures)
    if selected:
        print('conversation-pane-browser.test.mjs: all assertions passed')
        sys.exit(0)

    context = browser.new_context(viewport={"width": 1000, "height": 760})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(5_000)
    browser_errors = []
    page.on("pageerror", lambda error: browser_errors.append(str(error)))
    page.goto(sys.argv[1], wait_until="domcontentloaded")

    # Rest, peeked, and opened are three heights of one mounted conversation.
    state(page, "rest")
    assert page.locator("[data-conversation-rest-bar]").is_visible()
    assert "Line 27:" in page.locator("[data-conversation-rest-line]").inner_text()
    assert page.locator(THREAD).count() == 1
    assert not page.locator(THREAD).is_visible()

    page.locator(INPUT).click()
    state(page, "peeked")
    assert page.locator(THREAD).is_visible()
    assert page.locator("[data-conversation-rest-bar]").count() == 0
    send_mode = page.locator("[data-conversation-send-mode]")
    send_mode_trigger = page.locator("[data-conversation-send-mode-trigger]")
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Steer"
    assert send_mode_trigger.get_attribute("aria-expanded") == "false"
    assert send_mode_trigger.get_attribute("aria-controls") is None

    # The model chooser uses the same one-tab-stop shell and focus contract.
    model_picker = page.locator("[data-conversation-model-picker]")
    model_trigger = model_picker.locator("[data-conversation-picker-trigger]")
    model_label = model_trigger.get_attribute("aria-label")
    assert model_trigger.get_attribute("aria-controls") is None
    model_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    model_controlled = model_trigger.get_attribute("aria-controls")
    assert model_controlled and model_picker.locator(f"#{model_controlled}").get_attribute("role") == "listbox"
    assert model_picker.locator("button").evaluate_all("buttons => buttons.filter(button => button.tabIndex === 0).length") == 1
    model_picker.locator('[data-conversation-picker-choice="sonnet"]').hover()
    # A pointer that moves onto a row makes that row the active one.
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=sonnet]')?.getAttribute('data-conversation-picker-active') === 'true'")
    assert model_trigger.get_attribute("aria-label") == model_label
    model_picker.get_by_role("listbox").press("s")
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=sonnet]')?.getAttribute('data-conversation-picker-active') === 'true'")
    model_picker.get_by_role("listbox").press("Enter")
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-trigger]')?.getAttribute('aria-label')?.toLowerCase().includes('sonnet')")
    assert "sonnet" in model_trigger.get_attribute("aria-label").lower()
    model_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    model_picker.get_by_role("listbox").press("Home")
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=opus]')?.getAttribute('data-conversation-picker-active') === 'true'")
    model_picker.get_by_role("listbox").press(" ")
    # Opus takes a reasoning level. Choosing its row retains the popover and moves to
    # the second step. The trigger still shows the committed Sonnet choice until this
    # level completes the configuration.
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [role=listbox]')?.getAttribute('aria-label') === 'Reasoning efforts'")
    assert "sonnet" in model_trigger.get_attribute("aria-label").lower()
    model_picker.get_by_role("listbox").press("Enter")
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-trigger]')?.getAttribute('aria-label')?.toLowerCase().includes('opus')")
    assert "opus" in model_trigger.get_attribute("aria-label").lower()

    model_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")

    # The pointer has rested on sonnet since the hover above, and opus is now the chosen
    # model. When the options mount again the browser sends a mouse move at the pointer's
    # resting place. That is the browser reporting geometry, not a person choosing a row,
    # so the chosen model keeps the active mark. Settle first, or the mouse move has not
    # arrived yet and the check proves nothing.
    page.wait_for_timeout(200)
    assert model_picker.locator('[data-conversation-picker-choice="opus"]').get_attribute("data-conversation-picker-active") == "true"

    model_picker.get_by_role("listbox").press("End")
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-choice=sonnet]')?.getAttribute('data-conversation-picker-active') === 'true'")
    model_picker.get_by_role("listbox").press("Enter")
    page.wait_for_function("document.querySelector('[data-conversation-model-picker] [data-conversation-picker-trigger]')?.getAttribute('aria-label')?.toLowerCase().includes('sonnet')")
    assert "sonnet" in model_trigger.get_attribute("aria-label").lower()
    model_trigger.press("ArrowDown")
    page.evaluate("window.__setComposerDisabled(true)")
    assert model_picker.evaluate("root => document.activeElement === root") is True
    page.evaluate("window.__setComposerDisabled(false)")
    page.wait_for_function("document.activeElement?.hasAttribute('data-conversation-picker-trigger')")

    # Reset the fixture so keyboard selection coverage does not alter later send scenarios.
    page.reload(wait_until="domcontentloaded")
    state(page, "rest")
    page.locator(INPUT).click()
    state(page, "peeked")
    send_mode = page.locator("[data-conversation-send-mode]")
    send_mode_trigger = page.locator("[data-conversation-send-mode-trigger]")

    # The delivery chooser follows the product picker contract for pointer, keyboard,
    # focus return, outside dismissal, and disabled state.
    send_mode_trigger.click()
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    send_mode_panel = page.locator("[data-conversation-send-mode-panel]")
    controlled = send_mode_trigger.get_attribute("aria-controls")
    assert controlled and send_mode.locator(f"#{controlled}").get_attribute("role") == "listbox"
    assert send_mode.locator("button").evaluate_all("buttons => buttons.filter(button => button.tabIndex === 0).length") == 1
    assert send_mode_panel.get_by_role("option").count() == 3
    assert send_mode_panel.get_by_role("option", name="Steer", exact=True).count() == 1
    assert send_mode_panel.get_by_role("option", name="Queue", exact=True).count() == 1
    assert send_mode_panel.get_by_role("option", name="Send now", exact=True).count() == 1
    assert send_mode_panel.get_by_role("option", name="Steer").get_attribute("aria-selected") == "true"
    send_mode_panel.get_by_role("option", name="Send now").hover()
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Steer"
    send_mode_panel.press("Escape")
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0
    assert send_mode_trigger.get_attribute("aria-controls") is None
    assert send_mode_trigger.evaluate("button => document.activeElement === button") is True

    send_mode_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    send_mode_panel.press("Tab")
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0
    assert send_mode.evaluate("root => !root.contains(document.activeElement)") is True

    send_mode_trigger.focus()
    send_mode_trigger.press("ArrowDown")
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Steer"
    send_mode.get_by_role("listbox").press("q")
    page.wait_for_function("document.querySelector('[data-conversation-send-mode] [data-conversation-send-mode-choice=queue]')?.getAttribute('data-listbox-picker-active') === 'true'")
    assert send_mode_panel.get_by_role("option", name="Queue").get_attribute("data-listbox-picker-active") == "true"
    send_mode.get_by_role("listbox").press("Enter")
    page.wait_for_function("document.querySelector('[data-conversation-send-mode-trigger]')?.getAttribute('aria-label') === 'Message delivery mode: Queue'")
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Queue"
    send_mode_trigger.click()
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'listbox'")
    send_mode.get_by_role("listbox").press("Home")
    page.wait_for_function("document.querySelector('[data-conversation-send-mode] [data-conversation-send-mode-choice=steer]')?.getAttribute('data-listbox-picker-active') === 'true'")
    send_mode.get_by_role("listbox").press("ArrowDown")
    page.wait_for_function("document.querySelector('[data-conversation-send-mode] [data-conversation-send-mode-choice=queue]')?.getAttribute('data-listbox-picker-active') === 'true'")
    send_mode.get_by_role("listbox").press(" ")
    page.wait_for_function("document.querySelector('[data-conversation-send-mode-trigger]')?.getAttribute('aria-label') === 'Message delivery mode: Queue'")
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Queue"
    page.wait_for_function("document.activeElement?.hasAttribute('data-conversation-send-mode-trigger')")
    assert send_mode_trigger.evaluate("button => document.activeElement === button") is True

    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="send_now"]').click()
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Send now"
    send_mode_trigger.click()
    page.locator(INPUT).click()
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0

    send_mode_trigger.click()
    page.evaluate("window.__setComposerDisabled(true)")
    assert send_mode_trigger.is_disabled()
    assert page.locator("[data-conversation-send-mode-panel]").count() == 0
    assert send_mode.evaluate("root => document.activeElement === root") is True
    page.evaluate("window.__setComposerDisabled(false)")
    assert not send_mode_trigger.is_disabled()
    page.wait_for_function("document.activeElement?.hasAttribute('data-conversation-send-mode-trigger')")

    # Re-enabling returns focus only while focus remains within the disabled picker.
    send_mode_trigger.click()
    page.evaluate("window.__setComposerDisabled(true)")
    page.wait_for_function("document.querySelector('[data-conversation-send-mode]') === document.activeElement")
    unrelated_control = page.locator("[data-ticket-behind]")
    unrelated_control.focus()
    assert unrelated_control.evaluate("control => document.activeElement === control") is True
    page.evaluate("window.__setComposerDisabled(false)")
    page.wait_for_function("!document.querySelector('[data-conversation-send-mode-trigger]').disabled")
    page.evaluate("window.__settle()")
    active_after_enable = page.evaluate("document.activeElement?.outerHTML")
    assert unrelated_control.evaluate("control => document.activeElement === control") is True, active_after_enable
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="queue"]').click()

    page.locator(INPUT).fill("the draft stays exactly here")
    page.locator(INPUT).evaluate("box => box.setSelectionRange(9, 9)")
    page.evaluate("window.__rememberConversationInput()")
    expected_draft = {"text": "the draft stays exactly here", "start": 9, "end": 9}

    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    assert draft(page) == expected_draft, (draft(page), expected_draft)
    assert page.evaluate("window.__conversationInputSurvived()") is True
    assert send_mode_trigger.get_attribute("aria-label") == "Message delivery mode: Queue"

    # A named pair in the header, and the F shortcut, switch the lens in place. Both
    # names stay on the screen and the pressed one says where you are. Editable controls
    # keep ordinary F input, and modified shortcuts do nothing.
    focus_choice = page.locator('[data-conversation-lens-choice="focus"]')
    full_choice = page.locator('[data-conversation-lens-choice="full"]')

    def lens_now():
        assert focus_choice.get_attribute("aria-pressed") != full_choice.get_attribute(
            "aria-pressed"
        ), "exactly one lens is pressed"
        return "focus" if focus_choice.get_attribute("aria-pressed") == "true" else "full"

    assert focus_choice.inner_text() == "Focus"
    assert full_choice.inner_text() == "Full"
    assert lens_now() == "focus"
    full_choice.click()
    assert lens_now() == "full"
    page.keyboard.press("f")
    assert lens_now() == "focus"
    page.keyboard.press("Control+f")
    assert lens_now() == "focus"
    page.locator(INPUT).press("f")
    assert lens_now() == "focus"
    expected_draft = draft(page)

    # Ticket content dismisses opened in one step without replacing or clearing the draft.
    page.locator("[data-ticket-behind]").click()
    state(page, "rest")
    assert draft(page) == expected_draft, (draft(page), expected_draft)
    assert page.evaluate("window.__conversationInputSurvived()") is True

    # A press beside the centered pane has the same dismissal contract.
    page.locator(INPUT).click()
    expected_draft = draft(page)
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    layer = page.locator("[data-conversation-layer-host]").bounding_box()
    pane = page.locator(PANE).bounding_box()
    assert layer is not None and pane is not None
    page.mouse.click(layer["x"] + 2, layer["y"] + layer["height"] / 2)
    state(page, "rest")
    assert draft(page) == expected_draft, (draft(page), expected_draft)

    # A reader's anchor survives a height change.
    page.locator(INPUT).click()
    state(page, "peeked")
    thread = page.locator(THREAD)
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      return thread && thread.scrollHeight > thread.clientHeight && thread.scrollTop > 0;
    }""")
    thread.hover()
    page.mouse.wheel(0, -550)
    page.wait_for_timeout(50)
    anchored = first_visible_line(page)
    assert anchored is not None
    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    restored = first_visible_line(page)
    assert restored is not None
    assert restored["text"] == anchored["text"]
    assert abs(restored["top"] - anchored["top"]) <= 3, (anchored, restored)

    # Removing a screenful above the reader moves the thread, not their anchored line.
    page.evaluate("window.__dropFirstConversationLines(6)")
    page.wait_for_function("""() =>
      document.querySelectorAll('[data-conversation-row="agent_message"]').length === 22
    """)
    after_removal = wait_for_anchor(page, anchored)
    assert after_removal["scrollTop"] < anchored["scrollTop"], (anchored, after_removal)

    # An existing row growing above the reader produces the equal opposite correction.
    page.evaluate("window.__growFirstConversationLine()")
    page.get_by_text("This existing line became much taller above the reader.", exact=False).wait_for()
    after_growth = wait_for_anchor(page, after_removal)
    assert after_growth["scrollTop"] > after_removal["scrollTop"], (after_removal, after_growth)

    # Latest resumes following, and a newly appended line stays in sight.
    page.locator('button[aria-label="Jump to latest message"]').click()
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      return thread.scrollHeight - thread.scrollTop - thread.clientHeight <= 2;
    }""")
    before = thread.evaluate("node => node.scrollTop")
    page.evaluate("window.__appendConversationLine()")
    page.get_by_text("Newest appended line 28", exact=True).wait_for()
    page.wait_for_function("""() => {
      const thread = document.querySelector('[data-conversation-thread]');
      const newest = [...thread.querySelectorAll('[data-conversation-row="agent_message"]')].at(-1);
      return newest.getBoundingClientRect().bottom <= thread.getBoundingClientRect().bottom + 1;
    }""")
    assert thread.evaluate("node => node.scrollTop") >= before

    # Opened adds one spacing token after the task strip. Peeked stays flush.
    page.locator("[data-ticket-behind]").click()
    state(page, "rest")
    page.evaluate("window.__showActivePlan()")
    page.locator(INPUT).click()
    state(page, "peeked")
    strip = page.locator("[data-conversation-task-strip]")
    composer = page.locator("[data-conversation-composer]")
    strip_box = strip.bounding_box()
    composer_box = composer.bounding_box()
    assert strip_box is not None and composer_box is not None
    assert round(strip_box["height"]) == 34
    assert round(composer_box["y"] - strip_box["y"] - strip_box["height"]) == 0

    page.locator("[data-conversation-expand]").click()
    state(page, "opened")
    strip_box = strip.bounding_box()
    composer_box = composer.bounding_box()
    assert strip_box is not None and composer_box is not None
    assert round(strip_box["height"]) == 34
    assert round(composer_box["y"] - strip_box["y"] - strip_box["height"]) == 8

    # Return to steer for the send-boundary scenarios below.
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="steer"]').click()

    # The steer selection and its queued fallback reason reach the held row.
    page.locator(INPUT).fill("default steer")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sentModes?.length === 1")
    assert page.evaluate("window.__sentModes") == ["steer"]
    page.locator('[data-conversation-held-row="composer-fallback"]').wait_for()
    assert "turn did not accept steering" in page.locator("[data-conversation-held-stack]").inner_text()

    # An explicit chooser selection reaches the send boundary unchanged.
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="queue"]').click()
    page.locator(INPUT).fill("explicit queue")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sentModes?.length === 2")
    assert page.evaluate("window.__sentModes") == ["steer", "queue"]
    send_mode_trigger.click()
    page.locator('[data-conversation-send-mode-choice="steer"]').click()

    # A run change remains attached to the default steer send. The server-visible
    # fallback is a queued message that explains it is waiting to apply that change.
    page.locator("[data-conversation-picker-model] [data-conversation-picker-trigger]").click()
    page.locator('[data-conversation-picker-choice="sonnet"]').click()
    page.locator(INPUT).fill("steer with a run change")
    page.locator(INPUT).press("Enter")
    page.wait_for_function("window.__sentModes?.length === 3")
    assert page.evaluate("window.__sentModes") == ["steer", "queue", "steer"]
    assert page.evaluate("window.__sentRuns[2].model") == "sonnet"
    assert "apply the run change" in page.locator("[data-conversation-held-stack]").inner_text()

    # The server capability controls one provider-neutral steering action.
    page.evaluate("window.__showHeldPrompt(false)")
    page.locator('[data-conversation-held-row="held-1"]').wait_for()
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0
    page.evaluate("window.__showHeldPrompt(true)")
    steer = page.locator('[data-conversation-held-promote="steer"]')
    steer.wait_for()
    assert steer.inner_text() == "Steer"
    assert "Hermes" not in page.locator("[data-conversation-held-stack]").inner_text()

    # A send the server never answered sits above the composer, never under the rows, and
    # carries the two things this tab can do about it on its own.
    page.evaluate("window.__showUnansweredSend()")
    page.locator('[data-conversation-held-row="local:sent-into-the-dark"]').wait_for()
    assert "no answer came" in page.locator("[data-conversation-held-stack]").inner_text()
    assert page.locator('[data-conversation-outgoing="sent-into-the-dark"]').count() == 0
    assert page.locator('[data-conversation-held-promote="send_now"]').count() == 0
    assert page.locator('[data-conversation-held-promote="steer"]').count() == 0
    send_again = page.locator('[data-conversation-held-send-again="sent-into-the-dark"]')
    send_again.wait_for()
    if screenshot_dir:
        page.locator("[data-conversation-held-stack]").screenshot(
            path=os.path.join(screenshot_dir, "unanswered-send-above-the-composer.png")
        )
    send_again.click()
    page.wait_for_function("window.__sentAgain === 'sent-into-the-dark'")
    page.locator('[data-conversation-held-stop-drawing="sent-into-the-dark"]').click()
    page.wait_for_function("window.__stoppedDrawing === 'sent-into-the-dark'")

    # Opening is an open, not a restore. A reader who moves up the thread and then puts
    # the conversation away comes back to the end of it, which is where the newest work
    # is. Nothing else about keeping their place changes.
    while page.locator(f'{PANE}[data-conversation-state="rest"]').count() == 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(50)
    page.locator(INPUT).click()
    state(page, "peeked")
    page.wait_for_function(
        """() => {
            const t = document.querySelector('[data-conversation-thread]');
            return t && t.scrollHeight - t.scrollTop - t.clientHeight <= 2;
        }"""
    )
    page.locator(THREAD).hover()
    for _ in range(8):
        page.mouse.wheel(0, -400)
    page.wait_for_function(
        """() => {
            const t = document.querySelector('[data-conversation-thread]');
            return t && t.scrollHeight - t.scrollTop - t.clientHeight > 200;
        }"""
    )
    page.keyboard.press("Escape")
    state(page, "rest")
    page.locator(INPUT).click()
    state(page, "peeked")
    page.wait_for_function(
        """() => {
            const t = document.querySelector('[data-conversation-thread]');
            return t && t.scrollHeight - t.scrollTop - t.clientHeight <= 2;
        }"""
    )
    page.keyboard.press("Escape")
    state(page, "rest")

    # The line at rest says what it always said, and says the rest of it without words:
    # independent message attention and turn-status shapes, drawn together.
    page.evaluate("window.__showEveryMark()")
    marks = page.locator("[data-conversation-rest-marks] [data-conversation-mark]")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-mark]').length === 3"
    )
    assert marks.evaluate_all("els => els.map(e => e.dataset.conversationMark)") == [
        "running",
        "reply",
        "failed",
    ]
    assert page.locator("[data-conversation-rest-line]").inner_text() != ""
    assert page.get_by_text("waiting for you", exact=True).count() == 0

    # The right of the composer is what there is to do, in the agreed order. A composer
    # with nothing in it offers no Send at all — not a greyed-out one — and the control
    # that stops the work sits after the microphone, as the approved composer design specifies.
    def submit_order(page):
        return page.locator(".chat-submit > *").evaluate_all(
            """els => els.map(e =>
                e.dataset.conversationStop !== undefined ? 'stop'
              : e.dataset.voiceRecord !== undefined ? 'mic'
              : e.dataset.conversationSend !== undefined ? 'send'
              : 'delivery')"""
        )

    page.evaluate("window.__showQuietComposer()")
    page.locator(INPUT).click()
    state(page, "peeked")
    page.locator(INPUT).fill("")
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-send]') === null"
    )
    assert submit_order(page) == ["delivery", "mic"]

    page.locator(INPUT).fill("something to send")
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-send]') !== null"
    )
    assert submit_order(page) == ["delivery", "mic", "send"]

    page.evaluate("window.__showWorkRunning()")
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-stop]') !== null"
    )
    assert submit_order(page) == ["delivery", "mic", "stop", "send"]
    page.locator(INPUT).fill("")
    page.wait_for_function(
        "() => document.querySelector('[data-conversation-send]') === null"
    )
    assert submit_order(page) == ["delivery", "mic", "stop"]

    # The head carries no worker title and no launch folder. The folder is still one
    # press away, where the card keeps everything else it is not showing.
    assert page.locator(".chat-lbl").count() == 0
    assert page.locator(".chat-head [data-conversation-workspace]").count() == 0

    # One card, one outline: the well is a band with a seam above it, not a second box.
    well = page.locator(".chat-box").evaluate(
        """box => {
            const s = getComputedStyle(box);
            return {top: s.borderTopWidth, left: s.borderLeftWidth,
                    bottom: s.borderBottomWidth, radius: s.borderTopLeftRadius};
        }"""
    )
    assert well["top"] == "1px", well
    assert well["left"] == "0px" and well["bottom"] == "0px", well
    assert well["radius"] == "0px", well

    # Back to rest, where the line lives.
    while page.locator(f'{PANE}[data-conversation-state="rest"]').count() == 0:
        page.keyboard.press("Escape")
        page.wait_for_timeout(50)

    # A pending ask stays in the text and creates no conversation attention dot.
    page.evaluate("window.__showRunningAndNeedsYou()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-mark]').length === 1"
    )
    assert marks.evaluate_all("els => els.map(e => e.dataset.conversationMark)") == ["running"]
    assert page.locator("[data-conversation-rest-line]").inner_text() != ""
    request = page.locator('[data-conversation-ask="ask-mark"]')
    request.wait_for(state="visible")
    assert "may I run this" in request.inner_text()
    assert "Write the permission exercise file" in request.inner_text()
    assert page.evaluate("window.__permissionAnswers") == []

    # The actual pane sends the backend's exact option id only after an explicit answer.
    # Backend waiting and file outcomes are exercised separately against real adapters.
    for option_id in ["allow-file", "deny-file"]:
        page.evaluate("window.__showRunningAndNeedsYou()")
        page.locator(f'[data-conversation-ask-action="{option_id}"]').click()
        page.wait_for_selector('[data-conversation-ask="ask-mark"]', state="detached")
        assert page.evaluate("window.__permissionAnswers") == [
            {"askId": "ask-mark", "optionId": option_id}
        ]

    page.keyboard.press("Escape")
    state(page, "rest")

    # Ordinary output past the read position is not a reply anybody is owed.
    page.evaluate("window.__showOrdinaryOutputOnly()")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-conversation-mark]').length === 0"
    )
    assert page.locator("[data-conversation-rest-marks]").count() == 0

    # A completed owner turn uses the hidden ending for structure. The collapsed Focus
    # line shows the reply without a live working timer.
    page.evaluate("window.__showSettledFocusRestLine()")
    state(page, "rest")
    assert page.locator("[data-conversation-rest-line]").inner_text() == "settled reply"
    assert page.locator("[data-conversation-rest-bar] .c2-rest-working").count() == 0

    # Focus keeps settled work folded. Full reveals all commentary and every tool call.
    page.evaluate("window.__showSettledTurn()")
    assert page.get_by_text("hidden commentary", exact=True).count() == 0
    assert page.locator('[data-conversation-row="tool_call"]').count() == 0
    page.locator('[data-conversation-lens-choice="full"]').click()
    assert page.get_by_text("hidden commentary", exact=True).count() == 1
    assert page.locator('[data-conversation-row="tool_call"]').count() == 4
    assert page.locator("[data-conversation-work-fold]").count() == 0
    assert page.locator("[data-conversation-turn-fold]").count() == 0
    assert page.locator("[data-conversation-turn-count]").count() == 0
    assert page.locator("[data-conversation-turn-settled-head]").count() == 1

    # The two endings a reader has to act on are the two the pane spells out: a turn the
    # backend killed, named with the reason it gave, and a turn whose ending the record
    # will never contain.
    page.evaluate("window.__showTurnEndings()")
    page.get_by_text("backend exited", exact=True).wait_for()
    page.get_by_text("turn stopped without an ending", exact=True).wait_for()

    # The composer catalog menu opens from the keyboard, under a pointer left wherever it
    # was. A menu that mounts or reflows under a still pointer receives a mouse event at
    # the pointer's resting place, and that is the browser reporting geometry rather than
    # a person choosing a row. This section parks the pointer, so it runs last and on a
    # fresh page.
    page.reload(wait_until="domcontentloaded")
    state(page, "rest")
    page.locator(INPUT).click()
    state(page, "peeked")

    CATALOG_MENU = "[data-conversation-catalog]"
    CATALOG_ROW = "[data-conversation-catalog-entry]"

    def catalog_rows(page):
        return page.locator(CATALOG_ROW).evaluate_all(
            "rows => rows.map(row => row.getAttribute('data-conversation-catalog-entry'))")

    def highlighted_entry(page):
        return page.evaluate(
            "() => document.querySelector('[data-conversation-catalog-active]')"
            "?.getAttribute('data-conversation-catalog-entry') ?? null")

    def close_catalog_menu(page):
        page.locator(INPUT).press("Control+a")
        page.locator(INPUT).press("Backspace")
        page.wait_for_selector(CATALOG_MENU, state="detached")

    # Where the rows land while the whole catalog shows.
    page.locator(INPUT).type("/")
    page.wait_for_selector(CATALOG_MENU)
    every_entry = catalog_rows(page)
    assert every_entry[0] == "/recap", every_entry
    third_row = page.locator(CATALOG_ROW).nth(2).bounding_box()
    close_catalog_menu(page)

    # Park the pointer over the third row's place. Nothing clicks after this, because a
    # click would move the pointer and the whole point is that it never moves again.
    page.mouse.move(third_row["x"] + third_row["width"] / 2,
                    third_row["y"] + third_row["height"] / 2)

    # The keyboard opens the menu and owns the highlight. Settle first, or the mouse event
    # has not arrived yet and the check proves nothing.
    page.locator(INPUT).type("/")
    page.wait_for_selector(CATALOG_MENU)
    page.wait_for_timeout(200)
    assert highlighted_entry(page) == every_entry[0], highlighted_entry(page)

    # Backspace widens the open list, so rows slide back under the still pointer. A new
    # list starts its highlight at the top, and geometry does not move it. The list must
    # really grow, or this checks nothing.
    page.locator(INPUT).type("r")
    page.wait_for_function("count => document.querySelectorAll('[data-conversation-catalog-entry]')"
                           ".length < count", arg=len(every_entry))
    narrowed = catalog_rows(page)
    page.locator(INPUT).press("Backspace")
    page.wait_for_function("count => document.querySelectorAll('[data-conversation-catalog-entry]')"
                           ".length === count", arg=len(every_entry))
    page.wait_for_timeout(200)
    widened = catalog_rows(page)
    assert len(widened) > len(narrowed), (narrowed, widened)
    assert highlighted_entry(page) == widened[0], (highlighted_entry(page), widened)

    # A real pointer move still moves the highlight. Without this the guard above can be
    # satisfied by a menu that never responds to the mouse at all.
    wanted = page.locator(CATALOG_ROW).nth(5)
    wanted_entry = wanted.get_attribute("data-conversation-catalog-entry")
    wanted.hover()
    page.wait_for_function(
        "entry => document.querySelector('[data-conversation-catalog-active]')"
        "?.getAttribute('data-conversation-catalog-entry') === entry", arg=wanted_entry)

    # A resting card grows intrinsically inside the bounded conversation layer.
    # Its own height cannot be the ruler for its content cap: that creates a shrinking
    # ResizeObserver feedback loop. Collapse/reopen must preserve the long draft.
    page.reload(wait_until="domcontentloaded")
    state(page, "rest")
    long_draft = "\n".join("Resting draft line " + str(n) for n in range(40))
    page.locator(INPUT).fill(long_draft)
    state(page, "rest")
    def measure_writing_area():
        return page.locator(INPUT).evaluate("""e => ({
          height:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).lineHeight),
          overflow:getComputedStyle(e).overflowY})""")
    def settle_frames():
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))")
    settle_frames()
    resting = measure_writing_area()
    assert resting["height"] >= resting["line"] * 3, resting
    assert resting["height"] <= resting["line"] * 12 + 1, resting
    assert resting["overflow"] == "auto", resting
    for _ in range(3):
        settle_frames()
        assert measure_writing_area() == resting
    page.locator(INPUT).click()
    state(page, "peeked")
    settle_frames()
    peeked = measure_writing_area()
    assert peeked["height"] >= peeked["line"] * 2, peeked
    page.keyboard.press("Escape")
    state(page, "rest")
    settle_frames()
    assert measure_writing_area() == resting
    page.locator(INPUT).click()
    state(page, "peeked")
    settle_frames()
    assert measure_writing_area() == peeked
    assert page.locator(INPUT).input_value() == long_draft
    assert not browser_errors, browser_errors

    # Former mocked E2E queue matrix, in this existing build/server/browser host.
    held = [{
        "key": f"held-{number}", "heldPromptId": f"held-{number}",
        "content": [{"piece": "text", "text": f"queued message {number}"}],
        "senderLabel": "owner", "senderMessageId": f"sender-{number}",
        "sentAtUnixMilliseconds": 1_700_000_000_000 + number,
        "state": "held", "queueReason": "requested",
    } for number in range(1, 7)]
    actions = []
    page.context.route("**/api/test/queue-matrix", lambda route: route.fulfill(json=held))

    def queue_api(route):
        request = route.request
        path = request.url.split("?", 1)[0]
        if request.method == "DELETE":
            held_id = path.rsplit("/", 1)[-1]
            actions.append([held_id, "discard"])
            held[:] = [row for row in held if row["heldPromptId"] != held_id]
            route.fulfill(json={"discarded": True})
        else:
            assert request.method == "POST" and path.endswith("/promote"), path
            held_id = path.rsplit("/", 2)[-2]
            mode = request.post_data_json["mode"]
            assert mode in ("steer", "send_now"), mode
            actions.append([held_id, mode])
            held[:] = [row for row in held if row["heldPromptId"] != held_id]
            route.fulfill(json={"promoted": True, "fate": "injected" if mode == "steer" else "started"})

    page.context.route("**/api/conversation/conversations/browser-fixture/held-prompts/**", queue_api)
    desktop = page.context.new_page()
    phone = page.context.new_page()
    phone.set_viewport_size({"width": 390, "height": 844})
    for queued_page in (desktop, phone):
        queued_page.on("pageerror", lambda error: browser_errors.append(str(error)))
        queued_page.goto(sys.argv[1], wait_until="domcontentloaded")
        state(queued_page, "rest")
        queued_page.evaluate("window.__loadQueueMatrix()")
        state(queued_page, "opened")
        queued_page.wait_for_selector("[data-conversation-held-row]")
        assert queued_page.locator("[data-conversation-held-row] .chat-qrow-txt").all_inner_texts() == [
            f"queued message {number}" for number in range(1, 7)
        ]
    def control_signature(page):
        return page.evaluate(
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
            )

    desktop_controls = control_signature(desktop)
    phone_controls = control_signature(phone)
    if screenshot_dir:
        desktop.screenshot(path=os.path.join(screenshot_dir, "queue-desktop.png"))
        phone.screenshot(path=os.path.join(screenshot_dir, "queue-phone.png"))
    assert desktop_controls == phone_controls, (desktop_controls, phone_controls)
    assert desktop_controls["inputWrap"] == "nowrap"

    phone_layout = phone.evaluate(
            "() => {"
            " const stack = document.querySelector('.chat-queue-stack');"
            " const text = document.querySelector('.chat-qrow-txt');"
            " return {"
            "   stackHeight: stack.clientHeight,"
            "   stackScrollHeight: stack.scrollHeight,"
            "   textWhiteSpace: getComputedStyle(text).whiteSpace,"
            "   documentWidth: document.documentElement.scrollWidth,"
            "   viewportWidth: window.innerWidth,"
            " };"
            "}"
        )
    assert phone_layout["stackScrollHeight"] > phone_layout["stackHeight"]
    assert phone_layout["textWhiteSpace"] == "nowrap"
    assert phone_layout["documentWidth"] <= phone_layout["viewportWidth"]
    # The queue-only host uses TicketRoute's actual named size container and column
    # rules. A narrow test column must not conceal clipped controls off the phone.
    phone_bounds = phone.evaluate("""() => {
        const column = document.querySelector('.conversation-column').getBoundingClientRect();
        const pane = document.querySelector('[data-conversation-pane]').getBoundingClientRect();
        const controls = [...document.querySelectorAll(
            '[data-conversation-held-row] button, .chat-foot button'
        )].map(button => {
            const bounds = button.getBoundingClientRect();
            return { label: button.getAttribute('aria-label') || button.textContent.trim(),
                left: bounds.left, right: bounds.right, width: bounds.width };
        });
        return { columnWidth: column.width, viewportWidth: innerWidth,
            paneLeft: pane.left, paneRight: pane.right, controls };
    }""")
    assert abs(phone_bounds["columnWidth"] - phone_bounds["viewportWidth"]) <= 1, phone_bounds
    assert 0 <= phone_bounds["paneLeft"] < phone_bounds["paneRight"] <= phone_bounds["viewportWidth"], phone_bounds
    for control in phone_bounds["controls"]:
        assert control["width"] > 0, control
        assert phone_bounds["paneLeft"] <= control["left"] < control["right"] <= phone_bounds["paneRight"], control

    desktop.click('[data-conversation-held-promote="steer"][data-held-prompt-id="held-1"]')
    desktop.wait_for_function("document.querySelectorAll('[data-conversation-held-row]').length === 5")
    assert actions[-1] == ["held-1", "steer"]
    phone.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    phone.wait_for_function("document.querySelectorAll('[data-conversation-held-row]').length === 5")
    assert "queued message 1" not in phone.inner_text("[data-conversation-held-stack]")
    phone.click('[data-conversation-held-discard="held-2"]')
    phone.wait_for_function("document.querySelectorAll('[data-conversation-held-row]').length === 4")
    assert actions[-1] == ["held-2", "discard"]
    desktop.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    desktop.wait_for_function("document.querySelectorAll('[data-conversation-held-row]').length === 4")
    desktop.click('[data-conversation-held-promote="send_now"][data-held-prompt-id="held-3"]')
    desktop.wait_for_function("document.querySelectorAll('[data-conversation-held-row]').length === 3")
    assert actions[-1] == ["held-3", "send_now"]
    desktop.close()
    phone.close()
    assert not browser_errors, browser_errors

    context.tracing.stop()

print("conversation-pane-browser.test.mjs: all assertions passed")
`;
  const probe = spawn(
    join(repositoryRoot, ".venv", "bin", "python"),
    ["-c", browserScript, url],
    { cwd: repositoryRoot, stdio: ["ignore", "pipe", "pipe"] }
  );
  let output = "";
  probe.stdout.on("data", (chunk) => { output += chunk; });
  probe.stderr.on("data", (chunk) => { output += chunk; });
  const exitCode = await new Promise((resolve) => probe.on("close", resolve));
  assert.equal(exitCode, 0, output);
  assert.match(output, /conversation-pane-browser\.test\.mjs: all assertions passed/);
  console.log(output.trim());
} finally {
  serverProcess?.kill();
  await rm(hostPath, { force: true });
  await rm(mainPath, { force: true });
  await rm(indexPath, { force: true });
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
  await new Promise((resolve) => server.close(resolve));
  return address.port;
}

async function waitUntilReady(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`static component host did not become ready: ${url}`);
}
