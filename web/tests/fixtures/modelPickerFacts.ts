import type { CanonicalModelValidation, PickerRow, CatalogEvidenceReference } from "../../src/lib/conversation/modelResolutionWire";

export const pickerEvidence: CatalogEvidenceReference = {
  observation_id: "fixture-observation", observation_revision: "fixture-revision", observed_at: 1,
  attempt_id: "fixture-attempt", clock_uncertainty_seconds: null,
  context: {context_ref: "fixture-context", destination: "macos", backend_key: "claude", executor_identity: null,
    workspace: {workspace_folder: "/fixture", workspace_path_policy: "native_absolute", workspace_home_relative: null},
    localized_workspace_folder: "/fixture", executable_identity: null, executable_version: null, provider_scope: null, account_generation: null}
};
export const pickerValidation: CanonicalModelValidation = {
  selection: {destination: "macos", backend_key: "claude", submitted_token: "opus", selection_kind: "provider_alias", reasoning_effort: "high", selection_revision: "fixture-selection"},
  state: "unavailable", reason: "catalog_unavailable", corrective_action: "refresh_catalog",
  resolved_model_id: "claude-opus-5", launch_token: "opus", evidence: null,
  current_alias_resolution: null, previous_alias_resolution: null,
  unavailable: {kind: "unavailable", backend_key: "claude", destination: "macos", attempted_context: null,
    reason: "catalog_unavailable", corrective_action: "refresh_catalog", detail: "The destination catalog read returned no observation.", observed_at: 1, retained_evidence: pickerEvidence}
};
export const pickerRows: readonly PickerRow[] = [
  {submitted_token: "opus", selection_kind: "provider_alias", resolved_model_id: "claude-opus-5", display_name: "Opus 5", supported_reasoning_efforts: null,
    context: null, observed_at: null, evidence_status: "unverified", catalog_presence: "unknown", current_selection: true,
    current_alias_resolution: {submitted_token: "opus", resolved_model_id: "claude-opus-5", evidence: pickerEvidence}, previous_alias_resolution: {submitted_token: "opus", resolved_model_id: "claude-opus-4-5", evidence: pickerEvidence}, provenance: [{evidence: pickerEvidence, row_ordinal: 0}], validation: pickerValidation},
  {submitted_token: "claude-opus-5", selection_kind: "concrete", resolved_model_id: "claude-opus-5", display_name: "Opus 5", supported_reasoning_efforts: [],
    context: null, observed_at: 1, evidence_status: "stale", catalog_presence: "present", current_selection: false,
    current_alias_resolution: null, previous_alias_resolution: null, provenance: [], validation: pickerValidation},
  ...Array.from({length: 12}, (_, index): PickerRow => ({submitted_token: `exact-concrete-id-with-long-context-${index}`, selection_kind: "concrete", resolved_model_id: null,
    display_name: `Retained catalog choice ${index}`, supported_reasoning_efforts: ["high"], context: null, observed_at: 1, evidence_status: "stale", catalog_presence: "absent", current_selection: false,
    current_alias_resolution: null, previous_alias_resolution: null, provenance: [], validation: pickerValidation}))
];
