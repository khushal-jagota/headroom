/** Validate machine allowance observations before the server retains them. */
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { BackendUsageResult, BackendUsageWindow } from './contracts.ts';
import { HttpError } from '../../core/http/errors.ts';
import { is_record } from '../../core/records.ts';
export function decode_machine_usage(
  backend_key: BackendInstanceId,
  value: unknown,
): BackendUsageResult {
  const invalid = () =>
    new HttpError(502, 'The machine returned an invalid allowance observation.');
  if (
    !is_record(value) ||
    value.backend_key !== backend_key ||
    !['succeeded', 'unavailable', 'unauthenticated', 'failed'].includes(String(value.outcome)) ||
    (value.detail !== null && typeof value.detail !== 'string')
  )
    throw invalid();
  const observed_at = value.observed_at === null ? null : iso_date(value.observed_at);
  const windows = decode_usage_windows(value.windows);
  if (observed_at === undefined || windows === null) throw invalid();
  if (value.outcome === 'succeeded' && observed_at === null) throw invalid();
  return {
    backend_key,
    outcome: value.outcome as BackendUsageResult['outcome'],
    detail: value.detail,
    observed_at,
    windows,
  };
}
/**
 * Windows as JSON carries them, from a machine or from a stored observation. Null when any window
 * is not in the current shape, such as an observation saved before windows carried an id.
 */
export function decode_usage_windows(value: unknown): BackendUsageWindow[] | null {
  if (!Array.isArray(value)) return null;
  const windows: BackendUsageWindow[] = [];
  for (const window of value) {
    if (
      !is_record(window) ||
      typeof window.id !== 'string' ||
      !window.id ||
      !['five_hour', 'seven_day'].includes(String(window.kind)) ||
      typeof window.label !== 'string' ||
      !window.label ||
      typeof window.used_percent !== 'number' ||
      !Number.isFinite(window.used_percent) ||
      window.used_percent < 0 ||
      window.used_percent > 100 ||
      (window.window_duration_mins !== null &&
        !(
          Number.isInteger(window.window_duration_mins) && Number(window.window_duration_mins) > 0
        )) ||
      (window.model_scope !== null && typeof window.model_scope !== 'string')
    )
      return null;
    const resets_at = window.resets_at === null ? null : iso_date(window.resets_at);
    if (resets_at === undefined) return null;
    windows.push({
      id: window.id,
      kind: window.kind as BackendUsageWindow['kind'],
      label: window.label,
      used_percent: window.used_percent,
      resets_at,
      window_duration_mins: window.window_duration_mins as number | null,
      model_scope: window.model_scope as string | null,
    });
  }
  return windows;
}
/** Undefined when the value is not a date string. */
function iso_date(raw: unknown): Date | undefined {
  if (typeof raw !== 'string' || !Number.isFinite(Date.parse(raw))) return undefined;
  return new Date(raw);
}
