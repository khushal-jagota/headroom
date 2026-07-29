/** The complete run-control answer for one composer render and send.
 *
 * Selection is the person's controlled draft state. Everything else is input owned by
 * the conversation or composer. Resolving them here keeps catalog reconciliation,
 * picker presentation, and the exact values carried by a send as one answer, without
 * putting policy or a second selection owner in the renderer.
 */
import {
  deliveryOptionsFor,
  effortOptionsFor,
  modelDetail,
  modelDisplayName,
  preselectedValue,
  type DeliveryOption,
  type RunValues
} from "../../../lib/conversation/composer";
import type {
  BackendModel,
  BackendSnapshot,
  ConversationBackendKey,
  PromptDeliveryMode
} from "../../../lib/conversation/wire";

export type ComposerRunSelection = Readonly<{
  deliveryMode: PromptDeliveryMode;
  pickedBackend: ConversationBackendKey | null;
  pickedModel: string | null;
  pickedReasoningEffort: string | null;
}>;

export type ComposerRunControlsInput = Readonly<{
  selection: ComposerRunSelection;
  backendKey: ConversationBackendKey | null;
  conversationExists: boolean;
  running: boolean;
  current: RunValues;
  models: readonly BackendModel[];
  backends: readonly BackendSnapshot[];
  effortOptions: readonly string[];
  startsOnModel: string | null;
  startsOnReasoningEffort: string | null;
  inputDisabled: boolean;
  hasSendableContent: boolean;
  sendsInFlight: number;
}>;

export type ComposerRunSelectionIntent =
  | { intent: "choose_backend"; backendKey: ConversationBackendKey }
  | { intent: "choose_model"; model: string }
  | { intent: "choose_reasoning_effort"; reasoningEffort: string }
  | { intent: "choose_delivery_mode"; deliveryMode: PromptDeliveryMode };

export type ComposerRunControlChoice = Readonly<{
  value: string;
  name: string;
  detail: string | null;
}>;

export type ComposerRunControlsView = Readonly<{
  normalizedSelection: ComposerRunSelection;
  carriedRunValues: RunValues;
  effectiveDeliveryMode: PromptDeliveryMode;
  disabled: boolean;
  backend: Readonly<{
    showing: ConversationBackendKey | null;
    locked: boolean;
  }>;
  model: Readonly<{
    value: string;
    choices: readonly ComposerRunControlChoice[];
    title: string;
  }>;
  effort: Readonly<{
    value: string;
    choices: readonly ComposerRunControlChoice[];
    bare: boolean;
  }> | null;
  delivery: Readonly<{
    selected: PromptDeliveryMode;
    options: readonly DeliveryOption[];
  }> | null;
  submit: Readonly<{
    action: "send" | "stop";
    active: boolean;
    sending: boolean;
    disabled: boolean;
    title: string;
    ariaLabel: string;
  }>;
}>;

export type ComposerRunControlIntents = Readonly<{
  chooseBackend: (backendKey: ConversationBackendKey) => void;
  chooseModel: (model: string) => void;
  chooseReasoningEffort: (reasoningEffort: string) => void;
  chooseDeliveryMode: (deliveryMode: PromptDeliveryMode) => void;
  send: () => void;
  stop: () => void;
}>;

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

function modelSecondLine(model: BackendModel): string | null {
  const detail = model.detail ?? null;
  if (detail !== null && detail !== "") return detail;
  return model.display_name === null || model.display_name === model.model_id
    ? null
    : model.model_id;
}

function normalizedModel(
  pickedModel: string | null,
  models: readonly BackendModel[]
): string | null {
  if (
    pickedModel !== null
    && models.length > 0
    && !models.some((model) => model.model_id === pickedModel)
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

function choicesForModels(
  models: readonly BackendModel[],
  shownModel: string
): ComposerRunControlChoice[] {
  const options =
    shownModel !== "" && !models.some((model) => model.model_id === shownModel)
      ? [{ model_id: shownModel, display_name: null }, ...models]
      : models;
  return options.map((model) => ({
    value: model.model_id,
    name: model.display_name ?? model.model_id,
    detail: modelSecondLine(model)
  }));
}

function choicesForEfforts(
  offeredEfforts: readonly string[],
  shownEffort: string
): ComposerRunControlChoice[] {
  const choices =
    shownEffort !== "" && !offeredEfforts.includes(shownEffort)
      ? [shownEffort, ...offeredEfforts]
      : offeredEfforts;
  return choices.map((effort) => ({
    value: effort,
    name: effort,
    detail: null
  }));
}

function modelTitle(models: readonly BackendModel[], shownModel: string): string {
  const value = shownModel === "" ? null : shownModel;
  const name = modelDisplayName(models, value) ?? "the backend's own model";
  const detail = modelDetail(models, value);
  return detail === null ? name : `${name} — ${detail}`;
}

function submitProjection(
  input: ComposerRunControlsInput
): ComposerRunControlsView["submit"] {
  const sending = input.sendsInFlight > 0 && !input.running;
  if (input.running) {
    return {
      action: "stop",
      active: false,
      sending,
      disabled: false,
      title: "Stop the turn — press Enter to send instead",
      ariaLabel: "Stop the turn"
    };
  }
  return {
    action: "send",
    active: input.hasSendableContent,
    sending,
    disabled: input.inputDisabled || !input.hasSendableContent,
    title: sending ? "On its way" : "Send",
    ariaLabel: sending ? "On its way" : "Send"
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
  const effortChoices = choicesForEfforts(offeredEfforts, shownEffort);
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
    effectiveDeliveryMode: input.running
      ? input.selection.deliveryMode
      : "run_when_free",
    disabled: input.inputDisabled,
    backend: {
      showing: shownBackend(input),
      locked: input.conversationExists
    },
    model: {
      value: shownModel,
      choices: choicesForModels(catalog.models, shownModel),
      title: modelTitle(catalog.models, shownModel)
    },
    effort: effortChoices.length === 0
      ? null
      : {
          value: shownEffort,
          choices: effortChoices,
          bare: shownEffort === ""
        },
    delivery: input.running
      ? {
          selected: input.selection.deliveryMode,
          options: deliveryOptionsFor(input.backendKey)
        }
      : null,
    submit: submitProjection(input)
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
      return { ...input.selection, pickedModel: intent.model };
    case "choose_reasoning_effort":
      return {
        ...input.selection,
        pickedReasoningEffort: intent.reasoningEffort
      };
    case "choose_delivery_mode":
      return { ...input.selection, deliveryMode: intent.deliveryMode };
  }
}
