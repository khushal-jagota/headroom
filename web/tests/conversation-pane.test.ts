import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { SvelteSet } from "svelte/reactivity";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PaneWithQueryClient from "./fixtures/PaneWithQueryClient.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";
import type { ConversationViewItem, CredentialRequestRowBody } from "../src/lib/conversation/view/contracts";
import { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

const base = { conversationId: "c-1", draftKey: "conversation-pane", onSend: async () => true };
const ask = { askId: "a-1", title: "Upload build 41", detail: null, options: [{ option_id: "allow", label: "Allow once", option_kind: "allow_once" as const }] };
const userInput = { requestId: "q-1", questions: [
  { question_id: "a", header: "Style", question: "Which style?", multi_select: false, allow_other: true, options: [{ label: "Soft", description: "" }] }
] };
const keyRequest: CredentialRequestRowBody = {
  kind: "credential_request", request_id: "r-1", name: "SENTRY_AUTH_TOKEN", provider: "Sentry",
  use: "Upload dSYMs from CI.", auth_kind: "bearer", header_name: null, fields: null, help: null, help_url: null,
  replacement: null, state: "pending", credential: null, delivery: null
};

function held(key: string, text: string): HeldPromptRow {
  return {
    key, heldPromptId: `hp-${key}`, senderMessageId: null, content: [{ piece: "text", text }],
    senderLabel: "owner", sentAtUnixMilliseconds: 1, state: "held", queueReason: null
  };
}

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;

describe("a waiting ask in the docked conversation", () => {
  it("is the composer at rest, and the dock line gives it the preview's room", () => {
    for (const [props, label] of [
      [{ ask }, "Needs your permission"],
      [{ userInput }, "Needs your answer"],
      [{ recordedCredentialRequest: keyRequest }, "Needs a key"]
    ] as const) {
      const body = render(PaneWithQueryClient, { props: { ...base, conversationState: "rest", ...props } }).body;
      expect(body).toContain(label);
      expect(body).toContain("data-conversation-rest-ask-below");
    }
    const quiet = render(PaneWithQueryClient, { props: { ...base, conversationState: "rest" } }).body;
    expect(quiet).toContain("data-conversation-rest-bar");
    expect(quiet).not.toContain("data-conversation-rest-ask-below");
  });
});

describe("a waiting ask in the opened conversation", () => {
  it("marks the transcript region open over the ask, for a question, a permission ask or a key request", () => {
    for (const props of [{ ask }, { userInput }, { recordedCredentialRequest: keyRequest }]) {
      const body = render(PaneWithQueryClient, { props: { ...base, conversationState: "opened", running: true, ...props } }).body;
      expect(body).toMatch(/conversation-transcript-region[^>]*data-conversation-ask-open/);
    }
    const quiet = render(PaneWithQueryClient, { props: { ...base, conversationState: "opened", running: true } }).body;
    expect(quiet).not.toContain("data-conversation-ask-open");
  });

  it("draws no Waiting line in the transcript: the ask says it waits", () => {
    const body = render(PaneWithQueryClient, { props: { ...base, conversationState: "opened", running: true, ask } }).body;
    expect(body).not.toContain("Waiting for you");
    expect(body).not.toContain("data-conversation-waiting-line");
  });
});

describe("the header", () => {
  it("shows archive preparation until the reader recovers", () => {
    const reader = new ConversationViewReader("c-1", "focus", true);
    const pane = () => render(PaneWithQueryClient, {
      props: { ...base, conversationState: "opened", readOnly: true, reader }
    }).body;
    reader.preparingHistory = true;
    expect(pane()).toContain("Preparing conversation history");
    expect(pane()).not.toContain("The conversation did not load");
    reader.preparingHistory = false;
    expect(pane()).not.toContain("Preparing conversation history");
    reader.close();
  });

  it("carries the title alone, never who does the work", () => {
    const body = render(PaneWithQueryClient, { props: { ...base, conversationState: "opened", title: "Vylo pricing", subtitle: "Coding worker" } }).body;
    expect(body).toMatch(/data-conversation-title[^>]*>Vylo pricing</);
    expect(body).not.toContain("Coding worker");
  });

  it("says the dock's state while the Worker is set up: no setup step, Machine offline when the Mac is away", () => {
    const workerSetup = { step: "preparing_workspace" as const, began_at: 1, machine_id: "mac" };
    const setting = render(PaneWithQueryClient, { props: { ...base, conversationState: "opened", workerSetup } }).body.replace(/<!--[^]*?-->/g, "");
    // The shared system line with its spinner; the clock joins once the page is live.
    expect(setting).toMatch(/data-conversation-head-status="setting-up"[^>]*><div class="cv-system-line[^>]*><div[^>]*><span[^>]*data-conversation-system-mark="spinner"[^]*?<span class="cv-system-line-words[^>]*>Setting up</);
    expect(setting).not.toContain("preparing workspace");
    for (const conversationState of ["opened", "rest"] as const) {
      const body = render(PaneWithQueryClient, { props: { ...base, conversationState, workerSetup, activityUncertain: true } }).body.replace(/<!--[^]*?-->/g, "");
      expect(body).toContain(">Machine offline</span>");
      expect(body).not.toContain("Setting up");
      expect(body).not.toContain('data-conversation-system-mark="spinner"');
    }
  });

  it("always shows the Focus/Full control", () => {
    for (const conversationState of ["opened", "rest"] as const) {
      const body = render(PaneWithQueryClient, { props: { ...base, conversationState } }).body;
      expect(body).toContain('data-conversation-lens-choice="focus"');
      expect(body).toContain('data-conversation-lens-choice="full"');
    }
  });

  it("says Close as a word alone, with no arrow", () => {
    const body = render(PaneWithQueryClient, { props: { ...base, conversationState: "opened" } }).body;
    const close = body.slice(body.indexOf("data-conversation-collapse"), body.indexOf("</button>", body.indexOf("data-conversation-collapse")));
    expect(close).toContain(">Close</span>");
    expect(close).not.toContain("<svg");
  });

  it("leads the options menu with Stop the agent while a turn is open, which stops through onStop", () => {
    // The menu opens on a click and the tests run without a DOM; the wiring is read from the source.
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    const actions = pane.slice(pane.indexOf('<div class="chat-overflow-actions" role="menu"'));
    const first = actions.indexOf("{#if running && onStop}");
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(actions.indexOf("{#if confirmArmed}"));
    expect(actions).toMatch(/data-conversation-menu-stop\s+onclick=\{stopFromTheMenu\}\s*>Stop the agent<\/button>/);
    expect(pane).toMatch(/function stopFromTheMenu\(\): void \{\s*closeMenu\(\);\s*onStop\?\.\(\);/);
  });
});

describe("queued messages in the pane", () => {
  it("wait in the tray on the composer's top edge, one line each, not in the thread", () => {
    const { body } = render(PaneWithQueryClient, {
      props: { ...base, conversationState: "opened", running: true, supportsSteer: true, heldPromptRows: [held("a", "After that, log each 429")] }
    });
    const content = body.indexOf("data-conversation-content");
    const stack = body.indexOf("data-conversation-held-stack");
    expect(content).toBeGreaterThan(-1);
    expect(stack).toBeGreaterThan(body.indexOf("data-conversation-composer"));
    expect(stack).toBeLessThan(body.indexOf("data-conversation-held-row"));
    expect(stack).toBeLessThan(body.indexOf("data-conversation-box"));
    const tray = body.slice(stack, body.indexOf("data-conversation-box"));
    expect(tray).toContain("After that, log each 429");
    expect(tray).not.toContain("c2-bubble");
    expect(tray).not.toContain("Queued ·");
    expect(tray).not.toContain("data-conversation-held-more");
    // One anchoring rule for every state: a short thread reads from the top.
    expect(body).not.toContain("data-anchor-end");
  });

  it("places the held box below an agent-started turn and its Working line", () => {
    const turn: ConversationViewItem = {
      kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
      settled: false, stopped: false, started_at_unix_milliseconds: 100_000, ending: null,
      duration_seconds: null, tool_call_count: 0, folded_message_count: 0, latest_message_key: null, is_latest: true
    };
    const header: ConversationViewItem = {
      kind: "row", key: "e1", row_kind: "agent_turn_started", order: { sequence: 1, slot: 0 },
      source_sequence: 1, sender_message_id: null, changed_through_sequence: 1, created_at: 100,
      turn_key: null, work_group_key: null, fold_rule: "never",
      body: { kind: "inline", value: { kind: "agent_turn_started", reason: "Sub-agent 'Check tests' finished" } }
    };
    const reader = {
      id: "c-1", lens: "focus", coverage: 5, entryPhase: "ready",
      pages: [{ conversation_id: "c-1", protocol_version: 1, projection_version: 1, lens: "focus", scope: { kind: "timeline", key: null }, items: [header, turn], older: null, newer: null, body_bytes: 0 }],
      status: {
        turn_key: "turn:e1", turn_running: true, pending_permission: null, pending_user_input: null, stopped_without_ending: false, background_tasks: [],
        rest: { latest_happened: null, latest_turn: null, latest_tool: null, permission_title_excerpt: null, latest_owner_message_preview: null, latest_owner_message_sequence: 0, last_boundary_failed: false,
          plan_progress: { total_count: 0, completed_count: 0, current_position: null, current_text_excerpt: null, has_unfinished_entry: false } }
      },
      restorationAnchor: null, restorationAttempt: 0, discardedBodies: 0, openTools: new SvelteSet<string>(),
      isExpanded: () => false, expand: async () => {}, load: async () => {}, textBodyReady: () => {}, failTextRestoration: () => {}, body: async () => ""
    } as unknown as ConversationViewReader;
    const { body } = render(PaneWithQueryClient, {
      props: { ...base, conversationState: "opened", running: true, reader, heldPromptRows: [held("a", "After that, log each 429")] }
    });
    const working = body.indexOf("data-conversation-alive-word");
    expect(body).toContain("Sub-agent 'Check tests' finished");
    expect(body).toContain('data-conversation-row="agent_turn_started"');
    expect(body).toContain('data-conversation-system-mark="spinner"');
    expect(body).not.toContain("data-conversation-live-edge");
    expect(working).toBeGreaterThan(body.indexOf('data-conversation-row="agent_turn_started"'));
    expect(body.indexOf("data-conversation-held-stack")).toBeGreaterThan(working);
    expect(working).toBeLessThan(body.indexOf("data-conversation-box"));
  });

  it("offer no Steer while the turn is blocked on an ask, since nothing is read until it is answered", () => {
    const { body } = render(PaneWithQueryClient, {
      props: { ...base, conversationState: "opened", running: true, supportsSteer: true, ask, heldPromptRows: [held("a", "Hold the upload")] }
    });
    expect(body).not.toContain('data-conversation-held-promote="steer"');
    expect(body).toContain('data-conversation-held-promote="send_now"');
  });

  it("go back to the composer on Edit: out of the queue first, then the words after any draft, still in Queue", () => {
    // Edit is a click and the tests run without a DOM; the wiring is read from the source.
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    const composer = source("../src/components/conversation/ConversationComposer.svelte");
    // The write's own answer decides: a message that the server did not discard keeps its words in the thread.
    expect(pane).toMatch(/async function editHeldPrompt\(heldPromptId: string, words: string\): Promise<void> \{[\s\S]*?if \(await onDiscardHeldPrompt\(heldPromptId, true\) === false\) return;[\s\S]*?origin !== conversationId[\s\S]*?composer\.takeBackQueuedWords\(words\);/);
    expect(pane).toContain("bind:this={composerHandle}");
    expect(composer).toMatch(/export function takeBackQueuedWords\(words: string\): void \{\s*deliveryMode = "queue";\s*const settled = text\.trimEnd\(\);\s*text = settled === "" \? words : `\$\{settled\}\\n\\n\$\{words\}`;/);
  });
});
