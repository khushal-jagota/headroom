import { describe, expect, it } from "vitest";

import {
  applyComposerRunSelectionIntent,
  resolveComposerRunControls,
  type ComposerRunControlsInput,
  type ComposerRunSelection
} from "../src/components/conversation/composer/runSelection";
import type {
  BackendModel,
  BackendSnapshot,
  ConversationBackendKey
} from "../src/lib/conversation/wire";

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
    ...overrides
  };
}

function selection(
  overrides: Partial<ComposerRunSelection> = {}
): ComposerRunSelection {
  return {
    deliveryMode: "run_when_free",
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
    expect(view.backend).toEqual({ showing: "claude", locked: false });
    expect(view.carriedRunValues).toEqual({
      model: "sonnet",
      reasoningEffort: null,
      backendKey: null
    });
    expect(view.model).toEqual({
      value: "sonnet",
      choices: [
        { value: "opus", name: "Opus", detail: "opus → claude-opus-5" },
        { value: "sonnet", name: "Sonnet", detail: "sonnet" }
      ],
      title: "Sonnet"
    });
    expect(view.effort?.value).toBe("high");
  });

  it("switches only an unlocked backend and clears values belonging to the old catalog", () => {
    const selected = selection({
      deliveryMode: "send_now",
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
      deliveryMode: "send_now",
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

    expect(view.backend).toEqual({ showing: "codex", locked: false });
    expect(view.model.value).toBe("gpt-5.5-codex");
    expect(view.model.choices.map((choice) => choice.value)).toEqual([
      "gpt-5.5-codex",
      "gpt-5.5-codex-mini"
    ]);
    expect(view.effort?.value).toBe("medium");
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

    expect(view.backend).toEqual({ showing: "claude", locked: true });
    expect(view.carriedRunValues).toEqual({
      model: "opus",
      reasoningEffort: "low",
      backendKey: null
    });
  });

  it("clears a disproved model pick without mutating input, but keeps it when the catalog is empty", () => {
    const selected = selection({
      pickedModel: "missing",
      pickedReasoningEffort: "high"
    });
    const supplied = input({ selection: selected });

    const resolved = resolveComposerRunControls(supplied);
    const unresolved = resolveComposerRunControls(
      input({ selection: selected, models: [] })
    );

    expect(resolved.normalizedSelection).toEqual({
      ...selected,
      pickedModel: null
    });
    expect(resolved.model.value).toBe("sonnet");
    expect(unresolved.normalizedSelection.pickedModel).toBe("missing");
    expect(unresolved.model.value).toBe("missing");
    expect(supplied.selection).toEqual(selected);
  });

  it("prepends a current model missing from the catalog so the shown value remains pickable", () => {
    const view = resolveComposerRunControls(
      input({
        conversationExists: true,
        current: { model: "legacy", reasoningEffort: null }
      })
    );

    expect(view.model.value).toBe("legacy");
    expect(view.model.choices[0]).toEqual({
      value: "legacy",
      name: "legacy",
      detail: null
    });
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
      deliveryMode: "run_when_free",
      pickedBackend: null,
      pickedModel: null,
      pickedReasoningEffort: null
    });
    expect(view.model.value).toBe("sonnet");
    expect(view.effort).toEqual({
      value: "high",
      choices: [
        { value: "high", name: "high", detail: null },
        { value: "low", name: "low", detail: null }
      ],
      bare: false
    });
  });

  it("treats a model's explicit empty effort list as authoritative", () => {
    const view = resolveComposerRunControls(
      input({
        models: [
          model("haiku", "Haiku", { reasoning_effort_options: [] }),
          model("opus", "Opus")
        ],
        current: { model: "haiku", reasoningEffort: null },
        startsOnModel: null,
        startsOnReasoningEffort: null
      })
    );

    expect(view.effort).toBeNull();
  });

  it("keeps an absent current effort visible and renders an unvalued offered effort bare", () => {
    const currentEffort = resolveComposerRunControls(
      input({
        current: { model: "opus", reasoningEffort: "legacy" },
        startsOnModel: null,
        startsOnReasoningEffort: null
      })
    );
    const bareEffort = resolveComposerRunControls(
      input({
        current: { model: "opus", reasoningEffort: null },
        startsOnModel: null,
        startsOnReasoningEffort: null
      })
    );

    expect(currentEffort.effort).toEqual({
      value: "legacy",
      choices: [
        { value: "legacy", name: "legacy", detail: null },
        { value: "low", name: "low", detail: null },
        { value: "high", name: "high", detail: null }
      ],
      bare: false
    });
    expect(bareEffort.effort).toEqual({
      value: "",
      choices: [
        { value: "low", name: "low", detail: null },
        { value: "high", name: "high", detail: null }
      ],
      bare: true
    });
  });

  it("preserves model names, second lines, and the shown alias title", () => {
    const view = resolveComposerRunControls(
      input({
        current: { model: "opus", reasoningEffort: null },
        models: [
          model("opus", "Opus 5", { detail: "opus → claude-opus-5" }),
          model("sonnet", "Sonnet 5"),
          model("haiku", "haiku")
        ],
        startsOnModel: null
      })
    );

    expect(view.model).toEqual({
      value: "opus",
      choices: [
        { value: "opus", name: "Opus 5", detail: "opus → claude-opus-5" },
        { value: "sonnet", name: "Sonnet 5", detail: "sonnet" },
        { value: "haiku", name: "haiku", detail: null }
      ],
      title: "Opus 5 — opus → claude-opus-5"
    });
  });

  it("projects actual-backend delivery and effective mode only while running", () => {
    const running = resolveComposerRunControls(
      input({
        backendKey: "hermes",
        running: true,
        selection: selection({ deliveryMode: "steer" })
      })
    );
    const idle = resolveComposerRunControls(
      input({
        backendKey: "hermes",
        running: false,
        selection: selection({ deliveryMode: "steer" })
      })
    );

    expect(running.delivery?.selected).toBe("steer");
    expect(running.delivery?.options.map((option) => option.mode)).toEqual([
      "run_when_free",
      "send_now",
      "steer"
    ]);
    expect(running.effectiveDeliveryMode).toBe("steer");
    expect(idle.delivery).toBeNull();
    expect(idle.effectiveDeliveryMode).toBe("run_when_free");
  });

  it.each([
    [
      "empty",
      {},
      {
        action: "send",
        active: false,
        sending: false,
        disabled: true,
        title: "Send",
        ariaLabel: "Send"
      }
    ],
    [
      "sendable",
      { hasSendableContent: true },
      {
        action: "send",
        active: true,
        sending: false,
        disabled: false,
        title: "Send",
        ariaLabel: "Send"
      }
    ],
    [
      "in flight",
      { sendsInFlight: 1 },
      {
        action: "send",
        active: false,
        sending: true,
        disabled: true,
        title: "On its way",
        ariaLabel: "On its way"
      }
    ],
    [
      "disabled with content",
      { inputDisabled: true, hasSendableContent: true },
      {
        action: "send",
        active: true,
        sending: false,
        disabled: true,
        title: "Send",
        ariaLabel: "Send"
      }
    ],
    [
      "running",
      { running: true },
      {
        action: "stop",
        active: false,
        sending: false,
        disabled: false,
        title: "Stop the turn — press Enter to send instead",
        ariaLabel: "Stop the turn"
      }
    ]
  ] as const)("projects the existing %s submit state", (_label, overrides, expected) => {
    expect(resolveComposerRunControls(input(overrides)).submit).toEqual(expected);
  });

  it("changes only the selected model, effort, or delivery field", () => {
    const selected = selection({
      deliveryMode: "send_now",
      pickedBackend: "codex",
      pickedModel: "one",
      pickedReasoningEffort: "low"
    });
    const supplied = input({ selection: selected });

    expect(applyComposerRunSelectionIntent(
      supplied,
      { intent: "choose_model", model: "two" }
    )).toEqual({ ...selected, pickedModel: "two" });
    expect(applyComposerRunSelectionIntent(
      supplied,
      { intent: "choose_reasoning_effort", reasoningEffort: "high" }
    )).toEqual({ ...selected, pickedReasoningEffort: "high" });
    expect(applyComposerRunSelectionIntent(
      supplied,
      { intent: "choose_delivery_mode", deliveryMode: "steer" }
    )).toEqual({ ...selected, deliveryMode: "steer" });
    expect(supplied.selection).toEqual(selected);
  });
});
