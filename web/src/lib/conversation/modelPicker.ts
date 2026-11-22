import { effortOptionsFor, modelDisplayName, reasoningDescriptor } from "./composer";
import type { DriverPresentation } from "../drivers/contracts";
import { backendLabel, backendMark, type BackendMarkPresentation } from "../drivers/presentation";
import {
  type BackendModel,
  type BackendSnapshot,
  type ConversationBackendKey
} from "./wire";

export type ModelPickerChoice = Readonly<{
  value: string;
  name: string;
}>;

export type ModelPickerBackend = Readonly<{
  key: ConversationBackendKey;
  name: string;
  mark: BackendMarkPresentation;
  selected: boolean;
  unavailableReason: string | null;
}>;

export type ModelPickerView = Readonly<{
  backendKey: ConversationBackendKey | null;
  backendLocked?: boolean;
  backendName: string;
  /** Null when no backend is chosen. */
  backendMark: BackendMarkPresentation | null;
  /** False when the backend's catalogue offers no reasoning effort at all: then there are
   *  no effort rows and no effort word after the model name. */
  offersReasoning: boolean;
  modelValue: string;
  modelName: string;
  reasoningEffort: string;
  /** The closed control's words, as staging: the model, then the effort (also given apart,
   *  so the effort can be drawn as a quieter word). */
  face: string;
  modelFace: string;
  effortFace: string;
  backends: readonly ModelPickerBackend[];
  models: readonly ModelPickerChoice[];
  olderModels: readonly ModelPickerChoice[];
  emptyReason: string | null;
  efforts: readonly ModelPickerChoice[];
  defaultModel: string | null;
  defaultReasoningEffort: string | null;
}>;

export type ModelPickerInput = Readonly<{
  backendKey: ConversationBackendKey | null;
  /** The server's driver list; undefined while it loads. */
  drivers: readonly DriverPresentation[] | undefined;
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

function snapshotUnavailableReason(
  snapshot: BackendSnapshot | undefined,
  name: string
): string | null {
  if (snapshot === undefined) return "This backend is not available on this machine.";
  if (!snapshot.installed) {
    return snapshot.diagnoses[0] ?? `${name} is not installed.`;
  }
  if (snapshot.identity?.status === "unauthenticated") {
    return snapshot.identity.detail
      ?? `${name} is not signed in.`;
  }
  return null;
}

/** The drivers in the server's order, then any card or current key the list lacks. */
function railKeys(input: ModelPickerInput): ConversationBackendKey[] {
  const keys = (input.drivers ?? []).map((driver) => driver.driver_kind);
  for (const key of input.backends.map((snapshot) => snapshot.backend_key)) {
    if (!keys.includes(key)) keys.push(key);
  }
  if (input.backendKey !== null && !keys.includes(input.backendKey)) keys.push(input.backendKey);
  return keys;
}

/** A face names the resolution the server gives a readable name, else the resolved id. */
function modelChoiceName(models: readonly BackendModel[], value: string): string {
  const selected = models.find((model) => model.model_id === value);
  const resolved = selected?.resolved_model_id;
  if (!selected || !resolved) return selected?.display_name ?? value;
  if (resolved === selected.selected_variant) return selected.display_name ?? resolved;
  return selected.resolved_display_name ?? resolved;
}

export function resolveModelPicker(input: ModelPickerInput): ModelPickerView {
  const snapshot = input.backends.find(
    (candidate) => candidate.backend_key === input.backendKey
  );
  const catalogModels = input.models ?? snapshot?.available_models ?? [];
  const models = catalogModels.filter(modelIsEnabled);
  const backendEffortOptions =
    input.backendEffortOptions ?? snapshot?.reasoning_effort_options ?? [];
  // A catalogue with nothing in it (not read yet, not installed, or awaiting a fresh read)
  // says nothing against reasoning, so the stored effort stays. Only models offered with
  // no effort anywhere mean the backend has none.
  const catalogSaysNothing = catalogModels.length === 0 && backendEffortOptions.length === 0;
  const offersReasoning = catalogSaysNothing
    || backendEffortOptions.length > 0
    || catalogModels.some((model) => (reasoningDescriptor(model)?.options.length ?? 0) > 0);
  const defaultModel = input.defaultModel ?? snapshot?.default_model_id ?? null;
  const modelValue = input.model ?? defaultModel ?? "";
  const modelName = modelDisplayName(catalogModels, modelValue === "" ? null : modelValue) ?? "";
  const modelFace = modelChoiceName(catalogModels, modelValue);
  const efforts = offersReasoning ? effortOptionsFor(
    catalogModels,
    modelValue === "" ? null : modelValue,
    backendEffortOptions
  ) : [];
  const defaultReasoningEffort =
    input.defaultReasoningEffort ?? snapshot?.default_reasoning_effort ?? null;
  const reasoningEffort = offersReasoning ? input.reasoningEffort ?? "" : "";
  const name = input.backendKey === null ? "" : backendLabel(input.backendKey, input.drivers);

  const older = new Set(models.filter(candidate => {
    if (!candidate.selected_variant || !candidate.version?.length) return false;
    return models.some(other => other.selected_variant === candidate.selected_variant
      && compareVersions(other.version ?? [], candidate.version ?? []) > 0);
  }));
  const choice = (candidate: BackendModel): ModelPickerChoice => ({
    value: candidate.model_id,
    name: modelChoiceName(catalogModels, candidate.model_id)
  });
  return {
    backendKey: input.backendKey,
    backendLocked: input.backendLocked ?? false,
    backendName: name,
    backendMark: input.backendKey === null ? null : backendMark(input.backendKey, input.drivers),
    offersReasoning,
    modelValue,
    modelName,
    reasoningEffort,
    face: [modelFace, reasoningEffort].filter(Boolean).join(" "),
    modelFace,
    effortFace: reasoningEffort,
    backends: railKeys(input).map((key) => {
      const candidate = input.backends.find((item) => item.backend_key === key);
      const label = backendLabel(key, input.drivers);
      const lockedReason = input.backendLocked && key !== input.backendKey
        ? `This conversation runs on ${name}.`
        : null;
      return {
        key,
        name: label,
        mark: backendMark(key, input.drivers),
        selected: key === input.backendKey,
        unavailableReason: lockedReason ?? snapshotUnavailableReason(candidate, label)
      };
    }),
    models: models.filter(model => !older.has(model)).map(choice),
    olderModels: models.filter(model => older.has(model)).map(choice),
    emptyReason: models.length ? null : snapshot?.diagnoses[0]
      ?? (snapshot ? `${name} offers no models on this machine.` : `${name} model list is unavailable.`),
    efforts,
    defaultModel,
    defaultReasoningEffort,

  };
}

/** A missing trailing component is zero: 6.1 outranks 6, while 6 equals 6.0. */
function compareVersions(left: readonly number[], right: readonly number[]): number {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] ?? 0) - (right[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}
