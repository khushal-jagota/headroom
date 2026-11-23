import { describe, expect, it } from "vitest";
import { ASK_REPLY_PLACEHOLDER, COMPOSER_PLACEHOLDER, composerPlaceholderFor, resolveComposerRunControls } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ComposerRunControlsInput } from "../src/lib/conversation/runControls/contracts";
import { DELIVERY_MODE_CHOICES, deliveryModeChoices } from "../src/components/conversation/composer/deliveryModes";

function runInput(overrides: Partial<ComposerRunControlsInput>): ComposerRunControlsInput {
  return {
    selection: { pickedBackend: null, pickedModel: null, pickedReasoningEffort: null },
    backendKey: "claude",
    conversationExists: true,
    running: false,
    current: { model: "opus", reasoningEffort: "high" },
    models: [],
    backends: [],
    drivers: [],
    effortOptions: [],
    startsOnModel: null,
    startsOnReasoningEffort: null,
    inputDisabled: false,
    hasSendableContent: true,
    sendsInFlight: 0,
    ...overrides
  };
}

describe("delivery mode only matters while a turn runs", () => {
  it("offers no choice with nothing running, and a send starts the next turn", () => {
    const view = resolveComposerRunControls(runInput({ running: false, deliveryMode: "queue", supportsSteer: true }));
    expect(view.showDeliveryMode).toBe(false);
    expect(view.deliveryMode).toBe("steer");
    expect(view.submit.ariaLabel).toBe("Send this message");
  });

  it("keeps the person's choice while a turn runs", () => {
    const view = resolveComposerRunControls(runInput({ running: true, deliveryMode: "queue", supportsSteer: true }));
    expect(view.showDeliveryMode).toBe(true);
    expect(view.deliveryModes).toEqual(["steer", "queue", "send_now"]);
    expect(view.deliveryMode).toBe("queue");
    expect(view.submit.ariaLabel).toBe("Queue this message");
  });

  it("never offers or defaults to Steer for a turn that cannot take one", () => {
    const view = resolveComposerRunControls(runInput({ running: true, deliveryMode: "steer", supportsSteer: false }));
    expect(view.deliveryModes).toEqual(["queue", "send_now"]);
    expect(view.deliveryMode).toBe("queue");
  });

  it("does not offer Steer into a turn blocked on the person: the message waits, or ends the turn", () => {
    // The turn reads nothing new until the ask is answered (a Claude turn admits a steer but
    // only reads it after the parked tool call returns), so Steer would sit unread.
    const view = resolveComposerRunControls(runInput({ running: true, deliveryMode: "steer", supportsSteer: true, askWaiting: "holds" }));
    expect(view.showDeliveryMode).toBe(true);
    expect(view.deliveryModes).toEqual(["queue", "send_now"]);
    expect(view.deliveryMode).toBe("queue");
    expect(view.submit.ariaLabel).toBe("Hold this message until the turn ends");
    // The same choice comes back once the ask is answered.
    expect(resolveComposerRunControls(runInput({ running: true, deliveryMode: "steer", supportsSteer: true, askWaiting: null })).deliveryMode).toBe("steer");
  });

  it("sends words written under a question as its answer, with no delivery choice", () => {
    const view = resolveComposerRunControls(runInput({ running: true, deliveryMode: "steer", supportsSteer: true, askWaiting: "answers" }));
    expect(view.showDeliveryMode).toBe(false);
    expect(view.submit.ariaLabel).toBe("Send this as your answer");
  });

  it("names each mode by its label alone, with what each does kept for assistive technology", () => {
    expect(DELIVERY_MODE_CHOICES.map((choice) => choice.name)).toEqual(["Steer", "Queue", "Send now"]);
    for (const choice of DELIVERY_MODE_CHOICES) expect(choice.meaning.length).toBeGreaterThan(0);
    expect(deliveryModeChoices(["queue", "send_now"]).map((choice) => choice.value)).toEqual(["queue", "send_now"]);
  });
});

describe("Stop and Send", () => {
  it("shows Stop only while the turn works: waiting on an ask is not working, so the round is Send", () => {
    expect(resolveComposerRunControls(runInput({ running: true, askWaiting: null })).showStop).toBe(true);
    for (const askWaiting of ["holds", "answers"] as const) {
      const waiting = resolveComposerRunControls(runInput({ running: true, askWaiting, hasSendableContent: false }));
      expect(waiting.showStop).toBe(false);
      // Send stands in its place, unavailable until there is a draft, available once there is.
      expect(waiting.submit.disabled).toBe(true);
      expect(resolveComposerRunControls(runInput({ running: true, askWaiting, hasSendableContent: true })).submit.disabled).toBe(false);
    }
    expect(resolveComposerRunControls(runInput({ running: false })).showStop).toBe(false);
  });
});

describe("the empty composer's placeholder", () => {
  it("is Write a message everywhere, whatever the caller's role-named base", () => {
    expect(COMPOSER_PLACEHOLDER).toBe("Write a message");
    for (const base of ["Message Chief of Staff...", "Message the Coding worker...", "Message Wren…"]) {
      for (const running of [true, false]) {
        expect(composerPlaceholderFor({ askWaiting: null, mode: "steer", running, base })).toBe(COMPOSER_PLACEHOLDER);
      }
    }
  });

  it("says Or write a reply under every ask, including a key request left by a finished turn", () => {
    expect(ASK_REPLY_PLACEHOLDER).toBe("Or write a reply");
    for (const askWaiting of ["answers", "holds"] as const) {
      for (const mode of ["queue", "send_now", "steer"] as const) {
        for (const running of [true, false]) {
          expect(composerPlaceholderFor({ askWaiting, mode, running, base: "Message the Coding worker..." })).toBe(ASK_REPLY_PLACEHOLDER);
        }
      }
    }
  });
});
