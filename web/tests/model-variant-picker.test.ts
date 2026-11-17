import { describe, expect, it } from "vitest";
import { resolveModelPicker } from "../src/lib/conversation/modelPicker";
import type { BackendModel, BackendSnapshot } from "../src/lib/conversation/wire";
import type { ProviderOptionDescriptor } from "../src/lib/usage/contracts/model";
import { DRIVERS } from "./support/drivers";

/** The descriptor the server builds from a model's own effort list. */
function reasoning(efforts: string[]): ProviderOptionDescriptor[] {
  return efforts.length ? [{
    type: "select", id: "reasoning_effort", label: "Reasoning", role: "reasoning",
    options: efforts.map((effort) => ({ id: effort, label: effort }))
  }] : [];
}

function picker(models: BackendModel[], model = "gpt-6.1-sol") {
  return resolveModelPicker({
    backendKey: "codex", model, reasoningEffort: "medium", backends: [], drivers: DRIVERS,
    // Deliberate fixture shape: models carry the server's option_descriptors. These label
    // fixtures declare known support; unknown support is tested below.
    models: models.map((entry) => ({
      ...entry,
      option_descriptors: entry.option_descriptors ?? reasoning(["medium"])
    })),
    backendEffortOptions: ["medium"]
  });
}

function card(overrides: Partial<BackendSnapshot>): BackendSnapshot {
  return {
    backend_key: "codex", installed: true, executable_path: null, version: null, identity: null,
    available_models: [], reasoning_effort_options: [], cached_usage: null, update_advisory: null,
    diagnoses: [], ...overrides
  };
}

describe("current model variants in the shared picker", () => {
  it("shows the catalogue name on the face and in one list label", () => {
    const view = picker([{
      model_id: "gpt-6.1-sol", display_name: "GPT 6.1 Sol", enabled: true,
      selected_variant: "sol", resolved_model_id: "gpt-6.1-sol", resolved_display_name: "GPT 6.1 Sol"
    }]);
    expect(view.face).toBe("GPT 6.1 Sol medium");
    expect(view.modelValue).toBe("gpt-6.1-sol");
    expect(view.models).toEqual([{ value: "gpt-6.1-sol", name: "GPT 6.1 Sol" }]);
  });

  it("keeps an alias as the selection while it shows the server's name for the resolution", () => {
    const view = picker([{
      model_id: "opus", display_name: "Opus", enabled: true,
      selected_variant: "opus", resolved_model_id: "claude-opus-5-5", resolved_display_name: "Opus 5.5"
    }], "opus");
    expect(view.modelValue).toBe("opus");
    expect(view.face).toBe("Opus 5.5 medium");
    expect(view.models).toEqual([{ value: "opus", name: "Opus 5.5" }]);
  });

  it("preserves catalogue labels when the backend supplies no variant metadata", () => {
    const view = picker([{
      model_id: "provider:custom", display_name: "Custom model", enabled: true
    }], "provider:custom");
    expect(view.face).toBe("Custom model medium");
    expect(view.models[0].name).toBe("Custom model");
  });

  it.each([
    { resolved: null, readable: null, expected: "Opus medium" },
    { resolved: "opus", readable: null, expected: "Opus medium" },
    { resolved: "unrecognized-model-vNext", readable: null, expected: "unrecognized-model-vNext medium" },
    { resolved: "claude-haiku-4-5-20251001", readable: "Haiku 4.5", expected: "Haiku 4.5 medium", efforts: [] }
  ])("names the resolution from the server, else the raw id: $expected", ({
    resolved, readable, expected, efforts
  }) => {
    const view = picker([{
      model_id: "opus", display_name: "Opus", enabled: true,
      selected_variant: "opus", resolved_model_id: resolved, resolved_display_name: readable,
      ...(efforts ? { option_descriptors: reasoning(efforts) } : {})
    }], "opus");
    expect(view.face).toBe(expected);
    expect(view.modelValue).toBe("opus");
    expect(view.models).toEqual([{ value: "opus", name: expected.replace(/ medium$/, "") }]);
  });
});

describe("drivers, marks and reasoning in the picker", () => {
  it("lists the backends in the drivers' order, with their labels and marks", () => {
    const view = picker([]);
    expect(view.backends.map((backend) => [backend.key, backend.name])).toEqual([
      ["codex", "Codex"], ["claude", "Claude"]
    ]);
    expect(view.backends.map((backend) => backend.mark.kind)).toEqual(["icon", "icon"]);
    expect(view.backendName).toBe("Codex");
    expect(view.backendMark).toEqual(view.backends[0].mark);
  });

  it("appends a saved Hermes key with its capitalised name, initials and a reason", () => {
    const view = resolveModelPicker({
      backendKey: "hermes", model: "anthropic:sonnet", reasoningEffort: null, backends: [], drivers: DRIVERS
    });
    const appended = view.backends.at(-1);
    expect(view.backends.map((backend) => backend.key)).toEqual(["codex", "claude", "hermes"]);
    expect(appended).toMatchObject({
      key: "hermes", name: "Hermes", selected: true,
      mark: { kind: "initials", text: "H" },
      unavailableReason: "This backend is not available on this machine."
    });
    expect(view.emptyReason).toBe("Hermes model list is unavailable.");
  });

  it("offers no reasoning for a card without efforts, even with a stored effort", () => {
    const plain = card({
      backend_key: "codex", reasoning_effort_options: [],
      available_models: [{ model_id: "plain-model", display_name: "Plain model", enabled: true,
        reasoning_effort_options: null, option_descriptors: [] }],
      default_model_id: "plain-model"
    });
    const view = resolveModelPicker({
      backendKey: "codex", model: "plain-model", reasoningEffort: "high", backends: [plain], drivers: DRIVERS
    });
    expect(view.offersReasoning).toBe(false);
    expect(view.efforts).toEqual([]);
    expect(view.effortFace).toBe("");
    expect(view.face).toBe("Plain model");
  });

  it("keeps a stored effort word while no catalogue has been read", () => {
    const view = resolveModelPicker({
      backendKey: "codex", model: "gpt-6.1-sol", reasoningEffort: "high", backends: [], drivers: DRIVERS
    });
    expect(view.offersReasoning).toBe(true);
    expect(view.face).toBe("gpt-6.1-sol high");
  });

  it("keeps a stored effort word on a known but empty card, as a not-installed one is", () => {
    // The server sends an empty card when a backend is not installed or awaits a fresh read.
    const view = resolveModelPicker({
      backendKey: "codex", model: "gpt-6.1-sol", reasoningEffort: "high", drivers: DRIVERS,
      backends: [card({ installed: false })]
    });
    expect(view.offersReasoning).toBe(true);
    expect(view.face).toBe("gpt-6.1-sol high");
  });

  it("takes the effort rows from the model's reasoning descriptor", () => {
    const view = picker([{
      model_id: "gpt-6.1-sol", display_name: "GPT 6.1 Sol", enabled: true,
      option_descriptors: reasoning(["low", "high"])
    }]);
    expect(view.offersReasoning).toBe(true);
    expect(view.efforts).toEqual([{ value: "low", name: "low" }, { value: "high", name: "high" }]);
  });
});

it("offers the highest enabled version of each variant and keeps older exact choices reachable", () => {
  const view = picker([
    {model_id:"gpt-6-sol", display_name:"GPT-6 Sol", enabled:true, selected_variant:"sol", version:[6]},
    {model_id:"gpt-6.1-sol", display_name:"GPT-6.1 Sol", enabled:true, selected_variant:"sol", version:[6,1]},
    {model_id:"gpt-6-astra", display_name:"GPT-6 Astra", enabled:true, selected_variant:"astra", version:[6]},
    {model_id:"gpt-7-sol", display_name:"GPT-7 Sol", enabled:false, selected_variant:"sol", version:[7]},
    {model_id:"unversioned", display_name:"Next model", enabled:true, selected_variant:"sol", version:[]}
  ], "gpt-6-sol");
  expect(view.models.map(row => row.value)).toEqual(["gpt-6.1-sol", "gpt-6-astra", "unversioned"]);
  expect(view.olderModels).toEqual([{value:"gpt-6-sol", name:"GPT-6 Sol"}]);
  expect(view.modelValue).toBe("gpt-6-sol");
});
