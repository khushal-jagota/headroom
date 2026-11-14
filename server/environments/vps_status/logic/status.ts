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
export function round_percent(value: number): number {
  return Math.round(value * 100) / 100;
}
