/** Backend HTTP transport depends on Panels interfaces, never provider modules. */
import type { FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { requireOwner } from '../../core/authority/identity.ts';
import { HttpError } from '../../core/http/errors.ts';
import { JsonNumber } from '../../core/http/numeric_json.ts';
import { PlannerError } from '../../core/contracts.ts';
import { dictionaryBody, scalarQueries } from '../../list_reads/http.ts';
import { read_ticket } from '../../tickets/data/index.ts';
import { ConversationBackendKey } from '../contracts.ts';
import { Fields, body_fields, object } from '../http/intake.ts';
import { resolve_usage_model_scopes } from '../backend_state.ts';
import type { BackendHttpRuntime } from './contracts.ts';
import type { CodexSignInService } from './provider_contracts.ts';
import { CodexSignInUnavailable, UnknownSignInSession } from './provider_contracts.ts';
import { snapshot_json, sign_in_json } from './presentation.ts';
function backend(raw: unknown): ConversationBackendKey {
  const fields = new Fields(raw, ['path']);
  const result = fields.choice('backend_key', Object.values(ConversationBackendKey));
  fields.finish();
  return result as ConversationBackendKey;
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
function sign_in_service(
  runtime: BackendHttpRuntime,
  key: ConversationBackendKey,
): CodexSignInService {
  if (key !== 'codex' || runtime.codex_sign_in === null)
    throw new HttpError(404, `${key} cannot be signed in from Panels`);
  return runtime.codex_sign_in;
}
export function createBackendRoutes(runtime: BackendHttpRuntime): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
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
        backends: snapshots.map((snapshot) => snapshot_json(snapshot, runtime.backend_state)),
      };
    });
    app.post('/backends', async (request, reply) => {
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
        ticket.locale === 'macos' ? runtime.machine_catalog() : runtime.backend_snapshots;
      if (source === null)
        throw new PlannerError('validation', 'The selected machine is unavailable', {});
      const snapshots = await Promise.all(
        Object.values(ConversationBackendKey).map((key) =>
          source.snapshot(key, { refresh: refresh_requested, signal: signal_of(request) }),
        ),
      );
      return {
        backends: snapshots.map((snapshot) => snapshot_json(snapshot, runtime.backend_state)),
      };
    });
    app.post('/backends/refresh', async (request) => {
      const controller = new AbortController();
      const signal = AbortSignal.any([controller.signal, signal_of(request)]);
      const usage_refresh = Promise.all(
        Object.values(ConversationBackendKey).map((key) =>
          runtime.backend_usage.refresh(key, { signal }),
        ),
      );
      let snapshots;
      let refreshed;
      try {
        await Promise.resolve();
        snapshots = await runtime.backend_snapshots.snapshots({ signal });
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
        backends: snapshots.map((snapshot) => snapshot_json(snapshot, runtime.backend_state)),
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
        const key = backend(request.params);
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
      const key = backend(request.params);
      requireOwner(services.identity(request));
      return runtime.backend_snapshots.update_backend(key, { signal: signal_of(request) });
    });
    app.post('/backends/:backend_key/sign-in', async (request) => {
      const service = sign_in_service(runtime, backend(request.params));
      try {
        return sign_in_json(await service.start());
      } catch (error) {
        if (error instanceof CodexSignInUnavailable) throw new HttpError(503, error.message);
        throw error;
      }
    });
    app.get('/backends/:backend_key/sign-in', async (request) => {
      const pending = sign_in_service(runtime, backend(request.params)).read();
      return pending === null ? { sign_in: null } : sign_in_json(pending);
    });
    app.delete<{ Params: { backend_key: string; session_id: string } }>(
      '/backends/:backend_key/sign-in/:session_id',
      async (request) => {
        const service = sign_in_service(runtime, backend(request.params));
        try {
          return sign_in_json(await service.cancel(request.params.session_id));
        } catch (error) {
          if (error instanceof UnknownSignInSession)
            throw new HttpError(409, 'that sign-in is not the one in force any more');
          throw error;
        }
      },
    );
  };
}
