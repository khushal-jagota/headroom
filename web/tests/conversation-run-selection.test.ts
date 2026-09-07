import { describe, expect, it } from "vitest";

import {
  applyComposerRunSelectionIntent,
  resolveComposerRunControls
} from "../src/lib/conversation/runControls/logic/runSelection";
import type {
  ComposerRunControlsInput,
  ComposerRunSelection
} from "../src/lib/conversation/runControls/contracts";
import type {
  BackendModel,
  BackendSnapshot,
  ConversationBackendKey
} from "../src/lib/conversation/wire";
import {
  backendSelectionDefaults,
  resolveModelPicker
} from "../src/lib/conversation/modelPicker";

function snapshot(
  backendKey: ConversationBackendKey,
  overrides: Partial<BackendSnapshot> = {}
): BackendSnapshot {
  return {
    backend_key: backendKey,
    installed: true,
    executable_path: `/usr/local/bin/${backendKey}`,
    version: "1.0.0",
    identity: null,
    available_models: [],
    reasoning_effort_options: [],
    default_model_id: null,
    default_reasoning_effort: null,
    cached_usage: null,
    update_advisory: null,
    diagnoses: [],
    ...overrides
  };
}

function model(
  modelId: string,
  displayName: string | null = null,
  overrides: Partial<BackendModel> = {}
): BackendModel {
  return {
    model_id: modelId,
    display_name: displayName,
    enabled: true,
    ...overrides
  };
}

function selection(
  overrides: Partial<ComposerRunSelection> = {}
): ComposerRunSelection {
  return {
    pickedBackend: null,
    pickedModel: null,
    pickedReasoningEffort: null,
    ...overrides
  };
}

function input(
  overrides: Partial<ComposerRunControlsInput> = {}
): ComposerRunControlsInput {
  return {
    selection: selection(),
    backendKey: "claude",
    conversationExists: false,
    running: false,
    current: { model: null, reasoningEffort: null },
    models: [
      model("opus", "Opus", { detail: "opus → claude-opus-5" }),
      model("sonnet", "Sonnet")
    ],
    backends: [],
    effortOptions: ["low", "high"],
    startsOnModel: "sonnet",
    startsOnReasoningEffort: "high",
    inputDisabled: false,
    hasSendableContent: false,
    sendsInFlight: 0,
    ...overrides
  };
}

describe("composer run selection", () => {
  it("shows the untouched pre-conversation backend and concrete start model without inventing picks", () => {
    const supplied = input();

    const view = resolveComposerRunControls(supplied);

    expect(view.normalizedSelection).toEqual(selection());
    expect(view.picker.backendKey).toBe("claude");
    expect(view.carriedRunValues).toEqual({
      model: "sonnet",
      reasoningEffort: null,
      backendKey: null
    });
    expect(view.picker.modelValue).toBe("sonnet");
    expect(view.picker.models).toEqual([
      { value: "opus", name: "Opus" },
      { value: "sonnet", name: "Sonnet" }
    ]);
    expect(view.picker.reasoningEffort).toBe("high");
  });

  it("switches only an unlocked backend and clears values belonging to the old catalog", () => {
    const selected = selection({
      pickedModel: "opus",
      pickedReasoningEffort: "high"
    });

    const changed = applyComposerRunSelectionIntent(
      input({ selection: selected }),
      { intent: "choose_backend", backendKey: "codex" }
    );
    const alreadyShowing = applyComposerRunSelectionIntent(
      input({ selection: selected }),
      { intent: "choose_backend", backendKey: "claude" }
    );
    const locked = applyComposerRunSelectionIntent(
      input({ selection: selected, conversationExists: true }),
      { intent: "choose_backend", backendKey: "codex" }
    );

    expect(changed).toEqual({
      pickedBackend: "codex",
      pickedModel: null,
      pickedReasoningEffort: null
    });
    expect(alreadyShowing).toEqual(selected);
    expect(locked).toEqual(selected);
  });

  it("uses a picked backend's reported catalog and defaults and carries that explicit backend", () => {
    const codex = snapshot("codex", {
      available_models: [
        model("gpt-5.5-codex", "GPT-5.5 Codex"),
        model("gpt-5.5-codex-mini", "GPT-5.5 Codex mini")
      ],
      reasoning_effort_options: ["medium", "high"],
      default_model_id: "gpt-5.5-codex",
      default_reasoning_effort: "medium"
    });

    const view = resolveComposerRunControls(
      input({
        selection: selection({ pickedBackend: "codex" }),
        backends: [codex]
      })
    );

    expect(view.picker.backendKey).toBe("codex");
    expect(view.picker.modelValue).toBe("gpt-5.5-codex");
    expect(view.picker.models.map((choice) => choice.value)).toEqual([
      "gpt-5.5-codex",
      "gpt-5.5-codex-mini"
    ]);
    expect(view.picker.reasoningEffort).toBe("medium");
    expect(view.carriedRunValues).toEqual({
      model: "gpt-5.5-codex",
      reasoningEffort: null,
      backendKey: "codex"
    });
  });

  it("locks an existing conversation to its actual backend and carries only explicit run-value picks", () => {
    const view = resolveComposerRunControls(
      input({
        selection: selection({
          pickedBackend: "codex",
          pickedModel: "opus",
          pickedReasoningEffort: "low"
        }),
        backendKey: "claude",
        conversationExists: true,
        current: { model: "sonnet", reasoningEffort: "high" }
      })
    );

    expect(view.picker.backendKey).toBe("claude");
    expect(view.picker.backends.find((backend) => backend.key === "codex")?.unavailableReason)
      .toBe("This conversation runs on Claude.");
    expect(view.carriedRunValues).toEqual({
      model: "opus",
      reasoningEffort: "low",
      backendKey: null
    });
  });

  it("keeps a disabled historical model on the face but removes it from every choice", () => {
    const view = resolveComposerRunControls(
      input({
        conversationExists: true,
        current: { model: "opus", reasoningEffort: "high" },
        models: [
          model("opus", "Opus 5", { enabled: false }),
          model("sonnet", "Sonnet 5")
        ]
      })
    );

    expect(view.picker.modelValue).toBe("opus");
    expect(view.picker.face).toBe("Opus 5 high");
    expect(view.picker.models).toEqual([{ value: "sonnet", name: "Sonnet 5" }]);
    expect(view.picker.staleModelReason).toBe("Claude no longer offers opus.");
  });

  it("uses the normalized shown model for effort options in the same resolved view", () => {
    const view = resolveComposerRunControls(
      input({
        selection: selection({
          pickedModel: "missing",
          pickedReasoningEffort: "high"
        }),
        models: [
          model("sonnet", "Sonnet", {
            reasoning_effort_options: ["low"]
          })
        ],
        startsOnModel: "sonnet"
      })
    );

    expect(view.normalizedSelection).toEqual({
      pickedBackend: null,
      pickedModel: null,
      pickedReasoningEffort: null
    });
    expect(view.picker.modelValue).toBe("sonnet");
    expect(view.picker.reasoningEffort).toBe("high");
    expect(view.picker.efforts).toEqual([{ value: "low", name: "low" }]);
  });

  it("keeps Send present beside a separate Stop control while running", () => {
    const running = resolveComposerRunControls(
      input({
        backendKey: "hermes",
        running: true
      })
    );
    const idle = resolveComposerRunControls(
      input({
        backendKey: "hermes",
        running: false
      })
    );

    expect(running.showStop).toBe(true);
    expect(running.submit.title).toBe("Queue this message");
    expect(idle.showStop).toBe(false);
    expect(idle.submit.title).toBe("Send");
  });

  it("changes only the selected model or effort field", () => {
    const selected = selection({
      pickedBackend: "codex",
      pickedModel: "one",
      pickedReasoningEffort: "low"
    });
    const supplied = input({ selection: selected });

    expect(applyComposerRunSelectionIntent(
      supplied,
      { intent: "choose_model", model: "two", reasoningEffort: "high" }
    )).toEqual({ ...selected, pickedModel: "two", pickedReasoningEffort: "high" });
    expect(applyComposerRunSelectionIntent(
      supplied,
      { intent: "choose_reasoning_effort", reasoningEffort: "high" }
    )).toEqual({ ...selected, pickedReasoningEffort: "high" });
    expect(supplied.selection).toEqual(selected);
  });

  it("dims an installed but unauthenticated backend and keeps it focusable with a reason", () => {
    const codex = snapshot("codex", {
      identity: {
        status: "unauthenticated",
        account_label: null,
        detail: null,
        login_command: "codex login"
      }
    });
    const view = resolveModelPicker({
      backendKey: "claude",
      model: "opus",
      reasoningEffort: "high",
      backends: [snapshot("claude"), codex],
      models: [model("opus", "Opus")],
      backendEffortOptions: ["high"]
    });

    expect(view.backends.find((backend) => backend.key === "codex")?.unavailableReason)
      .toBe("Codex is not signed in.");
  });

  it("selects a backend's valid defaults and clears an invalid default effort", () => {
    const codex = snapshot("codex", {
      available_models: [
        model("small", "Small", { reasoning_effort_options: [] }),
        model("large", "Large", { reasoning_effort_options: ["low", "high"] })
      ],
      reasoning_effort_options: ["low", "high"],
      default_model_id: "small",
      default_reasoning_effort: "high"
    });

    expect(backendSelectionDefaults([codex], "codex")).toEqual({
      model: "small",
      reasoningEffort: null
    });
  });
});
