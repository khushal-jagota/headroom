import { describe, expect, it, vi } from "vitest";
import { applyComposerRunSelectionIntent, resolveComposerRunControls } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ComposerRunControlsInput } from "../src/lib/conversation/runControls/contracts";
import { sendBodyFor } from "../src/lib/conversation/composer";
import { mintOutgoingMessage, rememberOutgoingMessages, recallOutgoingMessages, resetOutgoingImageReservationsForTest } from "../src/lib/conversation/outgoing";

const input: ComposerRunControlsInput = {
  selection: {pickedBackend: null, pickedModel: "exact-hidden-id", pickedReasoningEffort: "high", selectionKind: "concrete", effortIsSet: true},
  backendKey: "claude", conversationExists: true, running: false,
  current: {model: "opus", reasoningEffort: "low"},
  models: [{model_id: "different-id", display_name: "Different", enabled: true}],
  backends: [], effortOptions: ["low"], startsOnModel: "other-default", startsOnReasoningEffort: "low",
  inputDisabled: false, hasSendableContent: true, sendsInFlight: 0,
};

// An unavailable advisory read must not erase a deliberate draft choice.
describe("canonical composer intent", () => {
  it("retains exact hidden model and unsupported explicit effort across unavailable reads", () => {
    const view = resolveComposerRunControls({...input, readError: "The scoped request failed."});
    expect(view.readError).toBe("The scoped request failed.");
    expect(view.normalizedSelection).toEqual(input.selection);
    expect(view.carriedRunValues).toEqual({model: "exact-hidden-id", reasoningEffort: "high", backendKey: null, selectionKind: "concrete", effortIsSet: true});
    expect(view.pickerSource.models).toEqual(input.models);
    expect(view.picker).toMatchObject({backendKey: "claude", modelValue: "exact-hidden-id", reasoningEffort: "high"});
  });
});

// The outgoing store is the actual durable browser boundary used after transport
// uncertainty. Pure draft tests cannot prove its decoder retains explicit null/kind.
it("reopens an uncertain outgoing send with the same exact run intent and old absent fields", async () => {
  const stored = new Map<string, string>();
  vi.stubGlobal("window", {sessionStorage: {
    get length() {return stored.size;}, key: (index: number) => [...stored.keys()][index] ?? null,
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value), removeItem: (key: string) => stored.delete(key)
  }});
  resetOutgoingImageReservationsForTest();
  try {
    const message = mintOutgoingMessage({content:[{piece:"text",text:"exact words"}], senderLabel:"owner", mode:"queue", pickedModel:"opus", pickedReasoningEffort:null, effortIsSet:true, selectionKind:"provider_alias", pickedBackend:"claude"});
    const old = mintOutgoingMessage({content:[{piece:"text",text:"old words"}], senderLabel:"owner", mode:"queue"});
    await rememberOutgoingMessages("canonical-retry", [message, old]);
    const [reopened, historical] = await recallOutgoingMessages("canonical-retry");
    expect(reopened).toMatchObject({pickedModel:"opus",pickedReasoningEffort:null,effortIsSet:true,selectionKind:"provider_alias",pickedBackend:"claude",knownFate:"sent_before_this_page"});
    const body = sendBodyFor({message:reopened, current:{model:"opus",reasoningEffort:"high"}, picked:{model:reopened.pickedModel ?? null, reasoningEffort:reopened.pickedReasoningEffort ?? null, effortIsSet:reopened.effortIsSet,selectionKind:reopened.selectionKind, backendKey:reopened.pickedBackend}, conversationId:null});
    expect(body).toMatchObject({model:"opus",selection_kind:"provider_alias",reasoning_effort:null,backend_key:"claude",sender_message_id:message.messageId});
    const oldBody = sendBodyFor({message:historical,current:{model:"opus",reasoningEffort:"high"},picked:{model:null,reasoningEffort:null},conversationId:"c"});
    expect(oldBody).not.toHaveProperty("selection_kind");
    expect(oldBody).not.toHaveProperty("reasoning_effort");
  } finally {resetOutgoingImageReservationsForTest();vi.unstubAllGlobals();}
});

it("preserves custom token, effort, and explicit null in a direct conversation without canonical rows", () => {
  for (const effort of ["future-effort", null]) {
    const direct = {...input};
    const selection = applyComposerRunSelectionIntent(direct, {intent: "choose_model", model: "provider-future", reasoningEffort: effort});
    const view = resolveComposerRunControls({...direct, selection});
    expect(view.carriedRunValues).toMatchObject({model: "provider-future", reasoningEffort: effort, selectionKind: "legacy_unclassified", effortIsSet: true});
    expect(view.picker.reasoningEffort).toBe(effort ?? "");
  }
});
