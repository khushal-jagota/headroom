import { describe, expect, it } from "vitest";
import { resolveModelPicker } from "../src/lib/conversation/modelPicker";
import type { BackendModel } from "../src/lib/conversation/wire";

function picker(models: BackendModel[], model = "gpt-6.1-sol") {
  return resolveModelPicker({
    backendKey: "codex", model, reasoningEffort: "medium", backends: [],
    models: models.map((entry) => ({
      ...entry,
      // These label fixtures declare known support. Unknown support is tested below.
      reasoning_effort_options: "reasoning_effort_options" in entry
        ? entry.reasoning_effort_options : ["medium"],
    })),
    backendEffortOptions: ["medium"]
  });
}

describe("current model variants in the shared picker", () => {
  it("shows the readable resolved model on the face and in one list label", () => {
    const view = picker([{
      model_id: "gpt-6.1-sol", display_name: "GPT 6.1 Sol", enabled: true,
      selected_variant: "sol", resolved_model_id: "gpt-6.1-sol"
    }]);
    expect(view.face).toBe("GPT 6.1 Sol medium");
    expect(view.modelValue).toBe("gpt-6.1-sol");
    expect(view.models).toEqual([{ value: "gpt-6.1-sol", name: "GPT 6.1 Sol" }]);
  });





  it("keeps a Claude alias as the selection while it shows the resolved version", () => {
    const view = picker([{
      model_id: "opus", display_name: "Opus", enabled: true,
      selected_variant: "opus", resolved_model_id: "claude-opus-5-5"
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
    { resolved: null, expected: "Opus medium" },
    { resolved: "opus", expected: "Opus medium" },
    { resolved: "unrecognized-model-vNext", expected: "unrecognized-model-vNext medium" },
    { resolved: "claude-opus-5-5[1m]", expected: "Opus 5.5 (1M) medium" },
    { resolved: "claude-haiku-4-5-20251001", expected: "Haiku 4.5 medium", efforts: [] },
    { resolved: "gpt-6.1-sol", expected: "GPT 6.1 Sol medium", model: "gpt-6.1-sol", alias: "sol" }
  ])("uses resolved evidence without invented versions or unsupported effort: $expected", ({
    resolved, expected, efforts, model = "opus", alias = "opus"
  }) => {
    const view = picker([{
      model_id: model, display_name: alias === "opus" ? "Opus" : alias, enabled: true,
      selected_variant: alias, resolved_model_id: resolved,
      ...(efforts ? { reasoning_effort_options: efforts } : {})
    }], model);
    expect(view.face).toBe(expected);
    expect(view.modelValue).toBe(model);
    expect(view.models).toEqual([{ value: model, name: expected.replace(/ medium$/, "") }]);
  });

  it("prefers the catalogue name for the concrete resolution over an alias label", () => {
    const view = picker([
      {
        model_id: "opus", display_name: "Opus", enabled: true,
        selected_variant: "opus", resolved_model_id: "provider:custom"
      },
      { model_id: "provider:custom", display_name: "Custom model", enabled: true }
    ], "opus");
    expect(view.face).toBe("Custom model medium");
    expect(view.modelValue).toBe("opus");
    expect(view.models[0]).toEqual({ value: "opus", name: "Custom model" });
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
