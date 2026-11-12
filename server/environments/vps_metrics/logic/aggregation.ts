import type { AggregateStatistics, Measurement, ProgressMarker, Sample } from '../contracts.ts';
import { float, number } from '../codec.ts';
export function signals(sample: Sample): Record<string, Record<string, Measurement>> {
  return Object.fromEntries([
    ['', sample.metrics],
    ...sample.services.map((service) => [service.identity, service.metrics]),
  ]);
}
export function interval_start(sample: Sample, previous: ProgressMarker | null): number {
  if (previous === null || previous.boot_id !== sample.boot_id) return number(sample.timestamp);
  const elapsed = number(sample.monotonic) - number(previous.monotonic);
  return elapsed <= 0
    ? number(sample.timestamp)
    : number(sample.timestamp) - Math.min(elapsed, number(sample.expected_interval));
}
export function empty_statistics(): AggregateStatistics {
  return {
    weighted_sum: float(0),
    valid_duration: float(0),
    expected_duration: float(0),
    minimum: null,
    maximum: null,
    count: 0,
    numerator: null,
    denominator: null,
  };
}
export function accumulate(
  stats: AggregateStatistics,
  measurement: Measurement,
  duration: number,
): void {
  stats.expected_duration = float(number(stats.expected_duration) + duration);
  const value = measurement.value === null ? null : number(measurement.value);
  if (value === null || !Number.isFinite(value)) return;
  stats.weighted_sum = float(number(stats.weighted_sum) + value * duration);
  stats.valid_duration = float(number(stats.valid_duration) + duration);
  if (stats.minimum === null || value <= number(stats.minimum)) stats.minimum = measurement.value;
  if (stats.maximum === null || value >= number(stats.maximum)) stats.maximum = measurement.value;
  stats.count = number(stats.count) + 1;
  const numerator = measurement.numerator;
  const source_duration = measurement.duration;
  if (
    numerator !== undefined &&
    numerator !== null &&
    source_duration !== undefined &&
    source_duration !== null &&
    number(source_duration) > 0
  ) {
    stats.numerator = float(
      (stats.numerator === null ? 0 : number(stats.numerator)) +
        (number(numerator) * duration) / number(source_duration),
    );
    stats.denominator = float(
      (stats.denominator === null ? 0 : number(stats.denominator)) + duration,
    );
  }
}
export function bucket_intervals(start: number, end: number, width: number): [number, number][] {
  const intervals: [number, number][] = [];
  while (start < end) {
    const bucket = Math.floor(start / width) * width;
    const boundary = Math.min(end, bucket + width);
    intervals.push([bucket, boundary - start]);
    start = boundary;
  }
  return intervals;
}
