import { PlannerFetchError } from '../api';
import type { FailureCode, RunFailure, SourceHealth, SourceStatus } from './wire';

const failureLabels: Record<FailureCode, string> = {
  runtime_failure: 'The source could not complete.',
  malformed_protocol: 'The source returned an invalid message.',
  credential_unavailable: 'A required credential is not configured.',
  transport_failure: 'The provider could not be reached.',
  http_error: 'The provider refused the request.',
  invalid_json: 'The provider returned invalid JSON.',
  invalid_output: 'The source returned invalid observations.',
  stale_definition: 'The saved source script changed during the run.',
  budget_exhausted: 'The source exceeded its execution timeout.',
  interrupted: 'The run was interrupted.'
};
export const healthLabels: Record<SourceHealth, string> = {
  disabled: 'Disabled', never_collected: 'Never collected', healthy: 'Healthy',
  failed: 'Collection failed'
};
export function failureLabel(failure: RunFailure | null): string {
  if (!failure) return '';
  if (failure.code === 'http_error' && [401, 403].includes(failure.http_status ?? 0)) {
    return 'The provider rejected the credential or its permissions.';
  }
  const reason = failureLabels[failure.code] ?? 'The source could not complete.';
  const detail = failure.exception_type ? ` ${failure.exception_type}${failure.source_line ? ` at script line ${failure.source_line}` : ''}.` : '';
  return reason + detail;
}
export function runRevision(status: SourceStatus): number | null {
  if (status.running_run_id || ['pending', 'running'].includes(status.latest_run?.status ?? '')) return null;
  return status.source.active_script_revision;
}
export function fixedWriteError(error: unknown): string {
  if (error instanceof PlannerFetchError && (error.status === 409 || ['conflict', 'stale_claim'].includes(error.code))) {
    return 'The settings changed. Refresh and try again.';
  }
  return 'Could not save this change. Check the fields and try again.';
}
export function timestamp(value: number | null): string {
  return value === null ? 'None yet' : new Date(value * 1000).toLocaleString();
}
