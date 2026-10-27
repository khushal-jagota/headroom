import { PlannerFetchError } from '../api';
import type { FailureCode, RunFailure, RunMode, SourceHealth, SourceStatus } from './wire';

const failureLabels: Record<FailureCode, string> = {
  sandbox_unavailable: 'Source execution is unavailable.',
  runtime_failure: 'The source could not complete.',
  malformed_protocol: 'The source returned an invalid message.',
  unauthorized_request: 'The source requested an unauthorized destination.',
  dns_policy_refused: 'The provider hostname resolved to an address blocked by the network policy.',
  credential_unavailable: 'A required credential is not configured or permitted.',
  transport_failure: 'The provider could not be reached.',
  http_error: 'The provider refused the request.',
  invalid_json: 'The provider returned invalid JSON.',
  invalid_output: 'The source returned invalid observations.',
  missing_output: 'The source did not return every required observation.',
  stale_definition: 'The source or measure changed during the run.',
  budget_exhausted: 'The source exceeded a run limit.',
  interrupted: 'The run was interrupted.'
};
export const healthLabels: Record<SourceHealth, string> = {
  disabled: 'Disabled', never_collected: 'Never collected', healthy: 'Healthy',
  stale: 'Overdue', failed: 'Collection failed'
};
export function failureLabel(failure: RunFailure | null): string {
  if (!failure) return '';
  if (failure.code === 'http_error' && [401, 403].includes(failure.http_status ?? 0)) {
    return 'The provider rejected the credential or its permissions.';
  }
  return failureLabels[failure.code] ?? 'The source could not complete.';
}
export function runRevision(status: SourceStatus, mode: RunMode): number | null {
  if (mode === 'collection' && !status.source.enabled) return null;
  if (status.running_run_id || ['pending', 'running'].includes(status.latest_run?.status ?? '')) return null;
  return mode === 'test' ? status.source.pending_script_revision : status.source.active_script_revision;
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
/** One mounted timeout at the server's next clock-derived status boundary. */
export function armStatusBoundary(
  statuses: readonly SourceStatus[], refresh: () => void,
  now = Date.now(),
  schedule: (callback: () => void, delay: number) => ReturnType<typeof setTimeout> = setTimeout,
  cancel: (handle: ReturnType<typeof setTimeout>) => void = clearTimeout
): () => void {
  const times = statuses.flatMap(status => status.next_status_change_at === null ? [] : [status.next_status_change_at * 1000]);
  if (!times.length) return () => {};
  const handle = schedule(refresh, Math.min(2_147_483_647, Math.max(0, Math.min(...times) - now + 50)));
  return () => cancel(handle);
}
