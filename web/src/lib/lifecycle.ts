// The per-Worker-type lifecycle: a derived, memoized view over one served manifest.
// This is frontend behavior derived from the served worker_types manifest.
// The retired ui.ts lifecycle constants/functions live here as Lifecycle-first-arg
// variants so stage rendering + the scope leash are driven by the served manifest,
// keyed by each Ticket's own worker_type, instead of a hardcoded coding table.
//
// stageLabel/fieldSlot and the FieldStageVisualState type are imported FROM ui.ts;
// ui.ts must NOT import from here (no cycle).
import {
  fieldSlot,
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
  default_ownership_mode: StageOwnershipMode | null;
};

export type ManifestField = {
  id: string;
  label: string;
};

export type WorkerTypeManifest = {
  worker_type: string;
  label: string;
  stages: ManifestStage[];
  dropped: ManifestStage;
  advance: Record<string, string>;
  fields: ManifestField[];
  ceiling_range: string[];
  default_ceiling: string;
  worker_profile_id: string;
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
  stageDefaultOwnershipMode: Record<string, StageOwnershipMode | null>;
};

export function buildLifecycle(m: WorkerTypeManifest): Lifecycle {
  const gatingField: Record<string, string> = {};
  const gatedStage: Record<string, string> = {};
  const stageLabel: Record<string, string> = {};
  const stageDefaultOwnershipMode: Record<string, StageOwnershipMode | null> = {};
  for (const stage of m.stages) {
    stageLabel[stage.id] = stage.label;
    stageDefaultOwnershipMode[stage.id] = stage.default_ownership_mode;
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
    stageDefaultOwnershipMode
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

export function ceilingOptionsFor(
  lc: Lifecycle | null,
  floorStage: string
): Array<{ value: string; label: string }> {
  if (!lc) return [];
  let start = lc.stageOrder.indexOf(floorStage);
  if (start < 0) start = 0;
  // Leash option labels stay the lowercase stageLabel(id) ("needs success"), NOT
  // the manifest's capitalized stage.label — preserving today's mockup wording.
  return lc.stageOrder.slice(start).map((stage) => ({ value: stage, label: stageLabel(stage) }));
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
    if (ticketStatus === "agent_running_step") return "current-running";
    if (ticketStatus === "errored") return "errored";
    if (ticketStatus === "paired_work") return "current-paired-work";
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
  return ticketStageVisualStateFor(lc, {
    ticketStage: detail.stage,
    ticketStatus: detail.ticket_status,
    fieldName,
    fieldHasProposal: Boolean(fieldSlot(detail, fieldName).proposal)
  });
}

export function recapVisibleFor(lc: Lifecycle | null, stage: string): boolean {
  if (!lc) return false;
  return lc.stageOrder.indexOf(stage) > 1;
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
