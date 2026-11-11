import { randomUUID } from 'node:crypto';
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { requiredQuery, scalarQueries } from '../list_reads/http.ts';
import type { ResourceProvider } from './contracts.ts';
import { checkedKey, parseKeys } from './logic/index.ts';
import { capture, providers } from './registry.ts';
export function createRoutes(providers: readonly ResourceProvider[]): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    const epoch = randomUUID();
    app.get<{ Querystring: { keys?: string } }>('/resource-versions', async (request, reply) => {
      const query = scalarQueries(request.query);
      if (!requiredQuery(query.keys, 'keys', reply)) return;
      const keys = parseKeys(query.keys);
      const resources = services.database.read(sql => keys.map(key => {
        const snapshot = capture(providers, sql, request, checkedKey(key));
        return { key, revision: snapshot?.revision ?? null };
      }));
      return { epoch, resources };
    });
    app.get<{ Querystring: { key?: string } }>('/resource-body', async (request, reply) => {
      const query = scalarQueries(request.query);
      if (!requiredQuery(query.key, 'key', reply)) return;
      const key = checkedKey(query.key);
      return services.database.read(sql => {
        const snapshot = capture(providers, sql, request, key);
        if (snapshot === null) throw new PlannerError('not_found', 'unregistered resource', { key });
        return { epoch, key, revision: snapshot.revision, body: snapshot.read() };
      });
    });
  };
}
export const routes = createRoutes(providers);
