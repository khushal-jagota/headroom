import { effortOptionsFor, modelDisplayName } from "./composer";
import { resolvedModelPickerLabel } from "./modelPickerLabel";
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
  backendLocked?: boolean;
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

/** Older cached/test snapshots predate model enablement. Absence preserves the
 * server-side default-on rule; only an explicit false removes a choice. */
export function modelIsEnabled(model: BackendModel): boolean {
  return model.enabled !== false;
}

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
  return null;
}

export function resolveModelPicker(input: ModelPickerInput): ModelPickerView {
  const snapshot = input.backends.find(
    (candidate) => candidate.backend_key === input.backendKey
  );
  const catalogModels = input.models ?? snapshot?.available_models ?? [];
  const models = catalogModels.filter(modelIsEnabled);
  const backendEffortOptions =
    input.backendEffortOptions ?? snapshot?.reasoning_effort_options ?? [];
  const defaultModel = input.defaultModel ?? snapshot?.default_model_id ?? null;
  const modelValue = input.model ?? defaultModel ?? "";
  const modelName = modelDisplayName(catalogModels, modelValue === "" ? null : modelValue) ?? "";
  const modelFace = resolvedModelPickerLabel(catalogModels, modelValue);
  const efforts = effortOptionsFor(
    catalogModels,
    modelValue === "" ? null : modelValue,
    backendEffortOptions
  );
  const defaultReasoningEffort =
    input.defaultReasoningEffort ?? snapshot?.default_reasoning_effort ?? null;
  const reasoningEffort = input.reasoningEffort ?? "";
  const name = backendName(input.backendKey);

  return {
    backendKey: input.backendKey,
    backendLocked: input.backendLocked ?? false,
    backendName: name,
    modelValue,
    modelName,
    reasoningEffort,
    face: [modelFace, reasoningEffort].filter(Boolean).join(" "),
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
      name: resolvedModelPickerLabel(catalogModels, candidate.model_id)
    })),
    efforts: efforts.map((effort) => ({ value: effort, name: effort })),
    defaultModel,
    defaultReasoningEffort,

  };
}
