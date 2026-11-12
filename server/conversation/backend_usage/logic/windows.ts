import type { BackendUsageWindow, BackendUsageWindowKind } from '../contracts.ts';
import { parse_iso_datetime } from '../../../core/clock.ts';
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
export function percentage(value: unknown): number | null {
  return typeof value === 'number' && value >= 0 && value <= 100 ? value : null;
}
export function parse_datetime(value: unknown): Date | null {
  let parsed: Date;
  if (typeof value === 'number') parsed = new Date(Math.round(value * 1_000_000) / 1000);
  else if (
    typeof value === 'string' &&
    value &&
    /(?:Z|[+-]\d\d(?::?\d\d)?)$/.test(value) &&
    /^(?:\d{4}-?\d{2}-?\d{2}|\d{4}-?W\d{2}(?:-?[1-7])?).\d{2}/u.test(value)
  ) {
    try {
      parsed = parse_iso_datetime(value);
    } catch {
      return null;
    }
  } else return null;
  return Number.isFinite(parsed.getTime()) &&
    parsed.getUTCFullYear() >= 1 &&
    parsed.getUTCFullYear() <= 9999
    ? parsed
    : null;
}
export function window_kind(minutes: unknown): BackendUsageWindowKind | null {
  return minutes === 300 ? 'five_hour' : minutes === 10080 ? 'seven_day' : null;
}
function claude_reset_time(value: Record<string, unknown>): Date | null {
  for (const key of ['resets_at', 'reset_at']) {
    const parsed = parse_datetime(value[key]);
    if (parsed !== null) return parsed;
  }
  return null;
}
function claude_limit_entry(value: unknown): BackendUsageWindow | null {
  if (!object(value)) return null;
  const used_percent = percentage(value.percent) ?? percentage(value.utilization);
  const resets_at = claude_reset_time(value);
  if (used_percent === null || resets_at === null) return null;
  const scope = object(value.scope) ? value.scope : null;
  const model = object(scope?.model) ? scope.model : null;
  let model_name = model?.display_name;
  if (typeof model_name !== 'string' || !model_name) model_name = model?.displayName;
  let kind: BackendUsageWindowKind;
  let model_scope: string | null;
  if (typeof model_name === 'string' && model_name) {
    kind = 'seven_day';
    model_scope = model_name;
  } else {
    if (value.group === 'session') kind = 'five_hour';
    else if (
      typeof value.group === 'string' &&
      ['weekly', 'week', 'seven_day'].includes(value.group)
    )
      kind = 'seven_day';
    else return null;
    model_scope = null;
  }
  return { kind, used_percent, resets_at, model_scope };
}
export function parse_claude_windows(body: unknown): readonly BackendUsageWindow[] | null {
  if (!object(body)) return null;
  const scoped = Array.isArray(body.limits)
    ? body.limits
        .map(claude_limit_entry)
        .filter((value): value is BackendUsageWindow => value !== null)
    : [];
  if (scoped.length) return scoped;
  const windows: BackendUsageWindow[] = [];
  for (const [key, value] of Object.entries(body)) {
    if (!object(value)) continue;
    const used_percent = percentage(value.utilization);
    const resets_at = claude_reset_time(value);
    if (used_percent === null || resets_at === null) continue;
    const suffix = key.startsWith('seven_day_') ? key.slice('seven_day_'.length) : key;
    const title = suffix
      .replaceAll('_', ' ')
      .replace(
        /\p{L}[\p{L}\p{M}]*/gu,
        (word) => word[0].toUpperCase() + word.slice(1).toLowerCase(),
      );
    windows.push({
      kind: key === 'five_hour' ? 'five_hour' : 'seven_day',
      used_percent,
      resets_at,
      model_scope: key === 'five_hour' || key === 'seven_day' ? null : title,
    });
  }
  return windows.length ? windows : null;
}
