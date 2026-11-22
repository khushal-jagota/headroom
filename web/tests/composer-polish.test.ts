import { describe, expect, it, vi } from "vitest";
import { composerPlaceholderFor, resolveComposerRunControls } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ComposerRunControlsInput } from "../src/lib/conversation/runControls/contracts";
import { conversationFileMediaType, createPendingConversationFiles } from "../src/lib/conversation/pendingFiles";
import { resolveModelPicker } from "../src/lib/conversation/modelPicker";
import { createVoiceCapture, VOICE_LEVEL_HISTORY, type VoiceRecorderLike } from "../src/lib/conversation/voiceCapture";
import type { BackendModel } from "../src/lib/conversation/wire";
import { attachmentByteLabel, attachmentKindLabel } from "../src/components/conversation/composer/attachments";

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

  it("says in the empty composer what a message written under an ask will do", () => {
    const base = "Message the Coding worker...";
    // v8 (8): one placeholder everywhere, whatever the caller's role-named base.
    expect(composerPlaceholderFor({ askWaiting: null, mode: "steer", running: true, base })).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: null, mode: "steer", running: true, base: "Message Wren…" })).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: "answers", mode: "queue", running: true, base })).toBe("Or write your own answer");
    expect(composerPlaceholderFor({ askWaiting: "holds", mode: "queue", running: true, base })).toBe("Or write a message");
    expect(composerPlaceholderFor({ askWaiting: "holds", mode: "send_now", running: true, base })).toBe("Or write a message. Sending it stops this turn");
    // A key request left by a finished turn holds nothing: the next message starts a turn.
    expect(composerPlaceholderFor({ askWaiting: "holds", mode: "steer", running: false, base })).toBe("Write a message");
  });

  it("sends words written under a question as its answer, with no delivery choice", () => {
    const view = resolveComposerRunControls(runInput({ running: true, deliveryMode: "steer", supportsSteer: true, askWaiting: "answers" }));
    expect(view.showDeliveryMode).toBe(false);
    expect(view.submit.ariaLabel).toBe("Send this as your answer");
  });
});

describe("plain-text log files", () => {
  it("are taken as text like a .txt", async () => {
    expect(conversationFileMediaType("ci-run.log")).toBe("text/plain");
    const file = new File(["FAIL push 4.5s\n"], "ci-run.log", { type: "" });
    const intake = await createPendingConversationFiles([file], 1);
    expect(intake.rejected).toEqual([]);
    expect(intake.accepted[0]).toMatchObject({ fileName: "ci-run.log", mediaType: "text/plain" });
  });

  it("still refuses a binary file named .log", async () => {
    const file = new File([new Uint8Array([0, 159, 146, 150])], "core.log");
    expect((await createPendingConversationFiles([file], 1)).rejected).toHaveLength(1);
  });
});

describe("model picker list", () => {
  const model = (model_id: string): BackendModel => ({ model_id, display_name: null } as BackendModel);

  it("v6.1: lists only the catalogue's models, as on the base branch (no extra current-model row)", () => {
    const view = resolveModelPicker({
      backendKey: "claude", drivers: [], model: "claude-opus-5-5", reasoningEffort: "high",
      backends: [], backendLocked: true, models: [model("other-model")]
    });
    expect(view.models.map((choice) => choice.value)).toEqual(["other-model"]);
  });

  it("does not repeat a model the catalogue offers", () => {
    const view = resolveModelPicker({
      backendKey: "claude", drivers: [], model: "opus", reasoningEffort: null,
      backends: [], models: [model("opus"), model("sonnet")]
    });
    expect(view.models.map((choice) => choice.value)).toEqual(["opus", "sonnet"]);
  });
});

describe("voice level bars", () => {
  it("records recent loudness while a meter listens, and closes it when the take ends", async () => {
    let tick: () => void = () => {};
    const close = vi.fn();
    const loudness = [0.2, 0.8];
    const recorder: VoiceRecorderLike = { start: vi.fn(), stop: vi.fn(), ondataavailable: null, onstop: null };
    const capture = createVoiceCapture({ onState: vi.fn(), onTranscript: vi.fn() }, {
      getStream: async () => ({ getTracks: () => [] }),
      makeRecorder: () => recorder,
      now: () => 0,
      startTicker: (next) => { tick = next; return () => {}; },
      acquireWakeLock: () => () => {},
      openMeter: () => ({ read: () => loudness.shift() ?? 0, close })
    });
    await capture.startRecording();
    expect(capture.state()).toEqual({ phase: "recording", elapsedMs: 0, levels: [] });
    tick();
    tick();
    expect(capture.state()).toMatchObject({ levels: [0.2, 0.8] });
    for (let index = 0; index < VOICE_LEVEL_HISTORY + 5; index += 1) tick();
    const state = capture.state();
    expect(state.phase === "recording" ? state.levels?.length : -1).toBe(VOICE_LEVEL_HISTORY);
    capture.cancel();
    expect(close).toHaveBeenCalledTimes(1);
  });
});

describe("a waiting file reads as the sent file card reads", () => {
  it("writes its size in the card's units", () => {
    expect(attachmentByteLabel(692)).toBe("692 B");
    expect(attachmentByteLabel(1741)).toBe("1.7 KiB");
    expect(attachmentByteLabel(24 * 1024)).toBe("24 KiB");
    expect(attachmentByteLabel(3.1 * 1024 * 1024)).toBe("3.1 MiB");
  });

  it("names its kind in words, never its raw media type", () => {
    expect(attachmentKindLabel("ci-run-4812.txt", "text/plain")).toBe("Text");
    expect(attachmentKindLabel("data.json", "application/json")).toBe("JSON");
    expect(attachmentKindLabel("spec.pdf", "application/pdf")).toBe("PDF");
    expect(attachmentKindLabel("archive.bin", "application/octet-stream")).toBe("File");
  });
});
