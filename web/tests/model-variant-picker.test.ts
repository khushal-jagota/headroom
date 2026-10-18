import { describe, expect, it } from "vitest";
import { resolveModelPicker } from "../src/lib/conversation/modelPicker";
import type { BackendModel } from "../src/lib/conversation/wire";

function picker(models: BackendModel[], model = "gpt-6.1-sol") {
  return resolveModelPicker({
    backendKey: "codex", model, reasoningEffort: "medium", backends: [], models,
    backendEffortOptions: ["medium"]
  });
}

describe("current model variants in the shared picker", () => {
  it("shows the server variant beside its current model without deriving a family", () => {
    const view = picker([{
      model_id: "gpt-6.1-sol", display_name: "GPT 6.1 Sol", enabled: true,
      selected_variant: "sol", resolved_model_id: "gpt-6.1-sol"
    }]);
    expect(view.face).toBe("sol · gpt-6.1-sol medium");
    expect(view.models).toEqual([{ value: "gpt-6.1-sol", name: "sol", detail: "gpt-6.1-sol" }]);
    expect(view.catalogueWarning).toBeNull();
  });

  it("keeps the last known-good model selectable and reports the catalogue failure", () => {
    const warning = "Model catalogue unavailable. Using the last known-good model for sol.";
    const view = picker([{
      model_id: "gpt-6.1-sol", display_name: "GPT 6.1 Sol", enabled: true,
      selected_variant: "sol", resolved_model_id: "gpt-6.1-sol", catalogue_warning: warning
    }]);
    expect(view.models[0].value).toBe("gpt-6.1-sol");
    expect(view.catalogueWarning).toBe(warning);
    expect(view.staleModelReason).toBeNull();
  });

  it("keeps a Claude alias as the selection while it shows the resolved version", () => {
    const view = picker([{
      model_id: "opus", display_name: "Opus", enabled: true,
      selected_variant: "opus", resolved_model_id: "claude-opus-5-5"
    }], "opus");
    expect(view.modelValue).toBe("opus");
    expect(view.face).toBe("opus · claude-opus-5-5 medium");
  });

  it("preserves catalogue labels when the backend supplies no variant metadata", () => {
    const view = picker([{
      model_id: "provider:custom", display_name: "Custom model", enabled: true
    }], "provider:custom");
    expect(view.face).toBe("Custom model medium");
    expect(view.models[0].name).toBe("Custom model");
  });
});
