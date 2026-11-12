import type { UpdateAttemptState } from '../contracts.ts';
export interface StoreOperation {
  kind: 'read' | 'create' | 'change' | 'remove' | 'import_legacy';
  data_directory?: string;
  running_sha?: string | null;
  attempt?: unknown;
  attempt_id?: string;
  expected?: UpdateAttemptState[];
  patch?: Record<string, unknown>;
}
export interface StoreProbeRequest {
  path: string;
  now: string;
  operations: StoreOperation[];
}
export interface StoreProbeAnswer {
  result: unknown;
  error: { type: string; message: string } | null;
}
export interface StoreProbeResult {
  answers: StoreProbeAnswer[];
  file_text: string | null;
  file_mode: number | null;
  emitted: number;
}
export interface ContendedStoreRequest {
  path: string;
  now: string;
  attempt_id: string;
  expected: UpdateAttemptState[];
}
export interface ExperienceCase {
  attempt: unknown;
  available: Record<string, unknown> | null;
  app_sha: string | null;
  now: string;
  mac_applies: boolean;
  mac_phase: string | null;
  mac_reported_at: string | null;
  mac_report_seconds: number;
  success_seconds: number;
}
