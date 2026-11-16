import type { Principal } from '../core/contracts.ts';
import type { Observation, OutputMeasure } from '../measures/contracts.ts';
import type { CandidateDatasetResult, OutputDataset } from '../datasets/contracts.ts';
import type { CredentialResolver } from '../credentials/contracts.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
export interface SourceScope {
  project_id: string;
  account: string;
}
export interface ScriptRevision {
  revision: number;
  text: string;
  sha256: string;
  authored_by: Principal;
  created_at: number;
}
export interface MeasureSource {
  id: string;
  name: string;
  scope: SourceScope;
  responsible: Principal;
  responsibility_revision: number;
  enabled: boolean;
  local_time: string | null;
  source_revision: number;
  active_script: ScriptRevision | null;
  created_at: number;
  updated_at: number;
}
export interface SourceCreate {
  name: string;
  scope: SourceScope;
  responsible: Principal;
}
export interface SourceScheduleBody {
  expected_revision: number;
  enabled: boolean;
  local_time: string | null;
}
export interface SourceScriptBody {
  expected_revision: number;
  text: string;
}
export type RunMode = 'collection' | 'test';
export type RunStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'interrupted';
export type RunTrigger = 'scheduled' | 'manual' | 'test';
export type FailureCode =
  | 'runtime_failure'
  | 'malformed_protocol'
  | 'credential_unavailable'
  | 'transport_failure'
  | 'http_error'
  | 'invalid_json'
  | 'invalid_output'
  | 'stale_definition'
  | 'budget_exhausted'
  | 'interrupted';
export type RuntimeBudget = 'wall_seconds' | 'output_bytes' | 'response_bytes' | 'heap_bytes';
export interface RunFailure {
  code: FailureCode;
  budget: RuntimeBudget | null;
  http_status: number | null;
  measure_id: string | null;
  date: string | null;
  exception_type: string | null;
  source_line: number | null;
}
export interface RunMetrics {
  request_count: number;
  response_bytes: number;
  child_output_bytes: number;
  elapsed_milliseconds: number;
  script_cpu_milliseconds: number;
}
export interface HistoryWindow {
  first_date: string;
  last_date: string;
}
export interface CandidateObservation {
  measure_id: string;
  date: string;
  value: number | null;
  unavailable_reason: 'provider_has_no_history' | 'provider_has_no_value' | null;
  query_start_at: number | null;
  query_end_at: number | null;
  limitation: string | null;
}
export interface SourceRun {
  id: string;
  source_id: string;
  mode: RunMode;
  trigger: RunTrigger;
  status: RunStatus;
  source_revision: number;
  script_revision: number;
  script_sha256: string;
  frozen_at: number;
  required_outputs: OutputMeasure[];
  history: HistoryWindow | null;
  schedule_date: string | null;
  schedule_time: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  failure: RunFailure | null;
  metrics: RunMetrics | null;
  observation_count: number;
  dataset_outputs: OutputDataset[];
  dataset_result_count: number;
  dataset_row_count: number;
}
export interface SourceRunDetail {
  run: SourceRun;
  test_candidates: NumericJSON<CandidateObservation[]> | null;
  test_dataset_results: NumericJSON<CandidateDatasetResult[]> | null;
}
export interface RuntimeLimits {
  wall_seconds: number;
  output_bytes: number;
  response_bytes: number;
  heap_megabytes: number;
}
export interface RunAdmission {
  run: SourceRun;
  script_text: string;
  previous: Observation[];
  limits: RuntimeLimits;
  deadline_monotonic: number;
}
export interface RunResult {
  observations: NumericJSON<CandidateObservation[]>;
  failure: RunFailure | null;
  metrics: RunMetrics;
  dataset_results: NumericJSON<CandidateDatasetResult[]>;
}
export interface SourceRuntime {
  cancel(run_id: string): void;
  execute(admission: RunAdmission, credentials: CredentialResolver): Promise<RunResult>;
}
export interface FailureEpisode {
  source_id: string;
  episode: number;
  opened_at: number;
  latest_run_id: string;
  closed_at: number | null;
}
export function failure(
  code: FailureCode,
  extra: Partial<Omit<RunFailure, 'code'>> = {},
): RunFailure {
  return {
    code,
    budget: null,
    http_status: null,
    measure_id: null,
    date: null,
    exception_type: null,
    source_line: null,
    ...extra,
  };
}
