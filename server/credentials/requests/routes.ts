/** Employee metadata requests and the private owner-only credential entry path. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { requireOwner } from '../../core/authority/identity.ts';
import { PlannerError } from '../../core/contracts.ts';
import { HttpError } from '../../core/http/errors.ts';
import { private_body, read_private_body } from '../routes.ts';
import * as actions from './actions.ts';
import * as data from './data.ts';
import type { CredentialRequestOperations } from './service.ts';

export function createCredentialRequestRoutes(
  operations: CredentialRequestOperations | null,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    // Private parsing never reflects plaintext or malformed secret input in an error.
    // Reject/review/retry ignore their body, including malformed JSON, like Python.
    app.removeAllContentTypeParsers();
    app.addContentTypeParser('*', { parseAs: 'buffer' }, (_request, raw, done) => {
      done(null, raw);
    });
    const active = (): CredentialRequestOperations => {
      if (operations === null)
        throw new HttpError(503, 'the conversation system is unavailable');
      return operations;
    };
    const origin_for = (current: CredentialRequestOperations, request_id: string): string =>
      current.runtime.database.read((sql) => data.row_for(sql, request_id).conversation_id);

    app.post('/credentials/requests', async (request) => {
      const current = active();
      const ctx = services.identity(request);
      const raw = read_private_body(request.body);
      const origin = ctx.conversationId;
      if (origin === null)
        throw new PlannerError(
          'agent_forbidden',
          'credential requests require the employee conversation identity',
        );
      const { database, core, key_path } = current.runtime;
      database.read((sql) => actions.origin_conversation(sql, ctx));
      return await current.shield_operation(async () => {
        const [body, attempt] = await core.record_events_transaction(origin, (tx) =>
          actions.create(tx, raw, { ctx, now: current.runtime.now(), key_path }),
        );
        return await current.dispatch(body.request_id, attempt);
      });
    });
    app.get<{ Params: { request_id: string } }>(
      '/credentials/requests/:request_id',
      async (request) => services.database.read((sql) => data.read(sql, request.params.request_id)),
    );
    app.post<{ Params: { request_id: string } }>(
      '/credentials/requests/:request_id/save',
      async (request) => {
        const current = active();
        const ctx = services.identity(request);
        requireOwner(ctx);
        const raw = private_body(request.body, ['name', 'value', 'expected_revision']);
        const request_id = request.params.request_id;
        const origin = origin_for(current, request_id);
        return await current.shield_operation(async () => {
          const [, attempt] = await current.runtime.core.record_events_transaction(origin, (tx) =>
            actions.save(tx, request_id, {
              name: raw.name,
              value: raw.value,
              expected_revision: raw.expected_revision,
              principal: ctx.principal,
              now: current.runtime.now(),
              key_path: current.runtime.key_path,
            }),
          );
          return await current.dispatch(request_id, attempt);
        });
      },
    );
    app.post<{ Params: { request_id: string } }>(
      '/credentials/requests/:request_id/reject',
      async (request) => {
        const current = active();
        const ctx = services.identity(request);
        requireOwner(ctx);
        const request_id = request.params.request_id;
        const origin = origin_for(current, request_id);
        return await current.shield_operation(async () => {
          const [, attempt] = await current.runtime.core.record_events_transaction(origin, (tx) =>
            actions.reject(tx, request_id, { principal: ctx.principal, now: current.runtime.now() }),
          );
          return await current.dispatch(request_id, attempt);
        });
      },
    );
    app.post<{ Params: { request_id: string } }>(
      '/credentials/requests/:request_id/review',
      async (request) => {
        const current = active();
        const ctx = services.identity(request);
        requireOwner(ctx);
        const request_id = request.params.request_id;
        const origin = origin_for(current, request_id);
        return await current.shield_operation(async () =>
          current.runtime.core.record_events_transaction(origin, (tx) =>
            actions.review(tx, request_id, { principal: ctx.principal, now: current.runtime.now() }),
          ),
        );
      },
    );
    app.post<{ Params: { request_id: string } }>(
      '/credentials/requests/:request_id/retry-delivery',
      async (request) => {
        const current = active();
        const ctx = services.identity(request);
        requireOwner(ctx);
        const request_id = request.params.request_id;
        const origin = origin_for(current, request_id);
        return await current.shield_operation(async () => {
          const [, attempt] = await current.runtime.core.record_events_transaction(origin, (tx) =>
            actions.retry(tx, request_id, { principal: ctx.principal, now: current.runtime.now() }),
          );
          return await current.dispatch(request_id, attempt);
        });
      },
    );
  };
}
