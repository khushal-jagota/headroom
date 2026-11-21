import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { paneHeadStatus, paneTitleFor, uncertainActivityFeedback } from "../src/lib/conversation/dockPresentation";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";

const quiet = { readOnly: false, activityUncertain: false, waitingForYou: false, running: false, backgroundWorking: false, settingUp: null, elapsed: null };

describe("the opened pane's top bar", () => {
  it("names who the conversation is with, from the page or else the role", () => {
    expect(paneTitleFor("Vylo pricing and launch", "Manager")).toBe("Vylo pricing and launch");
    expect(paneTitleFor("  ", "Chief")).toBe("Chief of Staff");
    expect(paneTitleFor(null, "Worker")).toBe("Worker");
  });

  it("says what the conversation is doing, the person's turn first", () => {
    expect(paneHeadStatus(quiet)).toBeNull();
    expect(paneHeadStatus({ ...quiet, running: true, elapsed: "2m 14s" })).toEqual({ text: "Working · 2m 14s", tone: "live" });
    expect(paneHeadStatus({ ...quiet, running: true })).toEqual({ text: "Working", tone: "live" });
    expect(paneHeadStatus({ ...quiet, running: true, waitingForYou: true })).toEqual({ text: "Waiting for you", tone: "attention" });
    expect(paneHeadStatus({ ...quiet, backgroundWorking: true })?.tone).toBe("live");
    expect(paneHeadStatus({ ...quiet, settingUp: "Setting up · preparing workspace · 1m 35s" }))
      .toEqual({ text: "Setting up · preparing workspace · 1m 35s", tone: "live" });
    expect(paneHeadStatus({ ...quiet, running: true, activityUncertain: true })?.tone).toBe("quiet");
    expect(paneHeadStatus({ ...quiet, running: true, readOnly: true })?.text).toBe("Past conversation · read only");
  });
});

describe("setting up while the machine cannot be read", () => {
  const workerSetup = { step: "preparing_workspace", began_at: 1, machine_id: "machine" } as const;
  it("reads as one state: the setup, still, with one line saying why it is unconfirmed", () => {
    expect(uncertainActivityFeedback(true)).toBe("This machine is not connected, so setup progress cannot be confirmed.");
    expect(uncertainActivityFeedback(false)).toBe("Current activity on this machine is unknown.");
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, workerSetup, activityKnown: false,
      feedback: uncertainActivityFeedback(true) } }).body;
    expect(html).toContain("Setting up · preparing workspace");
    expect(html).toContain("data-unconfirmed");
    expect(html.match(/data-conversation-dock-feedback/g)).toHaveLength(1);
  });
  it("keeps the moving mark while setup is known", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, workerSetup } }).body;
    expect(html).toContain('aria-label="setting up"');
    expect(html).not.toContain("data-unconfirmed");
  });
});
