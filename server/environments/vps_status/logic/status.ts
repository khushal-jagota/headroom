import { parseIsoDatetime } from '../../../core/clock.ts';
import type { StatusState } from '../contracts.ts';
export function overall_state(states: readonly StatusState[]): StatusState {
  return (
    (['critical', 'warning', 'review_needed', 'healthy', 'unavailable'] as const).find((state) =>
      states.includes(state),
    ) ?? 'unavailable'
  );
}
export function recognised_panels_workload_role(command: string): string | null {
  const tokens = command.trim().split(/\s+/);
  const executable = tokens[0].split('/').at(-1)!;
  if (['planner', 'panels'].includes(executable) && tokens[1] === 'serve') return 'planner';
  if (executable.startsWith('python') && tokens.slice(1, 4).join(' ') === '-m planner serve')
    return 'planner';
  return null;
}
export function instant(value: string): bigint {
  const parsed = parseIsoDatetime(value);
  return parsed.aware ? parsed.epochMicroseconds : parseIsoDatetime(value + 'Z').epochMicroseconds;
}
export function utc_iso(value: string | bigint): string {
  const microseconds = typeof value === 'bigint' ? value : instant(value);
  const remainder = ((microseconds % 1_000_000n) + 1_000_000n) % 1_000_000n;
  const seconds = (microseconds - remainder) / 1_000_000n;
  const base = new Date(Number(seconds) * 1000).toISOString().slice(0, 19);
  return base + (remainder ? '.' + String(remainder).padStart(6, '0') : '') + '+00:00';
}
/** Round the exact binary float, with Python's halfway-to-even rule. */
export function round_percent(value: number): number {
  if (!Number.isFinite(value) || value === 0) return value;
  const buffer = new DataView(new ArrayBuffer(8));
  buffer.setFloat64(0, Math.abs(value));
  const bits = buffer.getBigUint64(0);
  const exponent = Number((bits >> 52n) & 2047n);
  let numerator = ((bits & ((1n << 52n) - 1n)) + (exponent ? 1n << 52n : 0n)) * 100n;
  const shift = (exponent || 1) - 1023 - 52;
  const denominator = shift < 0 ? 1n << BigInt(-shift) : 1n;
  if (shift > 0) numerator <<= BigInt(shift);
  let rounded = numerator / denominator;
  const remainder = numerator % denominator;
  if (remainder * 2n > denominator || (remainder * 2n === denominator && rounded % 2n !== 0n))
    rounded++;
  return ((value < 0 ? -1 : 1) * Number(rounded)) / 100;
}

export function decimal_integer(value: string): bigint {
  const digits = Array.from(value, (character) => {
    const code = character.codePointAt(0)!;
    let first = code;
    while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
    return String((code - first) % 10);
  });
  return BigInt(digits.join(''));
}
