import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import QueuedMessageActions from "../src/components/conversation/composer/QueuedMessageActions.svelte";

const noop = () => undefined;

describe("actions on a queued message", () => {
  it("ties the closed menu and its actions to the single held prompt", () => {
    const { body } = render(QueuedMessageActions, {
      props: { heldPromptId: "hp-1", showSteer: true, running: true, onPromote: noop, onEdit: noop, onDiscard: noop }
    });

    expect(body).toMatch(/data-conversation-held-more[^>]*aria-haspopup="menu"[^>]*aria-expanded="false"/);
    expect(body).toMatch(/role="menu"[^>]*hidden[^>]*data-conversation-held-menu/);
    expect(body.match(/data-held-prompt-id="hp-1"/g)).toHaveLength(2);
    expect(body).toContain('data-conversation-held-edit="hp-1"');
    expect(body).toContain('data-conversation-held-discard="hp-1"');
    expect(body).toMatch(/>Send now<.*>Steer now<.*>Edit<.*>Remove</s);
  });

  it("disables Steer for an idle turn and omits unavailable actions", () => {
    const { body } = render(QueuedMessageActions, {
      props: { heldPromptId: "hp-2", showSteer: true, onPromote: noop }
    });

    expect(body).toMatch(/data-conversation-held-promote="steer"[^>]*disabled/);
    expect(body).not.toContain("data-conversation-held-edit");
    expect(body).not.toContain("data-conversation-held-discard");

    const unsupported = render(QueuedMessageActions, {
      props: { heldPromptId: "hp-2", running: true, onPromote: noop }
    }).body;
    expect(unsupported).not.toContain('data-conversation-held-promote="steer"');
  });

  it("omits the menu when the message offers no actions", () => {
    const { body } = render(QueuedMessageActions, { props: { heldPromptId: "hp-3", showSteer: true } });
    expect(body).not.toContain("data-conversation-held-more");
  });
});
