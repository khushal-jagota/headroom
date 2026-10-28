import type { CanonicalModelValidation, PickerRow } from "./modelResolutionWire";

/** Presentation only. Selection, support, evidence and validity come from the read model. */
export function pickerRowName(row: PickerRow): string {
  const identity = row.selection_kind === "provider_alias" || row.selection_kind === "panels_alias"
    ? `${row.submitted_token} → ${row.resolved_model_id ?? "unresolved"}`
    : [row.display_name, row.submitted_token].filter((part, index, parts) => part && parts.indexOf(part) === index).join(" · ");
  const previous = row.previous_alias_resolution?.resolved_model_id;
  return [identity, previous && previous !== row.resolved_model_id ? `was ${previous}` : null,
    row.evidence_status !== "fresh" ? row.evidence_status : null,
    row.current_selection ? "current selection" : null,
    `catalog ${row.catalog_presence}`].filter(Boolean).join(" · ");
}

export function validationSummary(validation: CanonicalModelValidation): string {
  return `Model ${validation.state} for ${validation.selection.destination === "macos" ? "Mac" : "VPS"}.`;
}

export function validationDetail(validation: CanonicalModelValidation): string {
  const unavailable = validation.unavailable;
  const evidence = validation.evidence ?? unavailable?.retained_evidence;
  const context = unavailable?.attempted_context ?? evidence?.context;
  return [unavailable?.detail, `Reason: ${validation.reason}.`,
    context ? `Context: ${context.context_ref}. Destination: ${context.destination}. Workspace: ${context.localized_workspace_folder}.` : "No launch context was observed.",
    evidence ? `Observation: ${evidence.observation_id}, revision ${evidence.observation_revision}, at ${evidence.observed_at}.` : "No catalog evidence was supplied.",
    unavailable ? `Unavailable attempt observed at: ${unavailable.observed_at}.` : null,
    `Required action: ${validation.corrective_action.replaceAll("_", " ")}.`,
    "Check again repeats the same read. A server observation does not establish a native destination failure."
  ].filter(Boolean).join(" ");
}

export function pickerRowDetail(row: PickerRow): string {
  return [pickerRowName(row), `Selection kind: ${row.selection_kind}.`,
    `Effort: ${row.supported_reasoning_efforts === null ? "unknown" : row.supported_reasoning_efforts.join(", ") || "none"}.`,
    row.current_alias_resolution ? `Current alias target: ${row.current_alias_resolution.resolved_model_id}.` : null,
    row.context ? `Context: ${row.context.context_ref}. Destination: ${row.context.destination}. Workspace: ${row.context.localized_workspace_folder}.` : "Context unknown.",
    `Observed at: ${row.observed_at ?? "unknown"}.`,
    ...row.provenance.map(reference => `Source: ${reference.evidence.observation_id}, revision ${reference.evidence.observation_revision}, row ${reference.row_ordinal}.`),
    validationDetail(row.validation)].filter(Boolean).join(" ");
}
