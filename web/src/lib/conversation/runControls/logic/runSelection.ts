/** The complete run-control answer for one composer render and send.
 *
 * Selection is the person's controlled draft state. Everything else is input owned by
 * the conversation or composer. Resolving them here keeps catalog reconciliation,
 * picker presentation, and the exact values carried by a send as one answer, without
 * putting policy or a second selection owner in the renderer.
 */
import { effortOptionsFor, preselectedValue } from "../../composer";
import type {
  BackendModel,
  ConversationBackendKey
} from "../../wire";
import type {
  ComposerRunControlsInput,
  ComposerRunControlsView,
  ComposerRunSelection,
  ComposerRunSelectionIntent
} from "../contracts";
import { modelIsEnabled, resolveModelPicker } from "../../modelPicker";

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

function normalizedModel(
  pickedModel: string | null,
  models: readonly BackendModel[]
): string | null {
  if (
    pickedModel !== null
    && models.length > 0
    && !models.some((model) => model.model_id === pickedModel && modelIsEnabled(model))
  ) {
    return null;
  }
  return pickedModel;
}

function normalizedEffort(
  pickedEffort: string | null,
  offeredEfforts: readonly string[]
): string | null {
  return pickedEffort !== null && !offeredEfforts.includes(pickedEffort)
    ? null
    : pickedEffort;
}

function submitProjection(
  input: ComposerRunControlsInput
): ComposerRunControlsView["submit"] {
  const sending = input.sendsInFlight > 0 && !input.running;
  return {
    active: input.hasSendableContent,
    sending,
    disabled: input.inputDisabled || !input.hasSendableContent,
    title: sending ? "On its way" : input.running ? "Queue this message" : "Send",
    ariaLabel: sending ? "On its way" : input.running ? "Queue this message" : "Send"
  };
}

export function resolveComposerRunControls(
  input: ComposerRunControlsInput
): ComposerRunControlsView {
  const catalog = activeCatalog(input);
  const pickedModel = normalizedModel(
    input.selection.pickedModel,
    catalog.models
  );
  const shownModel =
    pickedModel
    ?? preselectedValue(input.current.model, catalog.startsOnModel)
    ?? "";
  const offeredEfforts = effortOptionsFor(
    catalog.models,
    shownModel === "" ? null : shownModel,
    catalog.effortOptions
  );
  const pickedReasoningEffort = normalizedEffort(
    input.selection.pickedReasoningEffort,
    offeredEfforts
  );
  const shownEffort =
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
        : input.selection.pickedBackend
    },
    disabled: input.inputDisabled,
    picker: resolveModelPicker({
      backendKey: shownBackend(input),
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
    showStop: input.running
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
        pickedReasoningEffort: intent.reasoningEffort
      };
    case "choose_reasoning_effort":
      return {
        ...input.selection,
        pickedReasoningEffort: intent.reasoningEffort
      };
  }
}
