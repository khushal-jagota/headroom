import { selected_machine } from '../snapshot/routes.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
/** Thin HTTP transport for local recorded usage and persisted provider allowances. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { HttpError } from '../../core/http/errors.ts';
import { scalarQueries } from '../../list_reads/http.ts';
import { calendar_date } from '../../measures/logic/dates.ts';
import { timezone } from './timeZone.ts';
import { UsageReadError, UsageServiceStopped } from './contracts.ts';
import type { UsageHttpRuntime, UsageSummaryInput } from './contracts.ts';
import { presentProviderLimits, type MachineUsageLimits } from './providerLimits.ts';
import { datetime_json } from '../snapshot/presentation.ts';
import { list_machines } from '../../machines/data.ts';
import { is_record as object } from '../../core/records.ts';

interface QueryIssue {
  type: string;
  loc: string[];
  msg: string;
  input: unknown;
  ctx?: Record<string, unknown>;
}
function summary_input(raw: Record<string, unknown>): UsageSummaryInput {
  const query = scalarQueries(raw);
  const issues: QueryIssue[] = [];
  const dayPattern = '^\\d{4}-\\d{2}-\\d{2}$';
  for (const name of ['sinceDay', 'untilDay']) {
    const value = query[name];
    if (value === undefined)
      issues.push({ type: 'missing', loc: ['query', name], msg: 'Field required', input: null });
    else if (
      typeof value !== 'string' ||
      /^[\p{Decimal_Number}]{4}-[\p{Decimal_Number}]{2}-[\p{Decimal_Number}]{2}$/u.exec(
        value,
      )?.[0] !== value
    )
      issues.push({
        type: 'string_pattern_mismatch',
        loc: ['query', name],
        msg: `String should match pattern '${dayPattern}'`,
        input: value,
        ctx: { pattern: dayPattern },
      });
  }
  const timeZone = query.timeZone === undefined ? 'UTC' : String(query.timeZone);
  const length = Array.from(timeZone).length;
  if (length < 1 || length > 128) {
    const short = length < 1;
    issues.push({
      type: short ? 'string_too_short' : 'string_too_long',
      loc: ['query', 'timeZone'],
      msg: short
        ? 'String should have at least 1 character'
        : 'String should have at most 128 characters',
      input: timeZone,
      ctx: short ? { min_length: 1 } : { max_length: 128 },
    });
  }
  const resolution = query.resolution === undefined ? 'day' : String(query.resolution);
  if (!['day', 'hour'].includes(resolution))
    issues.push({
      type: 'string_pattern_mismatch',
      loc: ['query', 'resolution'],
      msg: "String should match pattern '^(day|hour)$'",
      input: resolution,
      ctx: { pattern: '^(day|hour)$' },
    });
  if (issues.length !== 0) throw new HttpError(422, issues);
  try {
    timezone(timeZone);
    for (const name of ['sinceDay', 'untilDay']) {
      const value = query[name] as string;
      // Years accept Unicode decimal digits. Two-digit months and leading day
      // alternatives retain the route's ASCII ranges.
      if (
        !/^\p{Decimal_Number}{4}-(?:0[1-9]|1[0-2])-(?:[0-2]\p{Decimal_Number}|3[01])$/u.test(value)
      )
        throw new Error('Invalid calendar date');
      const normalized = value.replace(/\p{Decimal_Number}/gu, (character) => {
        const point = character.codePointAt(0)!;
        let first = point;
        while (first > 0 && /\p{Decimal_Number}/u.test(String.fromCodePoint(first - 1))) first--;
        return String((point - first) % 10);
      });
      calendar_date(normalized);
    }
  } catch {
    throw new HttpError(422, 'Invalid usage date or time zone');
  }
  return {
    sinceDay: query.sinceDay,
    untilDay: query.untilDay,
    timeZone,
    resolution,
    ...(query.sinceTime === undefined ? {} : { sinceTime: query.sinceTime }),
    ...(query.untilTime === undefined ? {} : { untilTime: query.untilTime }),
  } as UsageSummaryInput;
}

export function createUsageRoutes(runtime: UsageHttpRuntime): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.get('/summary', async (request) => {
      const input = summary_input(request.query as Record<string, unknown>);
      const machine_id = selected_machine({ database: services.database }, request.query);
      if (machine_id !== HOME_MACHINE_ID) {
        // A joined machine scans its own transcripts and returns only its aggregated buckets.
        const controls = runtime.machine_backends?.(machine_id) ?? null;
        if (controls === null) throw new HttpError(503, 'The selected machine is unavailable.');
        return await controls.request({
          backend_key: '',
          operation: 'read_usage_summary',
          usage_summary_input: { ...input },
        });
      }
      try {
        return await runtime.recorded_usage.readSummary(input);
      } catch (error) {
        if (error instanceof UsageServiceStopped) throw new HttpError(503, error.message);
        if (error instanceof UsageReadError || error instanceof RangeError)
          throw new HttpError(422, error.message);
        throw error;
      }
    });
    app.get('/limits', async (request) => {
      const every_machine =
        scalarQueries(object(request.query) ? request.query : {}).machine_id === undefined;
      if (!runtime.conversation_runtime_available)
        throw new HttpError(503, 'the conversation system is unavailable');
      if (every_machine) {
        // Every machine's last observation, an unavailable machine's included.
        const readings: MachineUsageLimits[] = [];
        const failures: Record<string, unknown>[] = [];
        const state = runtime.backend_state;
        if (state === null) return { readings, failures };
        for (const machine of services.database.read((sql) => list_machines(sql)))
          for (const backend_key of runtime.backend_ids) {
            const failure = state.read_failed_usage(backend_key, machine.machine_id);
            if (failure !== null)
              failures.push({
                machine_id: machine.machine_id,
                machine_name: machine.name,
                backend_key,
                outcome: failure.outcome,
                detail: failure.detail,
                failed_at: datetime_json(failure.failed_at),
              });
            const observation = state.read_usage(backend_key, machine.machine_id);
            if (observation === null) continue;
            readings.push({
              machine_id: machine.machine_id,
              machine_name: machine.name,
              backend_key,
              account_label: observation.account_label,
              account_detail: observation.account_detail,
              limits: presentProviderLimits(observation),
            });
          }
        return { readings, failures };
      }
      const machine_id = selected_machine({ database: services.database }, request.query);
      if (runtime.backend_state === null) return {};
      const result: Record<string, ReturnType<typeof presentProviderLimits>> = {};
      for (const backend of runtime.backend_ids) {
        const observation = runtime.backend_state.read_usage(backend, machine_id);
        if (observation !== null) result[backend] = presentProviderLimits(observation);
      }
      return result;
    });
    await app.register(async (scope) => {
      // This route ignores the request body, including malformed JSON.
      scope.removeAllContentTypeParsers();
      scope.addContentTypeParser('*', { parseAs: 'buffer' }, (_request, body, done) =>
        done(null, body),
      );
      scope.post('/rates/refresh', async () => {
        try {
          return await runtime.recorded_usage.refreshRates();
        } catch (error) {
          if (error instanceof UsageServiceStopped) throw new HttpError(503, error.message);
          throw error;
        }
      });
    });
  };
}
