/** Backend HTTP transport depends on Panels interfaces, never provider modules. */
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { requireOwner } from '../../core/authority/identity.ts';
import { HttpError } from '../../core/http/errors.ts';
import { JsonNumber } from '../../core/http/numeric_json.ts';
import { PlannerError } from '../../core/contracts.ts';
import { dictionaryBody, scalarQueries } from '../../list_reads/http.ts';
import { read_ticket } from '../../tickets/data/index.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { Fields, body_fields, object } from '../http/intake.ts';
import type { ConversationHttpWriteScope } from '../http/writes.ts';
import { resolve_usage_model_scopes } from '../backend_usage/logic/scopes.ts';
import type { BackendHttpRuntime, BackendSnapshot } from './contracts.ts';
import type { DriverSignInController, SignInSession } from '../../drivers/core/sign_in.ts';
import {
  SignInReturnUrlRefused,
  SignInUnavailable,
  UnknownSignInSession,
} from '../../drivers/core/sign_in.ts';
import { snapshot_json, sign_in_json } from './presentation.ts';
import type { CardFacts } from './presentation.ts';
/** Home cards: this server's own hooks. Another machine's card: its driver only. */
function card_facts(runtime: BackendHttpRuntime, key: string, home = true): CardFacts {
  return {
    driver: runtime.driver_of(key),
    can_sign_in: home && runtime.sign_in(key) !== null,
    reports_allowance_usage: home && runtime.reports_allowance_usage(key),
  };
}
function home_card(runtime: BackendHttpRuntime, snapshot: BackendSnapshot) {
  return snapshot_json(snapshot, runtime.backend_state, card_facts(runtime, snapshot.backend_key));
}
function backend(runtime: BackendHttpRuntime, raw: unknown): BackendInstanceId {
  const fields = new Fields(raw, ['path']);
  const result = fields.choice('backend_key', runtime.backend_ids);
  fields.finish();
  return result as BackendInstanceId;
}
function boolean(value: unknown, location: string[]): boolean {
  const numeric = value instanceof JsonNumber ? value.value : value;
  if (typeof numeric === 'boolean') return numeric;
  if (numeric === 1 || numeric === 0) return numeric === 1;
  if (typeof numeric === 'string') {
    const lower = numeric.toLowerCase();
    if (['true', 't', '1', 'on', 'yes', 'y'].includes(lower)) return true;
    if (['false', 'f', '0', 'off', 'no', 'n'].includes(lower)) return false;
  }
  const parsing = typeof numeric === 'number' || typeof numeric === 'string';
  throw new HttpError(422, [
    {
      type: parsing ? 'bool_parsing' : 'bool_type',
      loc: location,
      msg: parsing
        ? 'Input should be a valid boolean, unable to interpret input'
        : 'Input should be a valid boolean',
      input: value,
    },
  ]);
}
function refresh(raw: unknown): boolean {
  const values = scalarQueries(object(raw) ? raw : {});
  return values.refresh === undefined ? false : boolean(values.refresh, ['query', 'refresh']);
}
/** The backend's sign-in, with the member the route calls. Otherwise 404. */
function sign_in_service(
  runtime: BackendHttpRuntime,
  raw: unknown,
  member?: 'submit_return_url' | 'sign_out',
): DriverSignInController {
  const key = backend(runtime, raw);
  const service = runtime.sign_in(key);
  if (service === null || (member !== undefined && service[member] === undefined))
    throw new HttpError(404, `${key} cannot be signed in from Panels`);
  return service;
}
async function in_force(operation: () => Promise<SignInSession>): Promise<SignInSession> {
  try {
    return await operation();
  } catch (error) {
    if (error instanceof UnknownSignInSession)
      throw new HttpError(409, 'that sign-in is not the one in force any more');
    if (error instanceof SignInReturnUrlRefused) throw new HttpError(422, error.message);
    throw error;
  }
}
export interface BackendRouteOptions {
  readonly available: boolean;
  readonly writes: ConversationHttpWriteScope;
}
export function createBackendRoutes(
  runtime: BackendHttpRuntime,
  options: BackendRouteOptions,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    // Runtime dependency resolution precedes body/query/path validation,
    // but malformed JSON is rejected by the transport before dependencies run.
    app.addHook('preValidation', async () => {
      if (!options.available) throw new HttpError(503, 'the conversation system is unavailable');
    });
    // Even catalog GETs can start external probes and mutate cached observations.
    // Own every handler through completion under the same shutdown seal.
    app.addHook('onRoute', (route) => {
      const handler = route.handler;
      route.handler = async function (request, reply) {
        return options.writes.run(() => handler.call(this, request, reply));
      };
    });
    const requests = new WeakMap<FastifyRequest, { signal: AbortSignal; release(): void }>();
    app.addHook('onRequest', async (request, reply) => {
      const controller = new AbortController();
      const abort = () => controller.abort(new Error('Backend request cancelled'));
      const closed = () => {
        if (!reply.raw.writableFinished) abort();
      };
      request.raw.once('aborted', abort);
      reply.raw.once('close', closed);
      requests.set(request, {
        signal: controller.signal,
        release() {
          request.raw.off('aborted', abort);
          reply.raw.off('close', closed);
        },
      });
    });
    app.addHook('onResponse', async (request) => requests.get(request)?.release());
    const signal_of = (request: FastifyRequest) => requests.get(request)!.signal;
    app.get('/backends', async (request) => {
      const snapshots = await runtime.backend_snapshots.snapshots({
        refresh: refresh(request.query),
        signal: signal_of(request),
      });
      return {
        backends: snapshots.map((snapshot) => home_card(runtime, snapshot)),
      };
    });
    app.post('/backends', async (request, reply) => {
      // Context validates identity only. This route has no owner-only rule.
      services.identity(request);
      const refresh_requested = refresh(request.query);
      if (!dictionaryBody(request.body, reply)) return;
      const raw = request.body;
      if (
        Object.keys(raw).length !== 1 ||
        typeof raw.ticket_id !== 'string' ||
        !raw.ticket_id.trim()
      )
        throw new PlannerError('validation', 'ticket_id is required', {});
      const ticket = runtime.database.read((sql) => read_ticket(sql, raw.ticket_id as string));
      const source =
        ticket.machine_id === HOME_MACHINE_ID
          ? runtime.backend_snapshots
          : runtime.machine_catalog(ticket.machine_id);
      // A machine with no executor here (never paired, or its pairing withdrawn) is a state
      // of that machine, not a malformed request. It answers in the transport's `detail`
      // sentence, which the composer shows as written; the planner envelope it used to
      // answer with reached the person as a bare "The server answered 400."
      if (source === null)
        throw new HttpError(409, 'This machine is not connected to Panels, so its models cannot be listed.');
      const snapshots = await Promise.all(
        runtime.backend_ids.map((key) =>
          source.snapshot(key, { refresh: refresh_requested, signal: signal_of(request) }),
        ),
      );
      return {
        backends: snapshots.map((snapshot) =>
          snapshot_json(
            snapshot,
            runtime.backend_state,
            card_facts(runtime, snapshot.backend_key, ticket.machine_id === HOME_MACHINE_ID),
          ),
        ),
      };
    });
    app.post('/backends/refresh', async (request) => {
      const controller = new AbortController();
      const signal = AbortSignal.any([controller.signal, signal_of(request)]);
      const usage_refresh = Promise.all(
        runtime.backend_ids.map((key) => runtime.backend_usage.refresh(key, { signal })),
      );
      let snapshots;
      let refreshed;
      try {
        await Promise.resolve();
        snapshots = await runtime.backend_snapshots.snapshots({ refresh: true, signal });
        refreshed = await usage_refresh;
      } finally {
        controller.abort();
        await usage_refresh.catch(() => {});
      }
      const by_backend = new Map(snapshots.map((snapshot) => [snapshot.backend_key, snapshot]));
      const resolved = refreshed.map((result) =>
        resolve_usage_model_scopes(result, by_backend.get(result.backend_key)!),
      );
      if (runtime.backend_state !== null) {
        for (const result of resolved) {
          if (result.outcome === 'succeeded' && result.observed_at !== null)
            runtime.backend_state.keep_successful_usage(result);
        }
      }
      return {
        backends: snapshots.map((snapshot) => home_card(runtime, snapshot)),
        usage_outcomes: resolved.map((result) => ({
          backend_key: result.backend_key,
          outcome: result.outcome,
          detail: result.detail,
        })),
      };
    });
    app.put<{ Params: { backend_key: string; '*': string } }>(
      '/backends/:backend_key/models/*',
      async (request) => {
        const key = backend(runtime, request.params);
        const path = request.params['*'];
        if (!path.endsWith('/enablement')) throw new HttpError(404, 'Not Found');
        const model_id = path.slice(0, -'/enablement'.length);
        const fields = body_fields(request.body);
        if (!('enabled' in fields.body)) {
          fields.issue('missing', ['enabled'], 'Field required', fields.body);
          fields.finish();
        }
        const enabled = boolean(fields.body.enabled, ['body', 'enabled']);
        if (!model_id || model_id !== model_id.trim())
          throw new HttpError(422, 'model_id must be trimmed text');
        const snapshot = await runtime.backend_snapshots.snapshot(key, {
          signal: signal_of(request),
        });
        if (!snapshot.available_models.some((model) => model.model_id === model_id))
          throw new HttpError(404, 'model is not in the backend catalogue');
        if (runtime.backend_state === null)
          throw new HttpError(503, 'backend state is unavailable');
        runtime.backend_state.write_model_enablement(key, model_id, enabled);
        return { backend_key: key, model_id, enabled };
      },
    );
    app.post('/backends/:backend_key/update', async (request) => {
      const key = backend(runtime, request.params);
      requireOwner(services.identity(request));
      return runtime.backend_snapshots.update_backend(key, { signal: signal_of(request) });
    });
    app.post('/backends/:backend_key/sign-in', async (request) => {
      const service = sign_in_service(runtime, request.params);
      try {
        return sign_in_json(await service.start());
      } catch (error) {
        if (error instanceof SignInUnavailable) throw new HttpError(503, error.message);
        throw error;
      }
    });
    app.get('/backends/:backend_key/sign-in', async (request) => {
      const pending = sign_in_service(runtime, request.params).read();
      return pending === null ? { sign_in: null } : sign_in_json(pending);
    });
    app.delete('/backends/:backend_key/sign-in', async (request) => {
      try {
        await sign_in_service(runtime, request.params, 'sign_out').sign_out!();
      } catch (error) {
        if (error instanceof SignInUnavailable) throw new HttpError(503, error.message);
        throw error;
      }
      return { sign_in: null };
    });
    app.delete<{ Params: { backend_key: string; session_id: string } }>(
      '/backends/:backend_key/sign-in/:session_id',
      async (request) => {
        const service = sign_in_service(runtime, request.params);
        return sign_in_json(await in_force(() => service.cancel(request.params.session_id)));
      },
    );
    app.post<{ Params: { backend_key: string; session_id: string } }>(
      '/backends/:backend_key/sign-in/:session_id/return-url',
      async (request) => {
        const service = sign_in_service(runtime, request.params, 'submit_return_url');
        const fields = body_fields(request.body);
        const return_url = fields.text('return_url');
        fields.finish();
        return sign_in_json(
          await in_force(() => service.submit_return_url!(request.params.session_id, return_url!)),
        );
      },
    );
  };
}
