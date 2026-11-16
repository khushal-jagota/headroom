import type { Coverage, Measure, OutputMeasure } from '../contracts.ts';
import { invalid, local_midnight, period_interval } from './dates.ts';
import { number } from './formulas.ts';
export function validate_coverage(start: unknown, end: unknown, limitation: unknown): void {
  for (const v of [start, end])
    if (v !== null) {
      const n = number(v);
      if (n < -62135596800 || n >= 253402300800)
        throw invalid('scalar query timestamp is outside the UTC datetime range');
    }
  if (start !== null && end !== null && number(start) >= number(end))
    throw invalid('scalar query interval must be positive');
  if (limitation !== null && (typeof limitation !== 'string' || !limitation.trim()))
    throw invalid('invalid scalar coverage limitation');
}
export function combine_coverage(values: Iterable<[Coverage, string[]]>): [Coverage, string[]] {
  const priority = { not_applicable: 0, complete: 1, unknown: 2, partial: 3 };
  let status: Coverage = 'not_applicable';
  const reasons = new Set<string>();
  for (const [s, r] of values) {
    if (priority[s] > priority[status]) status = s;
    for (const v of r) reasons.add(v);
  }
  return [status, [...reasons]];
}
export function observation_coverage(
  measure: Measure,
  captured: OutputMeasure | null,
  day: string,
  start: number | null,
  end: number | null,
  limitation: string | null,
): [Coverage, string[]] {
  const reasons = limitation === null ? [] : [limitation];
  if (measure.kind !== 'period_count' && captured?.kind !== 'period_count')
    return reasons.length ? ['partial', reasons] : ['not_applicable', []];
  let status: Coverage = 'unknown';
  if (!captured || captured.kind !== 'period_count' || !captured.period)
    reasons.push('Period coverage is unavailable for this observation.');
  else {
    try {
      const interval = period_interval(day, captured.period);
      const first = local_midnight(interval.start_date, captured.timezone),
        last = local_midnight(interval.end_date_exclusive, captured.timezone);
      if ((start !== null && start !== first) || (end !== null && end !== last)) {
        status = 'partial';
        reasons.push('The query interval does not match the complete local period.');
      } else if (start !== null && end !== null) status = 'complete';
      else reasons.push('Both query bounds are required to establish period coverage.');
    } catch {
      reasons.push('The period boundaries are unavailable for this observation.');
    }
    if (
      captured.kind !== measure.kind ||
      captured.timezone !== measure.timezone ||
      captured.period !== measure.period
    ) {
      if (status !== 'partial') status = 'unknown';
      reasons.push('The period definition differs from the captured observation definition.');
    }
  }
  if (limitation !== null) status = 'partial';
  return [status, reasons];
}
