import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ComposerWithQueryClient from "./fixtures/ComposerWithQueryClient.svelte";
import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import TaskProgress from "../src/components/conversation/TaskProgress.svelte";
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
  it("always ends in a send round, quiet and unavailable while there is nothing to send", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/<button[^>]*class="chat-send"[^>]*data-conversation-send/);
    expect(body).toMatch(/data-conversation-send[^>]*disabled|disabled[^>]*data-conversation-send/);
    expect(body).not.toContain("data-conversation-stop");
  });

  it("offers Stop turn while a turn runs, and keeps the draft and controls in their two rows", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true } });
    expect(body).toMatch(/data-conversation-stop[^>]*aria-label="Stop turn"|aria-label="Stop turn"[^>]*data-conversation-stop/);
    expect(body).not.toContain("data-conversation-send=");
    // The draft comes before the controls' row, which never moves beside it.
    expect(body.indexOf("data-conversation-input")).toBeLessThan(body.indexOf('class="chat-foot'));
  });

  it("says only Drop to attach while something is dragged over it", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/data-conversation-drop-label[\s\S]*Drop to attach/);
  });
});

describe("the queue is light", () => {
  it("draws the waiting messages with their actions, and no header or count", () => {
    const { body } = render(HeldPromptStack, {
      props: { rows: [held("a", "Log each 429"), held("b", "Match the dark style")], supportsSteer: true, running: true }
    });
    expect(body).not.toContain("chat-queue-head");
    expect(body).not.toMatch(/>Queued</);
    expect(body).toContain('aria-label="2 queued messages"');
    expect(body.match(/data-conversation-held-row=/g)?.length).toBe(2);
    expect(body.indexOf("Log each 429")).toBeLessThan(body.indexOf("Match the dark style"));
    expect(body.match(/data-conversation-held-promote="steer"/g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-promote="send_now"/g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-discard="hp-/g)?.length).toBe(2);
    expect(body).toContain('aria-label="Remove this message from the queue"');
  });

  it("stands over the composer's box, outside it", () => {
    const { body } = render(ComposerWithQueryClient, {
      props: { ...base, running: true, supportsSteer: true, heldPromptRows: [held("a", "After that, log each 429")] }
    });
    expect(body.indexOf("data-conversation-held-stack")).toBeLessThan(body.indexOf("data-conversation-box"));
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

describe("the task pill", () => {
  it("draws one segment per step: the done ones, the one in hand, the rest", () => {
    const { body } = render(TaskProgress, {
      props: {
        variant: "strip",
        progress: {
          entries: [
            { text: "Reproduce", status: "completed" },
            { text: "Read", status: "completed" },
            { text: "Confirm", status: "in_progress" },
            { text: "Write", status: "pending" },
            { text: "Propose", status: "pending" }
          ],
          currentPosition: 3,
          currentEntry: null,
          completedCount: 2,
          hasUnfinishedEntry: true,
          turnRunning: true
        }
      }
    });
    expect(body.match(/class="task-segment[ "]/g)?.length).toBe(5);
    expect(body.match(/task-segment[^"]*done/g)?.length).toBe(2);
    expect(body.match(/task-segment[^"]*now/g)?.length).toBe(1);
  });
});
