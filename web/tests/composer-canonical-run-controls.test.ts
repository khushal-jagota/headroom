import { describe, expect, it } from "vitest";
import { applyComposerRunSelectionIntent, resolveComposerRunControls } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ComposerRunControlsInput } from "../src/lib/conversation/runControls/contracts";
import { pickerRows, pickerValidation } from "./fixtures/modelPickerFacts";

const input: ComposerRunControlsInput = {
  selection: {pickedBackend: null, pickedModel: "exact-hidden-id", pickedReasoningEffort: "high", selectionKind: "concrete", effortIsSet: true},
  backendKey: "claude", conversationExists: true, running: false,
  current: {model: "opus", reasoningEffort: "low"},
  models: [{model_id: "different-id", display_name: "Different", enabled: true}],
  backends: [], effortOptions: ["low"], startsOnModel: "other-default", startsOnReasoningEffort: "low",
  inputDisabled: false, hasSendableContent: true, sendsInFlight: 0,
  canonicalRows: [], validation: null
};

// Legacy normalization cannot prove retained canonical intent when a scoped read fails,
// nor preserve two kinds sharing one token. Existing catalog tests exercise legacy input.
describe("canonical composer intent", () => {
  it("retains exact hidden model and unsupported explicit effort across unavailable reads", () => {
    const view = resolveComposerRunControls(input);
    expect(view.normalizedSelection).toEqual(input.selection);
    expect(view.carriedRunValues).toEqual({model: "exact-hidden-id", reasoningEffort: "high", backendKey: null, selectionKind: "concrete", effortIsSet: true});
    expect(view.canonicalRows).toEqual([]);
    expect(view.pickerSource.models).toEqual([]);
  });

  it("carries a deliberate same-token kind choice and null effort without resolving its alias locally", () => {
    const row = pickerRows[0];
    const selection = applyComposerRunSelectionIntent({...input, selection: {...input.selection, pickedModel: row.submitted_token, pickedReasoningEffort: null}}, {
      intent: "choose_model", model: row.submitted_token, reasoningEffort: null, row
    });
    const view = resolveComposerRunControls({...input, selection, canonicalRows: pickerRows, validation: pickerValidation});
    expect(view.carriedRunValues).toEqual({model: row.submitted_token, reasoningEffort: null, backendKey: null, selectionKind: row.selection_kind, effortIsSet: true});
    expect(view.carriedRunValues.model).not.toBe(row.resolved_model_id);
  });
});
