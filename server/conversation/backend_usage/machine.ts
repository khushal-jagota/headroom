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
    !Array.isArray(value.windows) ||
    (value.detail !== null && typeof value.detail !== 'string')
  )
    throw invalid();
  const date = (raw: unknown): Date => {
    if (typeof raw !== 'string' || !Number.isFinite(Date.parse(raw))) throw invalid();
    return new Date(raw);
  };
  const observed_at = value.observed_at === null ? null : date(value.observed_at);
  if (value.outcome === 'succeeded' && observed_at === null) throw invalid();
  const windows: BackendUsageWindow[] = value.windows.map((window) => {
    if (
      !is_record(window) ||
      !['five_hour', 'seven_day'].includes(String(window.kind)) ||
      typeof window.used_percent !== 'number' ||
      !Number.isFinite(window.used_percent) ||
      window.used_percent < 0 ||
      window.used_percent > 100 ||
      (window.model_scope !== null && typeof window.model_scope !== 'string') ||
      (window.scope_label !== null && typeof window.scope_label !== 'string')
    )
      throw invalid();
    return {
      kind: window.kind as BackendUsageWindow['kind'],
      used_percent: window.used_percent,
      resets_at: date(window.resets_at),
      model_scope: window.model_scope,
      scope_label: window.scope_label,
    };
  });
  return {
    backend_key,
    outcome: value.outcome as BackendUsageResult['outcome'],
    detail: value.detail,
    observed_at,
    windows,
  };
}
