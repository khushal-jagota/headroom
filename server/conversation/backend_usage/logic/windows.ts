import type { BackendUsageWindow, BackendUsageWindowKind } from '../contracts.ts';
import { parse_iso_datetime } from '../../../core/clock.ts';
export function percentage(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
    ? value
    : null;
}
export function parse_datetime(value: unknown): Date | null {
  let parsed: Date;
  if (typeof value === 'number' && Number.isFinite(value))
    parsed = new Date(Math.round(value * 1_000_000) / 1000);
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
/** Every adapter builds its windows here, so labels and lengths read the same for each provider. */
export function usage_window(input: {
  readonly id: string;
  readonly kind: BackendUsageWindowKind;
  readonly used_percent: number;
  readonly resets_at: Date | null;
  readonly model_scope: string | null;
}): BackendUsageWindow {
  const length = input.kind === 'five_hour' ? '5 hours' : '7 days';
  return {
    ...input,
    label: input.model_scope === null ? length : `${length} · ${input.model_scope}`,
    window_duration_mins: input.kind === 'five_hour' ? 300 : 10080,
  };
}
