/** Update state follows the persisted attempt, offered release, and one instant. */
import { parseIsoDatetime } from '../../../core/clock.ts';
import type { ParsedIsoDatetime } from '../../../core/clock.ts';
import type { PublishedUpdate, UpdateAttempt, UpdateExperience } from '../contracts.ts';

export function instant(value: string): ParsedIsoDatetime {
  const parsed = parseIsoDatetime(value.replaceAll('Z', '+00:00'));
  if (!parsed.aware) throw new Error('update timestamp requires a time zone');
  return parsed;
}
function floor_div(value: bigint, by: bigint): bigint {
  const result = value / by;
  return value % by < 0 ? result - 1n : result;
}
function rounded_microseconds(seconds: number): bigint {
  const value = seconds * 1_000_000;
  const lower = Math.floor(value);
  const fraction = value - lower;
  return BigInt(fraction === 0.5 ? (lower % 2 === 0 ? lower : lower + 1) : Math.round(value));
}
export function add_seconds(value: ParsedIsoDatetime, seconds: number): ParsedIsoDatetime {
  const epochMicroseconds = value.epochMicroseconds + rounded_microseconds(seconds);
  const zone = value.iso.slice(-6);
  const minutes =
    (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4))) * (zone[0] === '-' ? -1 : 1);
  const local = epochMicroseconds + BigInt(minutes) * 60_000_000n;
  const date = new Date(Number(floor_div(local, 1000n)));
  const microsecond = ((local % 1_000_000n) + 1_000_000n) % 1_000_000n;
  const fraction = microsecond === 0n ? '' : '.' + microsecond.toString().padStart(6, '0');
  return {
    date: new Date(Number(floor_div(epochMicroseconds, 1000n))),
    iso: date.toISOString().slice(0, 19) + fraction + zone,
    aware: true,
    epochMicroseconds,
  };
}
export function resolve(
  attempt: UpdateAttempt | null,
  available: PublishedUpdate | null,
): UpdateExperience {
  const idle: UpdateExperience =
    available !== null ? ['available', ['update'], null, null] : ['up_to_date', [], null, null];
  if (attempt === null) return idle;
  // A waiting attempt is a record from an earlier release. Startup ends it.
  if (
    attempt.state === 'waiting' ||
    attempt.state === 'preparing' ||
    attempt.state === 'installing'
  )
    return ['updating', [], null, attempt.deadline_at];
  if (attempt.state === 'failed') {
    if (available === null) return ['failed', [], attempt.detail, null];
    if (available.manifest.app_sha !== attempt.to_sha) return idle;
    return ['failed', ['retry'], attempt.detail, null];
  }
  return idle;
}
