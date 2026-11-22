import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteSet } from "svelte/reactivity";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import AskShell from "../src/components/conversation/AskShell.svelte";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";
import ProjectedTranscript from "../src/components/conversation/view/ProjectedTranscript.svelte";
import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";
import { conversationState } from "../src/lib/conversation/dockPresentation";
import type { ToolCallPresentation } from "../src/lib/conversation/toolCallPresentation";
import type { ConversationViewItem, ConversationViewPage, ViewHistoryScope } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const pane = source("../src/components/conversation/ConversationPane.svelte");

describe("design v5: empty and header", () => {
  it("an empty conversation says one quiet centred line (v5.1 words), 15px #b0b0b0", () => {
    const live = source("../src/components/conversation/LiveConversation.svelte");
    expect(live).toContain('<span class="conversation-empty-said">Start with a message. You can attach or drop files here.</span>');
    expect(live).toContain(".conversation-empty-said { color: #b0b0b0; font: var(--cv-weight-body) 15px/24px var(--cv-font); text-align: center; }");
    expect(live).not.toMatch(/\.conversation-empty \{[^}]*clip-path/);
  });

  it("v10 (7): the header title is 17/24 semibold, alone, and the collapse control is a Dock button", () => {
    expect(pane).toContain("font: var(--cv-weight-title) 17px/24px var(--cv-font);");
    expect(pane).not.toContain("data-conversation-subtitle");
    expect(pane).toContain('{stateControl.opensIt ? "Open conversation" : "Dock"}');
    // v7: Dock is a Button: a 32 tall pill. v10 (5): the resting #232323 pill, #d9d9d9.
    expect(pane).toMatch(/\.chat-head-collapse \{[^}]*height: 32px;[^}]*border-radius: 999px;/);
    expect(pane).toContain(".chat-head-collapse { background: #232323; color: #d9d9d9; }");
  });
});

describe("design v5: tones and accent, scoped to the conversation", () => {
  it("quiet text #b0b0b0, placeholders #999999, accent #bccbf0 inside the pane only", () => {
    const scoped = tokens.slice(tokens.indexOf("[data-conversation-pane] {"));
    expect(scoped).toContain("--cv-t3: #b0b0b0;");
    expect(scoped).toContain("--cv-t4: #b0b0b0;");
    expect(scoped).toContain("--cv-placeholder: #999999;");
    expect(scoped).toContain("--cv-accent: #bccbf0;");
    expect(scoped).toContain("--cv-data-1-strong: #bccbf0;");
    expect(scoped).toContain("--cv-data-2-strong: #e0b36b;");
    // The page around it keeps its own.
    expect(tokens).toContain("--cv-t3: #a3a3a3;");
  });
});

describe("design v5: asks", () => {
  it("a permission ask says Needs your permission behind a 12px accent shield; a question keeps the dot", () => {
    const permission = render(AskShell, { props: { kind: "permission", eyebrow: "Needs your permission", title: "Upload" } }).body;
    expect(permission).toContain("data-ask-shield");
    expect(permission).not.toContain("cv-ask-dot");
    const question = render(AskShell, { props: { kind: "question", eyebrow: "Needs your answer", title: "Which?" } }).body;
    expect(question).toContain("cv-ask-dot");
    expect(question).not.toContain("data-ask-shield");
    expect(source("../src/components/conversation/AskShell.svelte")).toMatch(/\.cv-ask-shield \{[^}]*width: 12px;[^}]*stroke: var\(--cv-accent\);/);
  });

  it("number keycaps are #333333 with a #d9d9d9 number", () => {
    expect(source("../src/components/conversation/AskShell.svelte")).toMatch(/:global\(\.cv-ask-key\) \{[^}]*background: #333333;\s*color: #d9d9d9;/);
  });
});

describe("design v5.1: turn timing at the top, Paused under a waiting ask", () => {
  const turn: ConversationViewItem = {
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled: false, stopped: false, started_at_unix_milliseconds: 100_000, ending: null,
    duration_seconds: null, tool_call_count: 0, folded_message_count: 0, latest_message_key: null, is_latest: true
  } as ConversationViewItem;
  const draw = (status: Record<string, unknown>): string => {
    const pages: ConversationViewPage[] = [{
      conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "focus",
      scope: { kind: "timeline", key: null } as ViewHistoryScope, items: [turn], older: null, newer: null, body_bytes: 0
    } as ConversationViewPage];
    const reader = {
      id: "c", lens: "focus", pages, coverage: 5, entryPhase: "ready",
      status: { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, ...status },
      restorationAnchor: null, restorationAttempt: 0, discardedBodies: 0, openTools: new SvelteSet<string>(),
      isExpanded: () => false, expand: async () => {}, load: async () => {}, textBodyReady: () => {}, failTextRestoration: () => {},
      body: async () => ""
    } as unknown as ConversationViewReader;
    return render(ProjectedTranscript, { props: { reader, models: [], ownSenderLabel: null, livenessPulse: 0 } }).body;
  };

  it("v6: a running turn's top draws nothing (its live line is the live edge) and no Paused line", () => {
    const html = draw({});
    expect(html).not.toContain("data-conversation-alive-word");
    expect(html).not.toContain("data-conversation-paused");
  });

  it("p12c: under a waiting ask the transcript ends at the last message; the Paused line is the composer's", () => {
    const html = draw({ pending_permission: { kind: "permission", request_id: "a1" } });
    expect(html).not.toContain("data-conversation-paused");
    expect(html).not.toContain("data-conversation-alive-word");
    // v6: no Paused line anywhere; the ask card's own label is the one waiting label.
    const composer = source("../src/components/conversation/ConversationComposer.svelte");
    expect(composer).not.toContain("chat-paused");
  });

  it("v6: with the ask card showing under it, the dock's status row is empty on the left", () => {
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: true, waitingFor: "approval" as const, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts), askBelow: true } }).body;
    expect(html).not.toContain("Paused");
    expect(html).not.toContain("data-conversation-rest-line");
    expect(html).toContain("Open conversation");
  });

  it("p12c: the dock's Working · time is spaced as the transcript's (three parts, 6px apart)", () => {
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: false, waitingFor: null, running: true, backgroundWorking: false, settingUp: null, elapsed: "2m 14s" };
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts as never) } }).body;
    expect(html).toMatch(/c2-rest-label[^>]*>Working<\/span><span class="c2-rest-sep[^"]*" aria-hidden="true">·<\/span><span class="c2-rest-clock/);
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain(".c2-rest-working { display:inline-flex; align-items:center; gap:6px; }");
  });

  it("v9 (1): the running run keeps its static icon and says Running; the Working line has the one spinner; one line, chevron after it", () => {
    const running = { title: "Running", summary: "go test -race ./internal/push/...", summaryIsLiteral: true, sentence: "Running go test" } as unknown as ToolCallPresentation;
    const html = render(ToolRunSummary, { props: { summary: { sentence: "Ran 2 commands", failedCount: 0, running, iconPaths: [] }, callCount: 3, onToggle: () => {} } }).body;
    expect(html).not.toContain("data-conversation-run-spinner");
    expect(html).toMatch(/<svg class="c2-run-icon[^"]*"[^>]*data-conversation-run-icon="running"/);
    expect(html).toMatch(/c2-run-verb[^>]*>Running</);
    const summary = source("../src/components/conversation/ToolRunSummary.svelte");
    expect(summary).not.toContain("line-clamp");
    expect(summary).not.toContain("c2-run-spin");
    expect(summary).toContain(".c2-run-summary.is-live .c2-run-verb { margin-inline-end: 2px; color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }");
    const call = source("../src/components/conversation/ToolCallRow.svelte");
    expect(call).not.toContain("data-conversation-tool-spinner");
    expect(call).not.toContain("c2-tool-spin");
    expect(call).toContain('.acp-step[data-conversation-tool-status="running"] .c2-tool-verb { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }');
    expect(html.indexOf("c2-run-subject")).toBeLessThan(html.indexOf("c2-run-chevron"));
    expect(source("../src/components/conversation/ToolCallRow.svelte")).not.toContain("data-conversation-tool-elapsed");
  });

  it("v7: the Working label is a status label, 13/20 500 #ececec", () => {
    expect(source("../src/components/conversation/TurnAnchor.svelte")).toContain(".c2-meta-working { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }");
  });
});

describe("design v5: errors", () => {
  const status = { tone: "error" as const, title: "The turn stopped because the model's rate limit was reached.", detail: "429", next: "Send a message once the limit resets." , action: "choose-model" as const };
  it("a callout counts as the newest until measured; v6: an older one is an inline status line", () => {
    const html = render(TranscriptStatusRow, { props: { status } }).body;
    expect(html).toContain('data-conversation-callout-newest="true"');
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain("registerCallout(root");
    expect(row).toContain('data-conversation-error-inline');
  });
  it("v7: Details is a quiet Button, 13/20 500 #bdbdbd", () => {
    expect(source("../src/components/conversation/TranscriptStatusRow.svelte")).toMatch(/\.c2-status-toggle \{[^}]*height: 32px;[^}]*color: var\(--cv-quiet-ink, #bdbdbd\);\s*font-size: 13px;/);
  });
});

describe("design v5: controls", () => {
  it("v5.4: Jump to latest is a 32px round button straddling the composer's top edge, centred on the column", () => {
    expect(app).toMatch(/\.cv-jump \{[^}]*position: absolute;\s*left: 50%;\s*bottom: -16px;\s*transform: translateX\(-50%\);[^}]*width: 32px;\s*height: 32px;[^}]*border: 1px solid #3a3a3a;\s*border-radius: 50%;\s*background: #262626;\s*color: #d9d9d9;/);
    expect(app).not.toContain(".chat-paused");
    expect(pane).not.toContain("</svg>{jump.label}</button>");
  });
  it("v5.4: it shows only when the newest content is more than one viewport below the visible area", async () => {
    const { jumpToLatestWanted } = await import("../src/components/conversation/viewport/threadGeometry");
    expect(jumpToLatestWanted(0, 600)).toBe(false);
    expect(jumpToLatestWanted(300, 600)).toBe(false);
    expect(jumpToLatestWanted(600, 600)).toBe(false);
    expect(jumpToLatestWanted(601, 600)).toBe(true);
    expect(jumpToLatestWanted(5000, 0)).toBe(false);
    const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
    expect(viewport).toContain("const jumpShown = $derived(newestLineFarBelow || newerHistory || newerThanUnplacedAnchor || latestPending);");
  });
  it("dropdowns fit their longest label (min 160); v6: the composer's open 8px above the composer's top edge", () => {
    const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");
    expect(mode).toMatch(/bottom: calc\(100% \+ 8px \+ var\(--c2-menu-lift, 0px\)\);\s*width: max-content;\s*min-width: 160px;/);
    const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
    expect(held).toMatch(/bottom: calc\(100% \+ 8px\);\s*width: max-content;\s*min-width: 160px;/);
  });
  it("p12c: an open chip (Steer/Queue, model, Queued) is #3a3a3a with #f2f2f2 words; v8 (14): no chevron", () => {
    expect(app).toContain("[data-conversation-composer] .chat-foot [data-conversation-model-picker] > .listbox-picker-trigger.is-open:hover:not(:disabled) { position: relative; z-index: 41; background: #3a3a3a; color: #f2f2f2; }");
    const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");
    expect(mode).toMatch(/trigger\.is-open:hover:not\(:disabled\)\) \{\s*background: #3a3a3a;\s*color: #f2f2f2;/);
    expect(mode).toMatch(/background: #3a3a3a;\s*color: #f2f2f2;\s*\/\*[^*]*\*\/\s*position: relative;\s*z-index: 41;/);
    const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
    expect(held).toContain('.chat-qrow-chip[aria-expanded="true"], .chat-qrow-chip[aria-expanded="true"]:hover:not(:disabled) { position: relative; z-index: 31; background: #3a3a3a; color: #f2f2f2; }');
    expect(held).not.toContain("chat-qrow-chevron");
    expect(mode).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).toContain('chevron="none"');
  });
  it("Stop is #f2f2f2 with a #121212 square; v9 (9): disabled send #2e2e2e with a #a6a6a6 arrow", () => {
    expect(app).toContain(".chat-send.stop { background: #f2f2f2; color: #121212; }");
    expect(app).toMatch(/\.chat-send \{[^}]*background: #2e2e2e;\s*color: #a6a6a6;/);
  });
});

describe("design v5.2: surfaces", () => {
  it("v6.1 (27): the dock has no surface of its own; it sits on the canvas with its canvas fade", () => {
    expect(app).not.toContain("#161616");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain("background:linear-gradient(to bottom, transparent, var(--cv-canvas));");
  });
  it("a waiting proposal's dock line says Needs your approval", () => {
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: true, waitingFor: "approval" as const, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts), pageHoldsTheAsk: true } }).body;
    expect(html).toContain("Needs your approval");
  });
  it("v6: tables: head 14 semibold #f2f2f2, body 15 regular #d9d9d9", () => {
    expect(app).toContain(".chat-thread .markdown th { color: #f2f2f2; font-size: 14px; line-height: 20px; }");
    expect(app).toContain(".chat-thread .markdown td { color: #d9d9d9; font-size: 15px; line-height: 22px; font-weight: var(--cv-weight-body); }");
    expect(app).not.toContain(".chat-thread .markdown td:first-child { color: var(--cv-t1); }");
  });
  it("sent pictures up to 320 tall; composer picture chips 76 tall", () => {
    expect(app).toMatch(/\.chat-u \.file-preview-media > :is\(\.file-preview-image, \.file-preview-video-poster\) \{\s*max-height: 320px;/);
    const composer = source("../src/components/conversation/ConversationComposer.svelte");
    expect(composer).toMatch(/\.chat-att\.img \{ width: 76px; height: 76px;/);
    // p12c: the file chip is as tall, its content centred.
    expect(composer).toMatch(/\.chat-att\.file \{\s*height: 76px;\s*align-items: center;/);
  });
});

describe("design v5.1: inline code spacing in the markdown rendering", () => {
  it("marks a code span followed by closing punctuation, and one that opens a list item", async () => {
    const { inlineCodeSpacing } = await import("../src/lib/markdownPipeline");
    // "at `batcher.go:97`, read" — the comma sits against the chip.
    expect(inlineCodeSpacing("P", ["at ", null, ", read"], 1)).toEqual(["cv-code-before-punctuation"]);
    expect(inlineCodeSpacing("P", ["at ", null, "."], 1)).toEqual(["cv-code-before-punctuation"]);
    expect(inlineCodeSpacing("P", ["at ", null, " while"], 1)).toEqual([]);
    // "- `flush()` swaps" — the chip opens the item, so its pad hangs outside the text edge.
    expect(inlineCodeSpacing("LI", [null, " swaps"], 0)).toEqual(["cv-code-leads"]);
    expect(inlineCodeSpacing("LI", ["\n", null, ")"], 1)).toEqual(["cv-code-before-punctuation", "cv-code-leads"]);
    expect(inlineCodeSpacing("LI", ["read ", null], 1)).toEqual([]);
    expect(inlineCodeSpacing("P", [null, " swaps"], 0)).toEqual([]);
  });
  it("the stylesheet pulls the chip's edge in for both", () => {
    expect(app).toContain(".chat-thread .markdown :not(pre) > code.cv-code-before-punctuation { padding-inline-end: 1px; }");
    expect(app).toContain(".chat-thread .markdown li > code.cv-code-leads { margin-inline-start: -4px; }");
  });
});
