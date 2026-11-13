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
export function source_error(error: unknown, allowed: readonly string[]): string {
  const reason = evidence_reason(error);
  if (allowed.includes(reason) || (allowed.includes('EIO') && is_os_error(error))) return reason;
  throw error;
}
