/** `panels connection records` reads the stream closes and client events that the server stored. */
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { emit, fail_validation, send } from '../../cli/http.ts';
import { numeric_value } from '../../core/http/numeric_json.ts';

const UNIT_MS = { m: 60_000, h: 3_600_000, d: 86_400_000 } as const;
/** An age ("30m") counts back from now. An ISO time must carry its offset, so that 01:50 is exact. */
export function parse_time(inv: Invocation, label: string, raw: string, now: number): number {
  const age = /^(\d+)([mhd])$/.exec(raw);
  if (age) return now - Number(age[1]) * UNIT_MS[age[2] as keyof typeof UNIT_MS];
  const parsed = /(?:Z|[+-]\d\d:\d\d)$/.test(raw) ? Date.parse(raw) : NaN;
  if (Number.isNaN(parsed))
    fail_validation(
      inv,
      `${label} must be an age such as 30m, 24h or 7d, or an ISO time with an offset such as 2026-11-27T01:50:00+01:00.`,
    );
  return parsed;
}
const clock = (at: number) => new Date(at).toISOString().replace('T', ' ');
const n = (value: unknown) => Number(numeric_value(value as never) ?? value);

export async function run_connection_command(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const now = Date.now();
  const params: Record<string, string | number> = { limit: v.limit };
  if (v.since !== null) params.since = parse_time(inv, '--since', v.since, now);
  if (v.until !== null) params.until = parse_time(inv, '--until', v.until, now);
  if (v.device_id !== null) params.device_id = v.device_id;
  const data = await send(inv, 'GET', '/api/connection-records', { params });
  const rows = [
    ...(data.stream_closes as Record<string, any>[]).map((row) => ({
      at: n(row.closed_at),
      text: `${clock(n(row.closed_at))}  stream closed by ${row.closed_by}  ${row.route}  ${row.client_kind}/${row.device_id ?? '-'}  open ${n(row.age_ms)} ms, ${n(row.bytes_written)} bytes, last write ${n(row.last_write_ago_ms)} ms before the close${row.error_code ? `, ${row.error_code}` : ''}`,
    })),
    ...(data.client_events as Record<string, any>[]).map((row) => {
      const { at, received_at, device_id, client_kind, kind, ...rest } = row;
      return {
        at: n(at),
        text: `${clock(n(at))}  client event ${kind}  ${client_kind}/${device_id}  ${JSON.stringify(rest)}`,
      };
    }),
  ].sort((a, b) => a.at - b.at);
  const human = rows.length
    ? rows.map((row) => row.text).join('\n')
    : 'No stream closes or client events in this window.';
  return emit(data, v.as_json, human);
}
