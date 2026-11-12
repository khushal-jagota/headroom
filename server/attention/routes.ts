import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { integerQuery, QueryIntegerError, scalarQueries } from '../list_reads/http.ts';
import { calendar_date } from '../measures/logic/dates.ts';
import { read_summary } from './actions.ts';
import { DEFAULT_LIMIT } from './configuration.ts';
import { summary_request } from './contracts.ts';
import type { GroupBy, ScopeKind } from './contracts.ts';
interface QueryFailure {
  type: string;
  loc: string[];
  msg: string;
  input: string;
  ctx?: { error?: string; expected?: string };
}
function date_query(raw: string, name: string): string | QueryFailure {
  const fail = (error: string): QueryFailure => ({
    type: 'date_from_datetime_parsing',
    loc: ['query', name],
    msg: 'Input should be a valid date or datetime, ' + error,
    input: raw,
    ctx: { error },
  });
  const inexact = (): QueryFailure => ({
    type: 'date_from_datetime_inexact',
    loc: ['query', name],
    msg: 'Datetimes provided to dates should have zero time - e.g. be exact dates',
    input: raw,
  });
  if (/^[+-]?\d+(?:\.\d+)?$/.test(raw)) {
    let seconds = Number(raw);
    if (Math.abs(seconds) > 20000000000) seconds /= 1000;
    const instant = new Date(seconds * 1000);
    if (
      Number.isFinite(instant.getTime()) &&
      instant.getUTCFullYear() >= 1 &&
      instant.getUTCFullYear() <= 9999
    ) {
      if (
        instant.getUTCHours() ||
        instant.getUTCMinutes() ||
        instant.getUTCSeconds() ||
        instant.getUTCMilliseconds()
      )
        return inexact();
      return instant.toISOString().slice(0, 10);
    }
  }
  if (raw.length < 10) return fail('input is too short');
  if (!/^\d{4}/.test(raw)) return fail('invalid character in year');
  if (raw[4] !== '-') return fail('invalid date separator, expected `-`');
  if (!/^\d{2}$/.test(raw.slice(5, 7))) return fail('invalid character in month');
  if (raw[7] !== '-') return fail('invalid date separator, expected `-`');
  if (!/^\d{2}$/.test(raw.slice(8, 10))) return fail('invalid character in day');
  if (Number(raw.slice(0, 4)) === 0)
    return {
      type: 'date_parsing',
      loc: ['query', name],
      msg: 'Input should be a valid date in the format YYYY-MM-DD, year 0 is out of range',
      input: raw,
      ctx: { error: 'year 0 is out of range' },
    };
  const month = Number(raw.slice(5, 7));
  if (month < 1 || month > 12) return fail('month value is outside expected range of 1-12');
  const date = raw.slice(0, 10);
  try {
    calendar_date(date);
  } catch {
    return fail('day value is outside expected range');
  }
  if (raw.length === 10) return date;
  const datetime =
    /^[Tt_ ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?([Zz]|[+-]\d{2}:?\d{2})?$/.exec(
      raw.slice(10),
    );
  if (!datetime) {
    if (/[+-]\d{2}:?$/.test(raw.slice(10))) return fail('invalid timezone minute');
    return fail('unexpected extra characters at the end of the input');
  }
  if (Number(datetime[1]) > 23) return fail('hour value is outside expected range of 0-23');
  if (Number(datetime[2]) > 59) return fail('minute value is outside expected range of 0-59');
  if (Number(datetime[3] ?? 0) > 59) return fail('second value is outside expected range of 0-59');
  const offset = datetime[5];
  if (offset !== undefined && /^[+-]/.test(offset)) {
    const digits = offset.slice(1).replace(':', '');
    if (Number(digits.slice(2)) > 59)
      return fail('timezone minute value is outside expected range of 0-59');
    if (Number(digits.slice(0, 2)) >= 24) return fail('timezone offset must be less than 24 hours');
  }
  if (
    Number(datetime[1]) ||
    Number(datetime[2]) ||
    Number(datetime[3] ?? 0) ||
    Number(datetime[4]?.slice(0, 6) ?? 0)
  )
    return inexact();
  return date;
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.get('/attention/summary', async (request, reply) => {
    const q = scalarQueries(request.query as Record<string, string | undefined>);
    const errors: unknown[] = [];
    let first: string | null = null,
      last: string | null = null;
    for (const name of ['first_date', 'last_date'] as const)
      if (q[name] !== undefined) {
        const value = date_query(q[name]!, name);
        if (typeof value !== 'string') errors.push(value);
        else if (name === 'first_date') first = value;
        else last = value;
      }
    const group = q.group_by ?? 'project',
      scope = q.scope_kind ?? 'all';
    for (const [name, value, choices] of [
      ['group_by', group, ['project', 'manager', 'ticket', 'goal']],
      ['scope_kind', scope, ['all', 'project', 'manager', 'ticket', 'goal', 'chief']],
    ] as const)
      if (!(choices as readonly string[]).includes(value)) {
        const quoted = choices.map((item) => `'${item}'`);
        const expected = quoted.slice(0, -1).join(', ') + ' or ' + quoted.at(-1);
        errors.push({
          type: 'enum',
          loc: ['query', name],
          msg: 'Input should be ' + expected,
          input: value,
          ctx: { expected },
        });
      }
    let limit = DEFAULT_LIMIT,
      offset = 0;
    for (const name of ['limit', 'offset'] as const)
      try {
        const value = integerQuery(q[name], name);
        if (value !== undefined) {
          if (name === 'limit') limit = value;
          else offset = value;
        }
      } catch (error) {
        if (error instanceof QueryIntegerError) errors.push(error.payload());
        else throw error;
      }
    if (errors.length) return reply.code(422).send({ detail: errors });
    const input = summary_request({
      first_date: first,
      last_date: last,
      group_by: group as GroupBy,
      scope: { kind: scope as ScopeKind, id: q.scope_id ?? null },
      sort: q.sort ?? 'owner_messages',
      limit,
      offset,
    });
    return services.database.read((sql) => read_summary(sql, input, services.clock.nowUnix()));
  });
};
