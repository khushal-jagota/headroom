import { is_os_error } from '../../../core/system_errors.ts';

/** Native error kinds retain the collector's source-local failure boundaries. */
export function evidence_error(kind: string, message: string): Error {
  if (kind === 'TypeError') return new TypeError(message);
  if (kind === 'RangeError') return new RangeError(message);
  return Object.assign(new Error(message), { code: kind });
}
export function evidence_reason(error: unknown): string {
  if (!(error instanceof Error)) throw error;
  return 'code' in error && typeof error.code === 'string' ? error.code : error.name;
}
/** The last logged text per reason, so a failure that repeats every sample is logged once. */
const logged_sources = new Map<string, string>();
export function source_error(error: unknown, allowed: readonly string[]): string {
  const reason = evidence_reason(error);
  if (allowed.includes(reason) || (allowed.includes('EIO') && is_os_error(error))) {
    const text = String((error as Error).stack ?? error);
    if (logged_sources.get(reason) !== text) {
      logged_sources.set(reason, text);
      console.warn(`VPS metrics source failed (${reason})`, error);
    }
    return reason;
  }
  throw error;
}
const ended_codes: readonly string[] = ['ENOENT', 'ESRCH'];
const denied_summary_ms = 3600_000;
let denied_count = 0;
let denied_logged_at = Date.now();
/**
 * Like source_error, for files of a process or cgroup that can end between listing and reading.
 * ENOENT and ESRCH mean it ended: that is normal, so it returns the reason without a log line.
 * With `denied_is_normal`, EACCES and EPERM are also silent, because another user's process files
 * are not readable. One summary line with the count is logged at most once an hour.
 */
export function ended_source_error(
  error: unknown,
  allowed: readonly string[],
  denied_is_normal = false,
  now: number = Date.now(),
): string {
  const reason = evidence_reason(error);
  if (ended_codes.includes(reason)) return reason;
  if (denied_is_normal && (reason === 'EACCES' || reason === 'EPERM')) {
    denied_count++;
    if (now - denied_logged_at >= denied_summary_ms) {
      console.warn(
        `VPS metrics: ${denied_count} reads of other users' process files were denied (${reason}); logging at most once an hour`,
      );
      denied_count = 0;
      denied_logged_at = now;
    }
    return reason;
  }
  return source_error(error, allowed);
}
