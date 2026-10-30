import type { CanonicalModelValidation, CatalogEvidenceReference } from "../../src/lib/conversation/modelResolutionWire";

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
export const pickerModels: import("../../src/lib/conversation/wire").BackendModel[] = [
  {enabled: true, model_id: "opus", resolved_model_id: "claude-opus-5", display_name: "Opus 5", reasoning_effort_options: ["high"]},
  {enabled: true, model_id: "claude-opus-5", resolved_model_id: "claude-opus-5", display_name: "Opus 5", reasoning_effort_options: ["high"]},
  ...Array.from({length: 12}, (_, index) => ({enabled: true, model_id: `exact-concrete-id-with-long-context-${index}`,
    display_name: `Retained catalog choice ${index}`, reasoning_effort_options: ["high"]}))
];

// Captured from claude_models_for_version("2.1.293") on candidate 6196cd69.
// Simulated CLI version, not a live provider observation.
export const representativeClaudeModels: import("../../src/lib/conversation/wire").BackendModel[] = [
  {
    "model_id": "claude-opus-5-5",
    "display_name": "Opus 5.5",
    "resolved_model_id": "claude-opus-5-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-sonnet-5-5",
    "display_name": "Sonnet 5.5",
    "resolved_model_id": "claude-sonnet-5-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-fable-5-1",
    "display_name": "Fable 5.1",
    "resolved_model_id": "claude-fable-5-1",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-fable-5",
    "display_name": "Fable 5",
    "resolved_model_id": "claude-fable-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-opus-5",
    "display_name": "Opus 5",
    "resolved_model_id": "claude-opus-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-opus-4-8",
    "display_name": "Opus 4.8",
    "resolved_model_id": "claude-opus-4-8",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-opus-4-7",
    "display_name": "Opus 4.7",
    "resolved_model_id": "claude-opus-4-7",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-opus-4-6",
    "display_name": "Opus 4.6",
    "resolved_model_id": "claude-opus-4-6",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-opus-4-5",
    "display_name": "Opus 4.5",
    "resolved_model_id": "claude-opus-4-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-sonnet-5",
    "display_name": "Sonnet 5",
    "resolved_model_id": "claude-sonnet-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-sonnet-4-6",
    "display_name": "Sonnet 4.6",
    "resolved_model_id": "claude-sonnet-4-6",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-haiku-5-5",
    "display_name": "Haiku 5.5",
    "resolved_model_id": "claude-haiku-5-5",
    "reasoning_effort_options": [
      "low",
      "medium",
      "high",
      "xhigh",
      "max"
    ],
    "enabled": true
  },
  {
    "model_id": "claude-haiku-4-5",
    "display_name": "Haiku 4.5",
    "resolved_model_id": "claude-haiku-4-5",
    "reasoning_effort_options": [],
    "enabled": true
  }
];

// Representative tokens from the owner's simple picker reference, not a live model/list.
export const representativeCodexModels: import("../../src/lib/conversation/wire").BackendModel[] = [
  {enabled:true, model_id:"gpt-6.1-sol", display_name:"GPT 6.1 Sol", reasoning_effort_options:["low","medium","high"]},
  {enabled:true, model_id:"gpt-6-astra", display_name:"GPT 6 Astra", reasoning_effort_options:["low","medium","high"]},
  {enabled:true, model_id:"gpt-6.1-terra", display_name:"GPT 6.1 Terra", reasoning_effort_options:["low","medium","high"]},
  {enabled:true, model_id:"gpt-6-luna", display_name:"GPT 6 Luna", reasoning_effort_options:["low","medium","high"]}
];
