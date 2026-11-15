/** Numeric intake is scoped to the durable Review command contract. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { install_numeric_json_parser } from '../core/http/numeric_intake.ts';
import { dictionaryBody } from '../list_reads/http.ts';
import * as operations from './decision_operations.ts';

export const decision_routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  install_numeric_json_parser(app);
  const path = '/tickets/:ticket_id/decision-operations/:operation_id';
  app.get<{ Params: { ticket_id: string; operation_id: string } }>(path, async (request) =>
    services.database.read((sql) =>
      operations.lookup(
        sql,
        request.params.ticket_id,
        request.params.operation_id,
        services.identity(request).principal,
      ),
    ),
  );
  app.post<{ Params: { ticket_id: string; operation_id: string } }>(
    path,
    async (request, reply) => {
      if (!dictionaryBody(request.body, reply)) return;
      return services.database.write((tx) =>
        operations.apply(
          tx,
          request.params.ticket_id,
          request.params.operation_id,
          request.body as Record<string, unknown>,
          services.identity(request).principal,
          services.clock.nowUnix(),
        ),
      );
    },
  );
};
