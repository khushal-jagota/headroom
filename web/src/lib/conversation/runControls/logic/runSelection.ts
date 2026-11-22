/** The complete run-control answer for one composer render and send.
 *
 * Selection is the person's controlled draft state. Everything else is input owned by
 * the conversation or composer. Resolving them here keeps catalog reconciliation,
 * picker presentation, and the exact values carried by a send as one answer, without
 * putting policy or a second selection owner in the renderer.
 */
import { preselectedValue } from "../../composer";
import type {
  BackendModel,
  ConversationBackendKey,
  PromptDeliveryMode
} from "../../wire";
import type {
  ComposerRunControlsInput,
  ComposerRunControlsView,
  ComposerRunSelection,
  ComposerRunSelectionIntent
} from "../contracts";
import { resolveModelPicker } from "../../modelPicker";

type ActiveCatalog = Readonly<{
  models: readonly BackendModel[];
  effortOptions: readonly string[];
  startsOnModel: string | null;
  startsOnReasoningEffort: string | null;
}>;

function activeCatalog(input: ComposerRunControlsInput): ActiveCatalog {
  const switchedBackend =
    !input.conversationExists && input.selection.pickedBackend !== null;
  if (!switchedBackend) {
    return {
      models: input.models,
      effortOptions: input.effortOptions,
      startsOnModel: input.startsOnModel,
      startsOnReasoningEffort: input.startsOnReasoningEffort
    };
  }
  const snapshot = input.backends.find(
    (candidate) => candidate.backend_key === input.selection.pickedBackend
  );
  return {
    models: snapshot?.available_models ?? [],
    effortOptions: snapshot?.reasoning_effort_options ?? [],
    startsOnModel: snapshot?.default_model_id ?? null,
    startsOnReasoningEffort: snapshot?.default_reasoning_effort ?? null
  };
}

function shownBackend(
  input: ComposerRunControlsInput
): ConversationBackendKey | null {
  return input.conversationExists
    ? input.backendKey
    : (input.selection.pickedBackend ?? input.backendKey);
}

/** What a running turn can do with a new message. Steer is only offered to a turn that
 *  can take one. */
export function deliveryModesFor(
  input: Pick<ComposerRunControlsInput, "supportsSteer" | "askWaiting">
): readonly PromptDeliveryMode[] {
  // A turn blocked on the person reads nothing until they decide: a steer would sit unread
  // behind the ask, so it is not offered. The message waits for the turn, or ends it.
  if (input.askWaiting === "holds") return ["queue", "send_now"];
  return input.supportsSteer === false ? ["queue", "send_now"] : ["steer", "queue", "send_now"];
}

/** The mode a send carries now. With nothing running there is no turn to steer, queue
 *  behind or interrupt, so the message simply starts the next one; the server treats a
 *  steer into an idle conversation exactly that way. While a turn runs the person's
 *  choice holds, unless this turn cannot take it, and then the first mode it can. */
export function effectiveDeliveryMode(
  input: Pick<ComposerRunControlsInput, "running" | "deliveryMode" | "supportsSteer" | "askWaiting">
): PromptDeliveryMode {
  if (!input.running) return "steer";
  const offered = deliveryModesFor(input);
  return input.deliveryMode !== undefined && offered.includes(input.deliveryMode)
    ? input.deliveryMode
    : offered[0];
}

function submitProjection(
  input: ComposerRunControlsInput
): ComposerRunControlsView["submit"] {
  const sending = input.sendsInFlight > 0 && !input.running;
  const mode = effectiveDeliveryMode(input);
  const words = sending
    ? "On its way"
    : input.askWaiting === "answers"
      ? "Send this as your answer"
      : !input.running
      ? "Send this message"
      : mode === "steer"
        ? "Steer this message"
        : mode === "send_now" ? "Send this message now" : "Queue this message";
  return {
    active: input.hasSendableContent,
    sending,
    disabled: input.inputDisabled || !input.hasSendableContent,
    title: words,
    ariaLabel: words
  };
}

export function resolveComposerRunControls(
  input: ComposerRunControlsInput
): ComposerRunControlsView {
  const catalog = activeCatalog(input);
  const pickedModel = input.selection.pickedModel;
  const shownModel =
    pickedModel
    ?? preselectedValue(input.current.model, catalog.startsOnModel)
    ?? "";
  const pickedReasoningEffort = input.selection.pickedReasoningEffort;
  const shownEffort = input.selection.effortIsSet ? pickedReasoningEffort ?? "" :
    pickedReasoningEffort
    ?? preselectedValue(
      input.current.reasoningEffort,
      catalog.startsOnReasoningEffort
    )
    ?? "";
  const normalizedSelection: ComposerRunSelection = {
    ...input.selection,
    pickedModel,
    pickedReasoningEffort
  };

  return {
    normalizedSelection,
    carriedRunValues: {
      model: input.conversationExists
        ? pickedModel
        : (pickedModel ?? catalog.startsOnModel),
      reasoningEffort: pickedReasoningEffort,
      backendKey: input.conversationExists
        ? null
        : input.selection.pickedBackend,
      ...(input.selection.effortIsSet === undefined ? {} : {effortIsSet: input.selection.effortIsSet})
    },
    disabled: input.inputDisabled,
    readError: input.readError,
    picker: resolveModelPicker({
      backendKey: shownBackend(input),
      drivers: input.drivers,
      model: shownModel || null,
      reasoningEffort: shownEffort || null,
      backends: input.backends,
      backendLocked: input.conversationExists,
      models: catalog.models,
      backendEffortOptions: catalog.effortOptions,
      defaultModel: catalog.startsOnModel,
      defaultReasoningEffort: catalog.startsOnReasoningEffort
    }),
    pickerSource: {
      backends: input.backends,
      models: catalog.models,
      backendEffortOptions: catalog.effortOptions
    },
    submit: submitProjection(input),
    showStop: input.running,
    deliveryMode: effectiveDeliveryMode(input),
    // A draft that answers the question on screen is not delivered into the turn at all.
    showDeliveryMode: input.running && input.askWaiting !== "answers",
    deliveryModes: deliveryModesFor(input)
  };
}

export function applyComposerRunSelectionIntent(
  input: ComposerRunControlsInput,
  intent: ComposerRunSelectionIntent
): ComposerRunSelection {
  switch (intent.intent) {
    case "choose_backend":
      if (
        input.conversationExists
        || intent.backendKey === shownBackend(input)
      ) {
        return input.selection;
      }
      return {
        ...input.selection,
        pickedBackend: intent.backendKey,
        pickedModel: null,
        pickedReasoningEffort: null
      };
    case "choose_model":
      return {
        ...input.selection,
        pickedModel: intent.model,
        pickedReasoningEffort: intent.reasoningEffort,
        effortIsSet: true,

      };
    case "choose_reasoning_effort":
      return {
        ...input.selection,
        pickedReasoningEffort: intent.reasoningEffort,
        effortIsSet: true
      };
  }
}
