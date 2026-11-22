import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteSet } from "svelte/reactivity";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";
import ProjectedTranscript from "../src/components/conversation/view/ProjectedTranscript.svelte";
import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import UserMessage from "../src/components/conversation/UserMessage.svelte";
import { conversationState } from "../src/lib/conversation/dockPresentation";
import type { ToolCallPresentation, ToolRunSummary as Summary } from "../src/lib/conversation/toolCallPresentation";
import type { ConversationViewItem, ConversationViewPage, ViewHistoryScope } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const shell = source("../src/components/conversation/AskShell.svelte");
const composer = source("../src/components/conversation/ConversationComposer.svelte");

describe("design v4.2: the dock line under which an ask waits", () => {
  const facts = { readOnly: false, activityUncertain: false, waitingForYou: true, waitingFor: "approval" as const, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
  const draw = (props: Record<string, unknown>) =>
    render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts), waitingTitle: "Upload Wren build 41 to TestFlight", ...props } }).body;

  it("carries no status text when an ask is the composer under it: only Open conversation", () => {
    for (const pageHoldsTheAsk of [false, true]) {
      const html = draw({ askBelow: true, pageHoldsTheAsk, preview: "An unread line" });
      expect(html).toContain("Open conversation");
      expect(html).not.toContain("data-conversation-rest-line");
      expect(html).not.toContain("Needs your approval");
      expect(html).not.toContain("Waiting for your approval");
      expect(html).not.toContain("Upload Wren build 41");
      expect(html).not.toContain("cv-dot");
      expect(html).not.toContain("An unread line");
    }
  });

  it("keeps its line when no ask is below: a proposal alone says Needs your approval (v5: the card's words)", () => {
    expect(draw({ pageHoldsTheAsk: true })).toContain("Needs your approval");
    expect(draw({ pageHoldsTheAsk: true })).not.toContain("Waiting for your approval");
    expect(draw({})).toContain("Needs your approval");
  });

  it("the pane says an ask is below whenever a permission ask, a question or a key request waits", () => {
    expect(source("../src/components/conversation/ConversationPane.svelte")).toContain("askBelow={ask !== null || userInput !== null || credentialWaiting}");
  });
});

describe("design v4.2: asks", () => {
  it("answer tiles are filled #262626 at rest, #2c2c2c on hover, the chosen one #2e2e2e with the accent edge (v5)", () => {
    expect(shell).toMatch(/:global\(\.cv-ask-row\) \{[^}]*background: #262626;/);
    expect(shell).toContain(":global(.cv-ask-row:hover:not(:disabled)) { background: #2c2c2c; }");
    expect(shell).toContain("background: #2e2e2e; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%);");
  });

  it("the pager's arrows are the meta tone; a disabled one is #8c8c8c (v5.1)", () => {
    expect(shell).toContain(".cv-ask-context :global(.cv-icon-button) { color: var(--cv-t3); }");
    expect(shell).toContain(".cv-ask-context :global(.cv-icon-button:disabled) { color: #8c8c8c; }");
  });

  it("the disabled primary pill reads #a3a3a3 on #2a2a2a", () => {
    expect(composer).toContain(".chat-box :global(.chat-ask-primary:disabled) { background: #2a2a2a; color: var(--cv-t3); cursor: default; }");
  });

  it("the own-answer line sits in a #141414 well, radius 12, 44 high, the pill outside it on the right", () => {
    expect(composer).toMatch(/\.chat-box\.has-ask\.is-compact > \.chat-foot > \.chat-ask-pen \{[^}]*min-height: 44px;[^}]*border-radius: 12px 0 0 12px;[^}]*background: #141414;/);
    expect(composer).toMatch(/\.chat-box\.has-ask\.is-compact > \.chat-ta \{[^}]*padding-block: 10px;[^}]*background: #141414;/);
    expect(composer).toMatch(/\.chat-submit > \.chat-voice-mic\) \{[^}]*border-radius: 0 12px 12px 0;[^}]*background: #141414;/);
    expect(composer).toContain(".chat-box.has-ask.is-compact:not(:has(.chat-voice-mic)) > .chat-ta { border-radius: 0 12px 12px 0; }");
    expect(composer).toMatch(/:global\(\.chat-submit > \.chat-ask-primary\) \{ order: 3; margin-inline: var\(--cv-s2\); \}/);
  });
});

describe("design v4.2: one primary button style", () => {
  it("cv-button--primary is #f2f2f2 with #121212 words, radius 18", () => {
    expect(app).toContain(".cv-button--primary { border-radius: 18px; padding-inline: var(--cv-s4); background: #f2f2f2; color: #121212; }");
  });
  it("an error callout's first action wears it; the others are bare #d9d9d9", () => {
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(":global(.c2-status-main) { background: #f2f2f2; color: #121212; }");
    expect(row).toContain(".c2-status-actions > :global(.c2-status-second:first-child) { background: #f2f2f2; color: #121212; }");
    expect(row).toContain(":global(.c2-status-second) { background: transparent; color: var(--cv-t2); }");
    expect(row).toMatch(/:global\(\.c2-status-main\), :global\(\.c2-status-second\) \{[^}]*border-radius: 18px;/);
  });
});

describe("design v4.2: messages", () => {
  it("a queued message's words are #d9d9d9 in the ghost frame", () => {
    const stack = source("../src/components/conversation/composer/HeldPromptStack.svelte");
    expect(stack).toMatch(/\.chat-qrow :global\(\.c2-bubble\) \{[^}]*rgb\(255 255 255 \/ 16%\);[^}]*color: var\(--cv-t2\);/);
  });

  it("an undelivered message has the small red ! beside its bubble again", () => {
    expect(source("../src/components/conversation/UserMessage.svelte")).toMatch(/\.chat-u\.undelivered :global\(\.c2-bubble\)::before \{[^}]*right: calc\(100% \+ 10px\);[^}]*background: #e5848a;/);
  });

  it("a message from Panels (v5): a small well, \"From Panels\" on its own line, then the words, #b0b0b0, no arrow", () => {
    const row = { key: "e1", kind: "prompt" as const, sequence: 1, createdAt: 1, senderLabel: "Panels", senderMessageId: null,
      content: [{ piece: "text" as const, text: "Continue the Ticket at Stage: Problem understanding." }] };
    const html = render(UserMessage, { props: { row, conversationId: "c", ownSenderLabel: "owner" } }).body;
    expect(html).toMatch(/data-conversation-prompt-label[^>]*>From Panels</);
    expect(html).not.toContain("Panels:");
    const note = html.slice(html.indexOf("c2-system-row"), html.indexOf("message-status-window"));
    expect(note).not.toContain("<svg");
    const css = source("../src/components/conversation/UserMessage.svelte");
    expect(css).toMatch(/\.c2-system-body \{[^}]*flex-direction: column;/);
    expect(css).toMatch(/\.c2-system-row \{[^}]*padding: 10px 14px;[^}]*border-radius: 10px;[^}]*background: #191919;/);
    expect(css).toMatch(/\.c2-label \{[^}]*color: #b0b0b0;[^}]*font: var\(--cv-weight-label\) 12px\/16px/);
    expect(css).toMatch(/\.c2-system-body :global\(\.markdown\) \{[^}]*color: #b0b0b0;[^}]*font-size: 14px;/);
  });


});

describe("design v4.2: Full view tool rows", () => {
  const closed: Summary = { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: [] };

  it("are quieter than prose: label #a3a3a3, icon #8c8c8c", () => {
    const summary = source("../src/components/conversation/ToolRunSummary.svelte");
    expect(summary).toMatch(/\.c2-run-icon \{[^}]*stroke: #8c8c8c;/);
    expect(summary).toMatch(/\.c2-run-verb \{[^}]*color: var\(--cv-t3\);/);
    const call = source("../src/components/conversation/ToolCallRow.svelte");
    expect(call).toMatch(/\.c2-tool-icon \{[^}]*stroke: #8c8c8c;/);
    expect(call).toMatch(/\.c2-tool-verb \{[^}]*color: var\(--cv-t3\);/);
  });

  it("closed: \"Ran 2 commands\" then the chevron", () => {
    const html = render(ToolRunSummary, { props: { summary: closed, callCount: 2, onToggle: () => {} } }).body;
    expect(html.indexOf("Ran 2 commands")).toBeLessThan(html.indexOf("c2-run-chevron"));
  });

  it("running: \"Running\", the command in mono, then the chevron at the row's end", () => {
    const running = { title: "Running", summary: "go test -race ./internal/push/...", summaryIsLiteral: true, sentence: "Running go test" } as unknown as ToolCallPresentation;
    const html = render(ToolRunSummary, { props: { summary: { ...closed, running }, callCount: 3, onToggle: () => {} } }).body;
    const verb = html.indexOf(">Running<");
    const command = html.indexOf("go test -race");
    const chevron = html.indexOf("c2-run-chevron");
    expect(verb).toBeGreaterThan(-1);
    expect(verb).toBeLessThan(command);
    expect(command).toBeLessThan(chevron);
    expect(html).toMatch(/class="c2-run-subject[^"]*c2-run-subject--literal/);
  });
});

describe("design v4.2: the Working line is always the last thing in the transcript", () => {
  const turn = (settled: boolean): ConversationViewItem => ({
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled, stopped: false, started_at_unix_milliseconds: 100_000, ending: settled ? "completed" : null,
    duration_seconds: settled ? 420 : null, tool_call_count: 2, folded_message_count: 0,
    latest_message_key: null, is_latest: true
  });
  const group: ConversationViewItem = {
    kind: "work_group", key: "work:e2", order: { sequence: 2, slot: 0 }, changed_through_sequence: 4,
    turn_key: "turn:e1", entry_count: 2, latest_entry_key: "e3", settled: false
  };
  const tool = (sequence: number, command: string, status: "completed" | "running"): ConversationViewItem => ({
    kind: "row", key: `e${sequence}`, row_kind: "tool_call", order: { sequence, slot: 1 },
    source_sequence: sequence, sender_message_id: null, changed_through_sequence: sequence,
    created_at: sequence, turn_key: "turn:e1", work_group_key: "work:e2", fold_rule: "work",
    body: { kind: "inline", value: {
      kind: "tool_call", tool_call_id: `t${sequence}`, title: command, tool_kind: "execute",
      detail: "ok", started_detail: "/w", capped_detail_sequence: null, status
    } }
  } as ConversationViewItem);
  const page = (scope: ViewHistoryScope, items: ConversationViewItem[]): ConversationViewPage => ({
    conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "full", scope, items, older: null, newer: null, body_bytes: 0
  });
  const draw = (settled: boolean): string => {
    const pages = [
      page({ kind: "timeline", key: null }, [turn(settled), group]),
      page({ kind: "work_group", key: "work:e2" }, [tool(3, "npm run check", "completed"), tool(4, "go test -race ./...", settled ? "completed" : "running")])
    ];
    const reader = {
      id: "c", lens: "full", pages, coverage: 5, entryPhase: "ready",
      status: { turn_key: "turn:e1", pending_permission: null, pending_user_input: null },
      restorationAnchor: null, restorationAttempt: 0, discardedBodies: 0, openTools: new SvelteSet<string>(),
      isExpanded: () => false, expand: async () => {}, load: async () => {}, textBodyReady: () => {}, failTextRestoration: () => {},
      body: async () => ""
    } as unknown as ConversationViewReader;
    return render(ProjectedTranscript, { props: { reader, models: [], ownSenderLabel: null, livenessPulse: 0 } }).body;
  };

  it("v5.1: while the turn runs, Working · time heads the turn, above its running tool row; nothing counts at the tail", () => {
    const html = draw(false);
    const working = html.indexOf("data-conversation-alive-word");
    const run = html.indexOf("data-conversation-work-group");
    expect(working).toBeGreaterThan(-1);
    expect(run).toBeGreaterThan(-1);
    expect(working).toBeLessThan(run);
    expect(html).not.toContain("data-conversation-live-tail");
    expect(html).not.toContain("data-conversation-tool-elapsed");
  });

  it("once settled, the turn's meta line heads the turn again, over its work", () => {
    const html = draw(true);
    expect(html).not.toContain("data-conversation-live-tail");
    expect(html.indexOf("data-conversation-turn-label")).toBeLessThan(html.indexOf("data-conversation-work-group"));
  });
});

describe("design v4.3", () => {
  const tokens = source("../../assets/tokens.css");
  it("the opened conversation is one 680px column; docked at rest it keeps the page's column", () => {
    expect(tokens).toContain("--cv-col: 680px;");
    expect(app).toMatch(/\[data-conversation-dock-state="opened"\] > \.conversation-layer > \.conversation-column \{\s*width: min\(var\(--cv-col\), calc\(100% - 2 \* var\(--space-5\)\)\);/);
  });
  it("inline code is lighter: 6% white fill, radius 4, padding 0 4px, #e0e0e0", () => {
    expect(app).toMatch(/\.chat-thread \.markdown :not\(pre\) > code,\n\.cv-code \{\s*padding: 0 4px;[^}]*border-radius: 4px;[^}]*background: rgb\(255 255 255 \/ 6%\);[^}]*color: #e0e0e0;/);
  });
  it("an attachment's remove button is a 20px #2a2a2a circle inside the tile's corner, inset 6", () => {
    expect(composer).toMatch(/\.chat-att-x \{\s*top: 6px;\s*right: 6px;\s*width: 20px;\s*height: 20px;[^}]*border-radius: 50%;\s*background: #2a2a2a;\s*color: #d9d9d9;/);
    expect(composer).not.toMatch(/\.chat-att-x \{[^}]*top: -/);
  });
  it("rhythm: 32 between turns, 6 from a meta line to its prose, 12 between paragraphs", () => {
    expect(tokens).toMatch(/--cv-gap-turn: 32px;/);
    expect(tokens).toMatch(/--cv-gap-meta: 6px;/);
    expect(tokens).toMatch(/--cv-gap-paragraph: 12px;/);
  });
});

describe("pass 11 strict review fixes", () => {
  it("v5: the header carries the title alone, never the role", () => {
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    expect(pane).toContain('<div class="chat-head-identity" data-conversation-head-identity>');
    expect(pane).not.toContain("data-conversation-subtitle");
  });
  it("a file tile sits on #242424 so the #2a2a2a remove circle reads", () => {
    expect(composer).toMatch(/\.chat-att\.file \{[^}]*background: #242424;/);
  });
});

describe("pass 11 manager review of p11b", () => {
  const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
  it("v5: under the solid header a 24px fade starts right at its bottom edge", () => {
    expect(viewport).toContain("--c2-top-fade: transparent 0, #000 24px;");
  });
  it("while newer content is below the view, the bottom edge dissolves over 24px above the composer", () => {
    expect(viewport).toContain("--c2-bottom-fade: #000 calc(100% - 24px), transparent 100%;");
    expect(viewport).toMatch(/\.chat-thread\[data-more-below="true"\] \{[^}]*mask-image: linear-gradient\(to bottom, var\(--c2-top-fade\), var\(--c2-bottom-fade\)\);/);
  });
  it("24px before the lens control", () => {
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    expect(pane).toMatch(/\.chat-head-identity \{[^}]*margin-inline-end: 12px;/);
  });
});
