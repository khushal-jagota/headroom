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
