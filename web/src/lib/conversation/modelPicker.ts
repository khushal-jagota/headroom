import { effortOptionsFor, modelDisplayName } from "./composer";
import {
  CONVERSATION_BACKEND_KEYS,
  type BackendModel,
  type BackendSnapshot,
  type ConversationBackendKey
} from "./wire";

export const MODEL_PICKER_BACKEND_KEYS: readonly ConversationBackendKey[] = [
  "claude",
  "codex",
  "hermes"
];

export type ModelPickerChoice = Readonly<{
  value: string;
  name: string;
}>;

export type ModelPickerBackend = Readonly<{
  key: ConversationBackendKey;
  name: string;
  selected: boolean;
  unavailableReason: string | null;
}>;

export type ModelPickerView = Readonly<{
  backendKey: ConversationBackendKey | null;
  backendName: string;
  modelValue: string;
  modelName: string;
  reasoningEffort: string;
  face: string;
  backends: readonly ModelPickerBackend[];
  models: readonly ModelPickerChoice[];
  efforts: readonly ModelPickerChoice[];
  defaultModel: string | null;
  defaultReasoningEffort: string | null;
  staleModelReason: string | null;
  reasoningUnavailableReason: string | null;
}>;

export type ModelPickerInput = Readonly<{
  backendKey: ConversationBackendKey | null;
  model: string | null;
  reasoningEffort: string | null;
  backends: readonly BackendSnapshot[];
  backendLocked?: boolean;
  models?: readonly BackendModel[];
  backendEffortOptions?: readonly string[];
  defaultModel?: string | null;
  defaultReasoningEffort?: string | null;
}>;

function backendName(key: ConversationBackendKey | null): string {
  if (key === null) return "";
  return key[0].toUpperCase() + key.slice(1);
}

function snapshotUnavailableReason(snapshot: BackendSnapshot | undefined): string | null {
  if (snapshot === undefined) return "This backend is not available on this machine.";
  if (!snapshot.installed) {
    return snapshot.diagnoses[0] ?? `${backendName(snapshot.backend_key)} is not installed.`;
  }
  if (snapshot.identity?.status === "unauthenticated") {
    return snapshot.identity.detail
      ?? `${backendName(snapshot.backend_key)} is not signed in.`;
  }
  if (
    (snapshot.default_model_id === null || snapshot.default_model_id === undefined)
    && snapshot.available_models.length === 0
  ) {
    return snapshot.diagnoses[0]
      ?? `${backendName(snapshot.backend_key)} offers no models on this machine.`;
  }
  return null;
}

export function resolveModelPicker(input: ModelPickerInput): ModelPickerView {
  const snapshot = input.backends.find(
    (candidate) => candidate.backend_key === input.backendKey
  );
  const models = input.models ?? snapshot?.available_models ?? [];
  const backendEffortOptions =
    input.backendEffortOptions ?? snapshot?.reasoning_effort_options ?? [];
  const defaultModel = input.defaultModel ?? snapshot?.default_model_id ?? null;
  const modelValue = input.model ?? defaultModel ?? "";
  const modelName = modelDisplayName(models, modelValue === "" ? null : modelValue) ?? "";
  const efforts = effortOptionsFor(
    models,
    modelValue === "" ? null : modelValue,
    backendEffortOptions
  );
  const defaultReasoningEffort =
    input.defaultReasoningEffort ?? snapshot?.default_reasoning_effort ?? null;
  const reasoningEffort = input.reasoningEffort
    ?? (defaultReasoningEffort !== null && efforts.includes(defaultReasoningEffort)
      ? defaultReasoningEffort
      : "");
  const staleModel = modelValue !== ""
    && models.length > 0
    && !models.some((candidate) => candidate.model_id === modelValue);
  const name = backendName(input.backendKey);

  return {
    backendKey: input.backendKey,
    backendName: name,
    modelValue,
    modelName,
    reasoningEffort,
    face: [modelName, efforts.length > 0 ? reasoningEffort : ""].filter(Boolean).join(" "),
    backends: MODEL_PICKER_BACKEND_KEYS.map((key) => {
      const candidate = input.backends.find((item) => item.backend_key === key);
      const lockedReason = input.backendLocked && key !== input.backendKey
        ? `This conversation runs on ${name}.`
        : null;
      return {
        key,
        name: backendName(key),
        selected: key === input.backendKey,
        unavailableReason: lockedReason ?? snapshotUnavailableReason(candidate)
      };
    }),
    models: models.map((candidate) => ({
      value: candidate.model_id,
      name: candidate.display_name ?? candidate.model_id
    })),
    efforts: efforts.map((effort) => ({ value: effort, name: effort })),
    defaultModel,
    defaultReasoningEffort,
    staleModelReason: staleModel
      ? `${name || "This backend"} no longer offers ${modelValue}.`
      : null,
    reasoningUnavailableReason: efforts.length === 0
      ? `${modelName || "This model"} takes no reasoning effort.`
      : null
  };
}

export function backendSelectionDefaults(
  backends: readonly BackendSnapshot[],
  key: ConversationBackendKey
): { model: string | null; reasoningEffort: string | null } {
  const snapshot = backends.find((candidate) => candidate.backend_key === key);
  if (snapshot === undefined || snapshotUnavailableReason(snapshot) !== null) {
    return { model: null, reasoningEffort: null };
  }
  const model = snapshot.default_model_id ?? snapshot.available_models[0]?.model_id ?? null;
  const efforts = effortOptionsFor(
    snapshot.available_models,
    model,
    snapshot.reasoning_effort_options
  );
  const preferred = snapshot.default_reasoning_effort ?? null;
  return {
    model,
    reasoningEffort: preferred !== null && efforts.includes(preferred)
      ? preferred
      : (efforts[0] ?? null)
  };
}

export function modelSelectionEffort(
  models: readonly BackendModel[],
  backendEfforts: readonly string[],
  model: string,
  currentEffort: string | null,
  defaultEffort: string | null
): string | null {
  const efforts = effortOptionsFor(models, model, backendEfforts);
  if (currentEffort !== null && efforts.includes(currentEffort)) return currentEffort;
  if (defaultEffort !== null && efforts.includes(defaultEffort)) return defaultEffort;
  return efforts[0] ?? null;
}

// Keep this import checked against the closed wire contract if one list changes later.
void CONVERSATION_BACKEND_KEYS;
