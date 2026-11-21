import { describe, expect, it, vi } from "vitest";
import { resolveComposerRunControls } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ComposerRunControlsInput } from "../src/lib/conversation/runControls/contracts";
import { conversationFileMediaType, createPendingConversationFiles } from "../src/lib/conversation/pendingFiles";
import { resolveModelPicker } from "../src/lib/conversation/modelPicker";
import { createVoiceCapture, VOICE_LEVEL_HISTORY, type VoiceRecorderLike } from "../src/lib/conversation/voiceCapture";
import type { BackendModel } from "../src/lib/conversation/wire";

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

  it("leads with the model the control names when the catalogue does not offer it", () => {
    const view = resolveModelPicker({
      backendKey: "claude", drivers: [], model: "claude-opus-5-5", reasoningEffort: "high",
      backends: [], backendLocked: true, models: [model("other-model")]
    });
    expect(view.models.map((choice) => choice.value)).toEqual(["claude-opus-5-5", "other-model"]);
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
