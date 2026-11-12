/** Immutable, validated workflow declarations. Runtime helpers do not modify these records. */
export type StageOwnershipMode = 'worker' | 'user' | 'paired';
export const BRIEF_FIELD_ID = 'brief';
export const CONSEQUENCES_FIELD_ID = 'consequences';
export const NEEDS_BRIEF_STAGE_ID = 'needs_brief';
export const RETIRED_PLANNING_WORKER_TYPES: ReadonlySet<string> = new Set([
  'planning-day',
  'planning-midday-check',
  'planning-sprint',
]);
export const RETIRED_WORKER_TYPES: ReadonlySet<string> = new Set([
  ...RETIRED_PLANNING_WORKER_TYPES,
  'personal',
]);
export interface StageDefinition {
  readonly id: string;
  readonly label: string;
  readonly gating_field: string | null;
  readonly is_terminal: boolean;
  readonly ownership_mode: StageOwnershipMode | null;
  readonly instructions: string;
}
export interface FieldDefinition {
  readonly id: string;
  readonly label: string;
}
export interface WorkerProfile {
  readonly specialist_skill: string;
  readonly default_model: string;
  readonly default_reasoning_effort: string | null;
  readonly toolset_profile: string;
  readonly default_backend: string;
}
export interface WorkflowDefinition {
  readonly stages: readonly StageDefinition[];
  readonly fields: readonly FieldDefinition[];
  readonly label: string;
  readonly guidance: string;
}
export interface WorkerTypeDefinition extends WorkflowDefinition {
  readonly worker_type: string;
  readonly worker_profile: WorkerProfile;
}
export interface TicketWorkflowDefinition extends WorkflowDefinition {
  readonly specialist_skill: string | null;
}
export interface WorkflowManifest {
  worker_type: string | null;
  label: string;
  guidance: string;
  stages: StageDefinition[];
  fields: FieldDefinition[];
  advance: Record<string, string>;
  ceiling_range: string[];
  default_ceiling: string;
  worker_profile_id: string | null;
}
export interface WorkerTypeManifest extends WorkflowManifest {
  worker_type: string;
  worker_profile_id: string;
  creatable: boolean;
  default_backend: string;
  default_model: string;
  default_reasoning_effort: string | null;
}
export interface GuidanceFragment {
  target: string;
  text: string;
}
export interface GuidanceConversionReport {
  skill_sha256: string;
  body_sha256: string;
  fragments: GuidanceFragment[];
  preserved_body_bytes: number;
}
