/** The history picker needs indexed metadata, not a running conversation core. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { PlannerError } from '../../core/contracts.ts';
import { HttpError } from '../../core/http/errors.ts';
import { integerQuery, QueryIntegerError, scalarQueries } from '../../list_reads/http.ts';
import { PAGE_SIZE } from '../../files/catalog/config.ts';
import { read_history_page } from './read.ts';
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.get<{
    Params: { kind: string; owner_id: string };
    Querystring: Record<string, string | undefined>;
  }>('/conversation-history/:kind/:owner_id', async (request) => {
    const query = scalarQueries(request.query);
    const values = { offset: 0, limit: PAGE_SIZE, after: -1 };
    const issues: ReturnType<QueryIntegerError['payload']>[] = [];
    // FastAPI validates scalar queries before entering the kind-selection handler.
    for (const key of ['offset', 'limit', 'after'] as const) {
      try {
        values[key] = integerQuery(query[key], key) ?? values[key];
      } catch (error) {
        if (!(error instanceof QueryIntegerError)) throw error;
        issues.push(error.payload());
      }
    }
    if (issues.length !== 0) throw new HttpError(422, issues);
    const { kind, owner_id } = request.params;
    if (kind !== 'ticket' && kind !== 'manager')
      throw new PlannerError('validation', 'unknown conversation history owner kind');
    return services.database.read((sql) =>
      read_history_page(sql, {
        kind,
        owner_id,
        ...values,
        selected: query.selected ?? null,
        prefix: query.prefix ?? null,
        locale: query.locale ?? 'en-US',
        timezone: query.timezone ?? 'UTC',
      }),
    );
  });
};
