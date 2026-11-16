import type { Measurement, MetricNumber } from '../contracts.ts';
import { float, number } from '../codec.ts';
export function counter_rate(
  current: MetricNumber,
  previous: MetricNumber | null,
  elapsed: MetricNumber,
  options: { same_identity: boolean; unit: string; scale?: number },
): Measurement {
  let reason: string | null = null;
  if (previous === null || !options.same_identity)
    reason = 'baseline unavailable or identity changed';
  else if (number(elapsed) <= 0) reason = 'monotonic interval did not advance';
  else if (number(current) < number(previous)) reason = 'counter reset';
  if (reason !== null)
    return { value: null, unit: options.unit, reason, numerator: null, duration: null };
  const difference = (number(current) - number(previous!)) * (options.scale ?? 1);
  return {
    value: float(difference / number(elapsed)),
    unit: options.unit,
    reason: null,
    numerator: float(difference),
    duration: elapsed,
  };
}
export function host_cpu_percent(
  current: readonly number[],
  previous: readonly number[] | null,
  options: { same_identity: boolean },
): Measurement {
  if (
    previous === null ||
    !options.same_identity ||
    current.length !== previous.length ||
    current.some((value, index) => value < previous[index])
  )
    return {
      value: null,
      unit: 'percent_total_capacity',
      reason: 'baseline unavailable, identity changed, or counter reset',
    };
  const total =
    current.reduce((sum, value) => sum + value, 0) -
    previous.reduce((sum, value) => sum + value, 0);
  let idle = current[3] - previous[3];
  if (current.length > 4) idle += current[4] - previous[4];
  if (total <= 0 || !(idle >= 0 && idle <= total))
    return {
      value: null,
      unit: 'percent_total_capacity',
      reason: 'CPU interval did not advance safely',
    };
  return {
    value: float((100 * (total - idle)) / total),
    unit: 'percent_total_capacity',
    reason: null,
  };
}
