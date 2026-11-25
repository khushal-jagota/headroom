/** Owner update actions use the same truthful principal as the other areas. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { requireOwner } from '../../core/authority/identity.ts';
import { HttpError } from '../../core/http/errors.ts';
import { list_client_versions } from '../../client_versions/data.ts';
import { UpdateConflict } from './attempts.ts';
import { is_object } from './codec.ts';
import type { UpdateAttempt, UpdateAttemptView, UpdateInterruption } from './contracts.ts';
import type { UpdateService } from './service.ts';

export interface UpdateMacReads {
  health_payload(): Record<string, unknown> | null;
  status(): Promise<unknown | null>;
}
export function attempt_body(attempt: UpdateAttempt): UpdateAttemptView {
  return {
    interruption: (attempt.extra.interruption as UpdateInterruption | undefined) ?? null,
    attempt_id: attempt.attempt_id,
    from_sha: attempt.from_sha,
    to_sha: attempt.to_sha,
    mode: attempt.mode,
    state: attempt.state,
    ...(attempt.state === 'done' && { completed_at: attempt.steps.done ?? null }),
    detail: attempt.detail,
    deadline_at: attempt.deadline_at,
  };
}
function body(raw: unknown): Record<string, unknown> {
  if (is_object(raw)) return raw;
  throw new HttpError(422, [
    {
      type: raw == null ? 'missing' : 'model_attributes_type',
      loc: ['body'],
      msg:
        raw == null
          ? 'Field required'
          : 'Input should be a valid dictionary or object to extract fields from',
      input: raw ?? null,
    },
  ]);
}
/** Only the release is read. An earlier page also sends a mode and a confirmation. */
function install_body(value: unknown): { app_sha: string } {
  const raw = body(value);
  if (!('app_sha' in raw))
    throw new HttpError(422, [
      { type: 'missing', loc: ['body', 'app_sha'], msg: 'Field required', input: raw },
    ]);
  if (typeof raw.app_sha !== 'string')
    throw new HttpError(422, [
      {
        type: 'string_type',
        loc: ['body', 'app_sha'],
        msg: 'Input should be a valid string',
        input: raw.app_sha,
      },
    ]);
  return { app_sha: raw.app_sha };
}
export function create_update_routes(
  service: UpdateService,
  mac: UpdateMacReads,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.get('/updates', async (request) => {
      requireOwner(services.identity(request));
      const result = service.status();
      const health = mac.health_payload();
      const mac_details = health === null ? null : { ...health, update: await mac.status() };
      return {
        ...result,
        client_versions: services.database.read(list_client_versions),
        allowed_actions: [...result.allowed_actions],
        available:
          result.available === null
            ? null
            : { app_sha: result.available.manifest.app_sha, ...result.available },
        attempt: result.attempt === null ? null : attempt_body(result.attempt),
        app_sha: result.app_sha ?? '',
        mac_details,
      };
    });
    const act = (action: () => UpdateAttempt | void) => {
      try {
        const attempt = action();
        return { attempt: attempt === undefined ? null : attempt_body(attempt) };
      } catch (error) {
        if (error instanceof UpdateConflict) {
          // The browser is not the only reader of a refusal.
          console.warn(`Update action refused: ${error.message}`);
          throw new HttpError(409, error.message);
        }
        throw error;
      }
    };
    app.post('/updates/install', async (request) => {
      const value = install_body(request.body);
      requireOwner(services.identity(request));
      return act(() => service.install(value.app_sha));
    });
    app.post<{ Params: { attempt_id: string } }>(
      '/updates/attempts/:attempt_id/retry',
      async (request) => {
        requireOwner(services.identity(request));
        return act(() => service.retry(request.params.attempt_id));
      },
    );
    app.post<{ Params: { attempt_id: string; machine_id: string } }>(
      '/updates/attempts/:attempt_id/machines/:machine_id/retry',
      async (request) => {
        requireOwner(services.identity(request));
        try {
          const result = service.retry_machine(
            request.params.machine_id,
            request.params.attempt_id,
          );
          return { attempt: attempt_body(result.attempt), machine: result.machine };
        } catch (error) {
          if (error instanceof UpdateConflict) throw new HttpError(409, error.message);
          throw error;
        }
      },
    );
    app.post<{ Params: { attempt_id: string } }>(
      '/updates/attempts/:attempt_id/retry-mac',
      async (request) => {
        requireOwner(services.identity(request));
        return act(() => service.retry_mac(request.params.attempt_id));
      },
    );
  };
}
