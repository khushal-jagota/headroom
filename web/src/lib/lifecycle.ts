// The per-Worker-type lifecycle: a derived, memoized view over one served manifest.
// This is frontend behavior derived from the served worker_types manifest.
// The retired ui.ts lifecycle constants/functions live here as Lifecycle-first-arg
// variants so stage rendering + the scope leash are driven by the served manifest,
// keyed by each Ticket's own worker_type, instead of a hardcoded coding table.
//
// stageLabel and the FieldStageVisualState type are imported FROM ui.ts;
// ui.ts must NOT import from here (no cycle).
import {
  labelize,
  stageLabel,
  type FieldStageVisualState,
  type TicketStageVisualInput
} from "./ui";
import type { StageOwnershipMode, TicketDetail } from "./types";

// --- served worker_types manifest shapes ----------------------------------------

export type ManifestStage = {
  id: string;
  label: string;
  gating_field: string | null;
  is_terminal: boolean;
  ownership_mode: StageOwnershipMode | null;
};

export type ManifestField = {
  id: string;
  label: string;
};

export type WorkerTypeManifest = {
  worker_type: string;
  label: string;
  stages: ManifestStage[];
  advance: Record<string, string>;
  fields: ManifestField[];
  ceiling_range: string[];
  default_ceiling: string;
  worker_profile_id: string;
  default_backend: string;
  default_model: string | null;
  default_reasoning_effort: string | null;
};

export type WorkerTypesResponse = {
  worker_types: WorkerTypeManifest[];
};

// --- the derived per-type lifecycle --------------------------------------------

export type Lifecycle = {
  workerType: string;
  workerTypeLabel: string; // == m.label — the worker pill text
  fieldIds: string[]; // == FIELD_NAMES
  stageOrder: string[]; // includes done
  gatingField: Record<string, string>; // Stage id -> gated field id
  gatedStage: Record<string, string>;
  advance: Record<string, string>; // == ADVANCE
  ceilingRange: string[]; // == m.ceiling_range
  fieldLabel: Record<string, string>;
  stageLabel: Record<string, string>;
  stageOwnershipMode: Record<string, StageOwnershipMode | null>;
};

export function buildLifecycle(m: WorkerTypeManifest): Lifecycle {
  const gatingField: Record<string, string> = {};
  const gatedStage: Record<string, string> = {};
  const stageLabel: Record<string, string> = {};
  const stageOwnershipMode: Record<string, StageOwnershipMode | null> = {};
  for (const stage of m.stages) {
    stageLabel[stage.id] = stage.label;
    stageOwnershipMode[stage.id] = stage.ownership_mode;
    if (!stage.is_terminal && stage.gating_field) {
      gatingField[stage.id] = stage.gating_field;
      gatedStage[stage.gating_field] = stage.id;
    }
  }
  const fieldLabel: Record<string, string> = {};
  for (const field of m.fields) {
    fieldLabel[field.id] = field.label;
  }
  return {
    workerType: m.worker_type,
    workerTypeLabel: m.label,
    fieldIds: m.fields.map((f) => f.id),
    stageOrder: m.stages.map((s) => s.id),
    gatingField,
    gatedStage,
    advance: { ...m.advance },
    ceilingRange: [...m.ceiling_range],
    fieldLabel,
    stageLabel,
    stageOwnershipMode
  };
}

// --- the retired ui.ts functions, re-homed as Lifecycle-first-arg variants ------
// Every one takes `lc: Lifecycle | null` and returns its pre-load default when null
// so callers can pass `lc` directly WITHOUT a per-site guard (Codex F1).

export function gatingFieldFor(lc: Lifecycle | null, stage: string): string | null {
  if (!lc) return null;
  return lc.gatingField[stage] || null;
}

export function advanceTargetFor(
  lc: Lifecycle | null,
  stage: string,
  ceiling: string
): string | null {
  if (!lc) return null;
  return lc.advance[stage] || null;
}

// The two names a reader sees. The Worker type's stored label is the name, and the
// id-derived text is only what shows before the manifest arrives. Every screen that
// spells a Stage or a field out goes through one of these, so one vocabulary reaches
// the page, the heading, and the leash together.
export function fieldLabelFor(lc: Lifecycle | null, field: string): string {
  return lc?.fieldLabel[field] || labelize(field);
}

export function stageLabelFor(lc: Lifecycle | null, stage: string): string {
  return lc?.stageLabel[stage] || stageLabel(stage);
}

export function ceilingOptionsFor(
  lc: Lifecycle | null,
  floorStage: string
): Array<{ value: string; label: string }> {
  if (!lc) return [];
  let start = lc.stageOrder.indexOf(floorStage);
  if (start < 0) start = 0;
  // Leash option labels are the Worker type's own Stage labels, so the control reads
  // "Until What Changes" and follows a Stage rename without a code change. This
  // replaces the earlier lowercase stageLabel(id) wording ("needs approach").
  return lc.stageOrder.slice(start).map((stage) => ({
    value: stage,
    label: stageLabelFor(lc, stage)
  }));
}

export function preferredScopeCeilingFor(
  lc: Lifecycle | null,
  newStage: string | null
): string | null {
  if (!lc) return null;
  const options = ceilingOptionsFor(lc, newStage || lc.ceilingRange[0] || "needs_success");
  const advanced = newStage ? lc.advance[newStage] : null;
  if (advanced && options.some((option) => option.value === advanced)) return advanced;
  const terminal = [...options].reverse().find((option) =>
    !Object.prototype.hasOwnProperty.call(lc.advance, option.value)
  );
  return terminal?.value || options.at(-1)?.value || newStage || null;
}

export function fieldIsPassedFor(
  lc: Lifecycle | null,
  field: string,
  stage: string
): boolean {
  if (!lc) return false;
  return lc.stageOrder.indexOf(stage) > lc.stageOrder.indexOf(lc.gatedStage[field]);
}

export function ticketStageVisualStateFor(
  lc: Lifecycle | null,
  {
    ticketStage,
    ticketStatus,
    fieldName,
    fieldHasProposal = false
  }: TicketStageVisualInput
): FieldStageVisualState {
  if (!lc) return "upcoming";
  if (ticketStage === "done") return "completed";

  if (fieldIsPassedFor(lc, fieldName, ticketStage)) return "completed";

  if (gatingFieldFor(lc, ticketStage) === fieldName) {
    if (ticketStatus === "agent") return "current-running";
    if (ticketStatus === "errored") return "errored";
    if (ticketStatus === "assigned") return "current-assigned";
    if (fieldHasProposal || ticketStatus === "awaiting_approval") {
      return "current-awaiting-approval";
    }
    return "current-waiting";
  }

  return "upcoming";
}

export function fieldStageVisualStateFor(
  lc: Lifecycle | null,
  detail: TicketDetail,
  fieldName: string
): FieldStageVisualState {
  const projectedStatus = detail.awaiting_approval
    ? "awaiting_approval"
    : detail.assigned
      ? "assigned"
      : detail.agent_state === "working"
        ? "agent"
        : detail.agent_state === "errored"
          ? "errored"
          : detail.ticket_status === "agent"
            ? "empty"
            : detail.ticket_status;
  return ticketStageVisualStateFor(lc, {
    ticketStage: detail.stage,
    ticketStatus: projectedStatus,
    fieldName,
    fieldHasProposal: detail.pending_proposal?.field === fieldName
  });
}

// --- per-type lifecycle lookup over a fetched manifest response -----------------
// Pure: lives here (not in the Svelte-runes manifest.svelte.ts) so the unit test
// exercises the REAL selector, not a copy (Codex F6). Memoizes the built Lifecycle
// by Worker type, keyed on the response OBJECT so a fresh fetched response rebuilds.
// Returns null while the manifest is still loading (no response yet) OR when the
// type is absent from a loaded manifest — the ROUTE tells those apart via
// manifest.loading / manifest.error + a type-present check (Codex F3).
const lifecycleMemo = new WeakMap<WorkerTypesResponse, Map<string, Lifecycle | null>>();

export function lifecycleFor(
  response: WorkerTypesResponse | undefined,
  workerType: string | undefined | null
): Lifecycle | null {
  if (!response || !workerType) return null;
  let byType = lifecycleMemo.get(response);
  if (!byType) {
    byType = new Map<string, Lifecycle | null>();
    lifecycleMemo.set(response, byType);
  }
  if (byType.has(workerType)) return byType.get(workerType) ?? null;
  const entry = response.worker_types.find((item) => item.worker_type === workerType);
  const lc = entry ? buildLifecycle(entry) : null;
  byType.set(workerType, lc);
  return lc;
}
