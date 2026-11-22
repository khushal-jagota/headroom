import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ComposerWithQueryClient from "./fixtures/ComposerWithQueryClient.svelte";
import PaneWithQueryClient from "./fixtures/PaneWithQueryClient.svelte";
import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import { DELIVERY_MODE_CHOICES, deliveryModeChoices } from "../src/components/conversation/composer/deliveryModes";
import { attachmentDisplayName, attachmentKindLabel } from "../src/components/conversation/composer/attachments";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

const base = { draftKey: "composer-direction-v3", onSend: async () => true };

function held(key: string, text: string): HeldPromptRow {
  return {
    key,
    heldPromptId: `hp-${key}`,
    senderMessageId: null,
    content: [{ piece: "text", text }],
    senderLabel: "owner",
    sentAtUnixMilliseconds: 1,
    state: "held",
    queueReason: null
  };
}

describe("the composer's controls row", () => {
  it("v11 (3): under a question there is no confirm pill; the well keeps its round send, and the one round Stop ends the row", () => {
    const userInput = { requestId: "q-1", questions: [
      { question_id: "a", header: "Style", question: "Which style?", multi_select: false, allow_other: true, options: [{ label: "Soft", description: "" }] },
      { question_id: "b", header: "Tone", question: "Which tone?", multi_select: false, allow_other: false, options: [{ label: "Warm", description: "" }] }
    ] };
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, userInput, onStop: () => {} } });
    expect(body).not.toContain("data-conversation-ask-primary");
    expect(body).toMatch(/<button[^>]*class="chat-send[^"]*chat-ask-send[^"]*"[^>]*data-conversation-send[^>]*disabled/);
    expect(body.match(/data-conversation-stop/g)?.length).toBe(1);
    expect(body).toMatch(/class="chat-ask-stop[^"]*"[^>]*data-conversation-stop/);
    expect(body).not.toContain(">Stop turn<");
    // The send is drawn inside the well: its round is a span in the button.
    expect(body).toMatch(/class="chat-send chat-ask-send[^"]*"[^>]*>(<!--[^>]*-->)*<span class="chat-ask-send-round"/);
  });

  it("keeps the round Stop while a turn works with nothing waiting on the person", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, onStop: () => {} } });
    expect(body).toContain("chat-send stop");
    expect(body).not.toContain("data-conversation-ask-stop");
    expect(body.match(/data-conversation-stop/g)?.length).toBe(1);
  });

  it("always ends in a send round, quiet and unavailable while there is nothing to send", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/<button[^>]*class="chat-send"[^>]*data-conversation-send/);
    expect(body).toMatch(/data-conversation-send[^>]*disabled|disabled[^>]*data-conversation-send/);
    expect(body).not.toContain("data-conversation-stop");
  });

  it("offers Stop turn while a turn runs, on the composer's one line", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true } });
    expect(body).toMatch(/data-conversation-stop[^>]*aria-label="Stop turn"|aria-label="Stop turn"[^>]*data-conversation-stop/);
    expect(body).not.toContain("data-conversation-send=");
    expect(body).toMatch(/class="chat-box[^"]*is-compact/);
    expect(body).toContain('data-conversation-box-shape="line"');
  });

  it("is one line at rest, with add, the model, voice and send in that one row", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/class="chat-box[^"]*is-compact/);
    expect(body).toContain("data-conversation-add");
    expect(body).toContain("data-conversation-model-picker");
  });

  it("v11 (3): while a permission ask waits, the one round Stop ends the row, and the well keeps its round send", () => {
    const ask = { askId: "a-1", title: "Upload build 41", detail: "xcrun altool --upload-app", options: [{ option_id: "allow", label: "Allow once", option_kind: "allow_once" as const }, { option_id: "deny", label: "Deny", option_kind: "reject_once" as const }] };
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, ask, onStop: () => {}, onCancelTurn: () => {} } });
    // Exactly one way to stop: the composer's round Stop, not the header and no text pill.
    expect(body.match(/data-conversation-stop/g)?.length).toBe(1);
    expect(body).toMatch(/class="chat-ask-stop[^"]*"[^>]*data-conversation-stop[^>]*data-conversation-ask-stop[^>]*aria-label="Stop the agent"[^>]*><span class="chat-ask-stop-round[^"]*"[^>]*><span class="chat-send-fill/);
    expect(body).not.toContain("cv-ask-stop");
    expect(body).not.toContain(">Stop turn<");
    // No confirm pill and no mode chip: the well's round send, unavailable while empty.
    expect(body).not.toContain("data-conversation-ask-primary");
    expect(body).not.toContain("data-conversation-send-mode");
    expect(body).toMatch(/<button[^>]*class="chat-send[^"]*chat-ask-send[^"]*"[^>]*data-conversation-send[^>]*disabled/);
    expect(body).toContain("chat-ask-pen");
    expect(body).not.toContain('data-conversation-ask-action="cancel_turn"');
    expect(body).toContain('placeholder="Or write a message"');
  });

  it("says only Drop to attach while something is dragged over it", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/data-conversation-drop-label[\s\S]*Drop to attach/);
  });
});

describe("the queue is light, and stands in the thread", () => {
  it("draws each waiting message as the person's own message not sent yet, with its actions and no header", () => {
    const { body } = render(HeldPromptStack, {
      props: { rows: [held("a", "Log each 429"), held("b", "Match the dark style")], supportsSteer: true, running: true, conversationId: "c-1" }
    });
    expect(body).not.toContain("chat-queue-head");
    expect(body).toContain('aria-label="2 queued messages"');
    expect(body.match(/data-conversation-held-row=/g)?.length).toBe(2);
    // Each is a ghost bubble of the person's words, with one "Queued" chip under it that
    // opens its actions as a dropdown (v4), drawn closed.
    expect(body.match(/data-conversation-bubble/g)?.length).toBe(2);
    expect(body.match(/class="chat-qrow-word[^"]*">Queued<\/span>/g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-chip/g)?.length).toBe(2);
    expect(body.match(/aria-haspopup="menu"/g)?.length).toBe(2);
    expect(body.match(/class="chat-qrow-menu[^"]*"[^>]*hidden/g)?.length).toBe(2);
    expect(body).not.toContain("sends when this turn ends");
    expect(body.indexOf("Log each 429")).toBeLessThan(body.indexOf("Match the dark style"));
    expect(body.match(/data-conversation-held-promote="steer"[^>]*>Steer now</g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-promote="send_now"/g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-discard="hp-/g)?.length).toBe(2);
    expect(body).toContain('aria-label="Remove this message from the queue"');
  });

  it("is never drawn over the composer", () => {
    const { body } = render(ComposerWithQueryClient, {
      props: { ...base, running: true, supportsSteer: true, heldPromptRows: [held("a", "After that, log each 429")] }
    });
    expect(body).not.toContain("data-conversation-held-stack");
  });

  it("is drawn at the end of the thread, after everything said, and the thread reads from its end", () => {
    const { body } = render(PaneWithQueryClient, {
      props: {
        conversationId: "c-1", draftKey: "pane-queue", onSend: async () => true, conversationState: "opened",
        running: true, supportsSteer: true, heldPromptRows: [held("a", "After that, log each 429")]
      }
    });
    const content = body.indexOf("data-conversation-content");
    const stack = body.indexOf("data-conversation-held-stack");
    expect(content).toBeGreaterThan(-1);
    expect(stack).toBeGreaterThan(content);
    expect(stack).toBeLessThan(body.indexOf("data-conversation-box"));
    // One anchoring rule for every state: a short thread reads from the top.
    expect(body).not.toContain("data-anchor-end");
  });

  it("offers no Steer while the turn is blocked on an ask, since nothing is read until it is answered", () => {
    const ask = { askId: "a-1", title: "Upload build 41", detail: null, options: [{ option_id: "allow", label: "Allow once", option_kind: "allow_once" as const }] };
    const { body } = render(PaneWithQueryClient, {
      props: {
        conversationId: "c-1", draftKey: "pane-queue-ask", onSend: async () => true, conversationState: "opened",
        running: true, supportsSteer: true, ask, heldPromptRows: [held("a", "Hold the upload")]
      }
    });
    expect(body).not.toContain('data-conversation-held-promote="steer"');
    expect(body).toContain('data-conversation-held-promote="send_now"');
  });

  it("offers no Steer for a turn that cannot take one", () => {
    const { body } = render(HeldPromptStack, { props: { rows: [held("a", "x")], supportsSteer: false, running: true } });
    expect(body).not.toContain('data-conversation-held-promote="steer"');
    expect(body).toContain('data-conversation-held-promote="send_now"');
  });
});

describe("delivery modes", () => {
  it("are their labels alone, with what each does kept for assistive technology", () => {
    expect(DELIVERY_MODE_CHOICES.map((choice) => choice.name)).toEqual(["Steer", "Queue", "Send now"]);
    for (const choice of DELIVERY_MODE_CHOICES) expect(choice.meaning.length).toBeGreaterThan(0);
    expect(deliveryModeChoices(["queue", "send_now"]).map((choice) => choice.value)).toEqual(["queue", "send_now"]);
  });
});

describe("pending attachment tiles", () => {
  it("cut a long name in its middle so its start and kind survive", () => {
    expect(attachmentDisplayName("ci-run-4812.txt")).toBe("ci-run-4812.txt");
    const cut = attachmentDisplayName("export-weekly-retention-cohorts-2026-11-v1.csv");
    expect(Array.from(cut).length).toBeLessThanOrEqual(26);
    expect(cut).toContain("…");
    expect(cut.startsWith("export-")).toBe(true);
    expect(cut.endsWith("v1.csv")).toBe(true);
  });

  it("name common kinds as people do", () => {
    expect(attachmentKindLabel("export.csv", "text/csv")).toBe("CSV");
    expect(attachmentKindLabel("chart.png", "image/png")).toBe("PNG");
    expect(attachmentKindLabel("notes.txt", "text/plain")).toBe("Text");
  });
});

