// The per-type lifecycle: a derived, memoized view over one ticket type's served
// manifest. This is the framework-free mirror of the backend's ticket_types views.
// The retired ui.ts lifecycle constants/functions live here as Lifecycle-first-arg
// variants so stage rendering + the scope leash are driven by the served manifest,
// keyed by each ticket's own ticket_type, instead of a hardcoded coding table.
//
// stateLabel/fieldSlot and the FieldStageVisualState type are imported FROM ui.ts;
// ui.ts must NOT import from here (no cycle).
import {
  fieldSlot,
  stateLabel,
  type FieldStageVisualState,
  type TicketStageVisualInput
} from "./ui";
import type { TicketDetail } from "./types";

// --- served manifest shapes (mirror ticket_types/contracts ManifestDict) --------

export type ManifestStage = {
  id: string;
  label: string;
  gating_field: string | null;
  is_terminal: boolean;
};

export type ManifestField = {
  id: string;
  label: string;
};

export type TicketTypeManifest = {
  type_id: string;
  label: string;
  stages: ManifestStage[];
  dropped: ManifestStage;
  advance: Record<string, string>;
  fields: ManifestField[];
  ceiling_range: string[];
  default_ceiling: string;
  worker_profile_id: string;
};

export type TicketTypesResponse = {
  types: TicketTypeManifest[];
};

// --- the derived per-type lifecycle --------------------------------------------

export type Lifecycle = {
  typeId: string;
  typeLabel: string; // == m.label — the worker pill text
  fieldIds: string[]; // == FIELD_NAMES
  stateOrder: string[]; // == STATE_ORDER (includes done)
  gatingField: Record<string, string>; // == GATING_FIELD
  gatedState: Record<string, string>; // == GATED_STATE
  advance: Record<string, string>; // == ADVANCE
  ceilingRange: string[]; // == m.ceiling_range
  fieldLabel: Record<string, string>;
  stageLabel: Record<string, string>;
};

export function buildLifecycle(m: TicketTypeManifest): Lifecycle {
  const gatingField: Record<string, string> = {};
  const gatedState: Record<string, string> = {};
  const stageLabel: Record<string, string> = {};
  for (const stage of m.stages) {
    stageLabel[stage.id] = stage.label;
    if (!stage.is_terminal && stage.gating_field) {
      gatingField[stage.id] = stage.gating_field;
      gatedState[stage.gating_field] = stage.id;
    }
  }
  const fieldLabel: Record<string, string> = {};
  for (const field of m.fields) {
    fieldLabel[field.id] = field.label;
  }
  return {
    typeId: m.type_id,
    typeLabel: m.label,
    fieldIds: m.fields.map((f) => f.id),
    stateOrder: m.stages.map((s) => s.id),
    gatingField,
    gatedState,
    advance: { ...m.advance },
    ceilingRange: [...m.ceiling_range],
    fieldLabel,
    stageLabel
  };
}

// --- the retired ui.ts functions, re-homed as Lifecycle-first-arg variants ------
// Every one takes `lc: Lifecycle | null` and returns its pre-load default when null
// so callers can pass `lc` directly WITHOUT a per-site guard (Codex F1).

export function gatingFieldFor(lc: Lifecycle | null, state: string): string | null {
  if (!lc) return null;
  return lc.gatingField[state] || null;
}

export function advanceTargetFor(
  lc: Lifecycle | null,
  state: string,
  ceiling: string
): string | null {
  if (!lc) return null;
  return lc.advance[state] || null;
}

export function ceilingOptionsFor(
  lc: Lifecycle | null,
  floorState: string
): Array<{ value: string; label: string }> {
  if (!lc) return [];
  let start = lc.stateOrder.indexOf(floorState);
  if (start < 0) start = 0;
  // Leash option labels stay the lowercase stateLabel(id) ("needs success"), NOT
  // the manifest's capitalized stage.label — preserving today's mockup wording.
  return lc.stateOrder.slice(start).map((state) => ({ value: state, label: stateLabel(state) }));
}

export function fieldIsPassedFor(
  lc: Lifecycle | null,
  field: string,
  state: string
): boolean {
  if (!lc) return false;
  return lc.stateOrder.indexOf(state) > lc.stateOrder.indexOf(lc.gatedState[field]);
}

export function ticketStageVisualStateFor(
  lc: Lifecycle | null,
  {
    ticketState,
    ticketStatus,
    fieldName,
    fieldHasProposal = false
  }: TicketStageVisualInput
): FieldStageVisualState {
  if (!lc) return "upcoming";
  if (ticketState === "done") return "completed";

  if (fieldIsPassedFor(lc, fieldName, ticketState)) return "completed";

  if (gatingFieldFor(lc, ticketState) === fieldName) {
    if (ticketStatus === "agent_running_step") return "current-running";
    if (ticketStatus === "errored") return "errored";
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
    ticketState: detail.state,
    ticketStatus: detail.ticket_status,
    fieldName,
    fieldHasProposal: Boolean(fieldSlot(detail, fieldName).proposal)
  });
}

export function recapVisibleFor(lc: Lifecycle | null, state: string): boolean {
  if (!lc) return false;
  return lc.stateOrder.indexOf(state) > 1;
}

// --- per-type lifecycle lookup over a fetched manifest response -----------------
// Pure: lives here (not in the Svelte-runes manifest.svelte.ts) so the unit test
// exercises the REAL selector, not a copy (Codex F6). Memoizes the built Lifecycle
// by typeId, keyed on the response OBJECT so a fresh fetched response rebuilds.
// Returns null while the manifest is still loading (no response yet) OR when the
// type is absent from a loaded manifest — the ROUTE tells those apart via
// manifest.loading / manifest.error + a type-present check (Codex F3).
const lifecycleMemo = new WeakMap<TicketTypesResponse, Map<string, Lifecycle | null>>();

export function lifecycleFor(
  response: TicketTypesResponse | undefined,
  typeId: string | undefined | null
): Lifecycle | null {
  if (!response || !typeId) return null;
  let byType = lifecycleMemo.get(response);
  if (!byType) {
    byType = new Map<string, Lifecycle | null>();
    lifecycleMemo.set(response, byType);
  }
  if (byType.has(typeId)) return byType.get(typeId) ?? null;
  const entry = response.types.find((t) => t.type_id === typeId);
  const lc = entry ? buildLifecycle(entry) : null;
  byType.set(typeId, lc);
  return lc;
}
