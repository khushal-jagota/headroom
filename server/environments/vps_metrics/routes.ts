/** These routes only read retained evidence. They never construct a host collector. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { HttpError } from '../../core/http/errors.ts';
import { integerQuery, QueryIntegerError, scalarQueries } from '../../list_reads/http.ts';
import { MetricsOperationalError, QueryLimitError, SnapshotExpiredError } from './contracts.ts';
import type { Resolution } from './contracts.ts';
import type { MetricsRuntime } from './runtime.ts';

function issue(type: string, key: string, msg: string, input: unknown, ctx?: unknown): unknown {
  return { type, loc: ['query', key], msg, input, ...(ctx === undefined ? {} : { ctx }) };
}
function integer(
  raw: string | undefined,
  key: string,
  fallback: number | null,
  minimum: number,
  errors: unknown[],
): number | null {
  if (raw === undefined) return fallback;
  try {
    const result = integerQuery(raw, key)!;
    if (result < minimum)
      errors.push(
        issue(
          'greater_than_equal',
          key,
          `Input should be greater than or equal to ${minimum}`,
          raw,
          { ge: minimum },
        ),
      );
    return result;
  } catch (error) {
    if (!(error instanceof QueryIntegerError)) throw error;
    errors.push(error.payload());
    return fallback;
  }
}
function floating(raw: string | undefined, key: string, errors: unknown[]): number {
  if (raw === undefined) {
    errors.push(issue('missing', key, 'Field required', null));
    return NaN;
  }
  const clean = raw.trim();
  if (
    !/^[+-]?(?:(?:\d+(?:_\d+)*(?:\.(?:\d+(?:_\d+)*)?)?|\.\d+(?:_\d+)*)(?:[eE][+-]?\d+(?:_\d+)*)?|inf(?:inity)?|nan)$/i.test(
      clean,
    )
  ) {
    errors.push(
      issue(
        'float_parsing',
        key,
        'Input should be a valid number, unable to parse string as a number',
        raw,
      ),
    );
    return NaN;
  }
  if (/^[+-]?inf(?:inity)?$/i.test(clean)) return clean.startsWith('-') ? -Infinity : Infinity;
  return Number(clean.replaceAll('_', ''));
}
function text(
  raw: string | undefined,
  key: string,
  minimum: number,
  maximum: number,
  required: boolean,
  errors: unknown[],
): string | null {
  if (raw === undefined) {
    if (required) errors.push(issue('missing', key, 'Field required', null));
    return null;
  }
  const length = Array.from(raw).length;
  if (length < minimum)
    errors.push(
      issue(
        'string_too_short',
        key,
        `String should have at least ${minimum} character${minimum === 1 ? '' : 's'}`,
        raw,
        { min_length: minimum },
      ),
    );
  if (length > maximum)
    errors.push(
      issue('string_too_long', key, `String should have at most ${maximum} characters`, raw, {
        max_length: maximum,
      }),
    );
  return raw;
}
async function stored<T>(read: () => Promise<T>): Promise<T> {
  try {
    return await read();
  } catch (error) {
    if (error instanceof SnapshotExpiredError) throw new HttpError(410, error.message);
    if (error instanceof QueryLimitError) throw new HttpError(422, error.message);
    if (error instanceof MetricsOperationalError)
      throw new HttpError(503, 'VPS metrics storage is unavailable');
    throw error;
  }
}
export function create_metrics_routes(
  runtime: MetricsRuntime | null,
): FastifyPluginAsync<AreaServices> {
  function current(): MetricsRuntime {
    if (!runtime) throw new HttpError(503, 'VPS metrics runtime is unavailable');
    return runtime;
  }
  return async (app) => {
    app.get('/vps-metrics/latest', async () => stored(() => current().latest()));
    app.get<{ Querystring: { sequence?: string; offset?: string; limit?: string } }>(
      '/vps-metrics/processes',
      async (request) => {
        const query = scalarQueries(request.query),
          errors: unknown[] = [];
        const sequence = integer(query.sequence, 'sequence', null, 1, errors);
        const offset = integer(query.offset, 'offset', 0, 0, errors)!;
        const limit = integer(query.limit, 'limit', 250, 1, errors)!;
        if (errors.length) throw new HttpError(422, errors);
        const runtime = current();
        if (limit > runtime.config.process_page_rows)
          throw new HttpError(422, 'Process page exceeds configured row limit');
        return stored(() =>
          runtime.read({
            kind: 'processes',
            now: runtime.wall_clock(),
            query: { sequence, offset, limit },
          }),
        );
      },
    );
    app.get<{
      Querystring: {
        signal?: string;
        start?: string;
        end?: string;
        resolution?: string;
        entity?: string;
      };
    }>('/vps-metrics/history', async (request) => {
      const query = scalarQueries(request.query),
        errors: unknown[] = [];
      const signal = text(query.signal, 'signal', 1, 200, true, errors)!;
      const start = floating(query.start, 'start', errors),
        end = floating(query.end, 'end', errors);
      const resolution = query.resolution ?? 'minute';
      if (!['raw', 'minute', 'coarse'].includes(resolution))
        errors.push(
          issue(
            'literal_error',
            'resolution',
            "Input should be 'raw', 'minute' or 'coarse'",
            resolution,
            { expected: "'raw', 'minute' or 'coarse'" },
          ),
        );
      const entity = text(query.entity, 'entity', 0, 500, false, errors);
      if (errors.length) throw new HttpError(422, errors);
      const runtime = current();
      if (
        !Number.isFinite(start) ||
        !Number.isFinite(end) ||
        end <= start ||
        end - start > runtime.config.coarse_seconds
      )
        throw new HttpError(422, 'History range exceeds configured retention or is invalid');
      return stored(() =>
        runtime.read({
          kind: 'history',
          query: { signal, entity, start, end, resolution: resolution as Resolution },
        }),
      );
    });
    app.post('/vps-metrics/activity', async () =>
      stored(async () => ({ active_until: await current().renew_activity() })),
    );
  };
}
