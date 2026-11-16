/** Owner update actions use the same truthful principal as the other areas. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../../core/authority/register.ts';
import { requireOwner } from '../../core/authority/identity.ts';
import { HttpError } from '../../core/http/errors.ts';
import { numeric_value } from '../../core/http/numeric_json.ts';
import { UpdateConflict } from './attempts.ts';
import { is_object } from './codec.ts';
import type { UpdateAttempt, UpdateMode } from './contracts.ts';
import type { UpdateService } from './service.ts';

export interface UpdateMacReads {
  health_payload(): Record<string, unknown> | null;
  status(): Promise<unknown | null>;
}
export function attempt_body(attempt: UpdateAttempt): Record<string, unknown> {
  return {
    attempt_id: attempt.attempt_id,
    from_sha: attempt.from_sha,
    to_sha: attempt.to_sha,
    mode: attempt.mode,
    state: attempt.state,
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
function confirmation(raw: Record<string, unknown>): boolean {
  if (!('confirm_activity' in raw)) return false;
  const value = raw.confirm_activity,
    numeric = numeric_value(value);
  if (typeof value === 'boolean') return value;
  if (numeric === 0 || numeric === 1) return numeric === 1;
  if (typeof value === 'string') {
    if (['1', 'true', 't', 'yes', 'y', 'on'].includes(value.toLowerCase())) return true;
    if (['0', 'false', 'f', 'no', 'n', 'off'].includes(value.toLowerCase())) return false;
  }
  const parsing = typeof value === 'string' || numeric !== undefined;
  throw new HttpError(422, [
    {
      type: parsing ? 'bool_parsing' : 'bool_type',
      loc: ['body', 'confirm_activity'],
      msg: parsing
        ? 'Input should be a valid boolean, unable to interpret input'
        : 'Input should be a valid boolean',
      input: value,
    },
  ]);
}
function install_body(value: unknown): {
  app_sha: string;
  mode: UpdateMode;
  confirm_activity: boolean;
} {
  const raw = body(value),
    errors: unknown[] = [];
  for (const key of ['app_sha', 'mode']) {
    if (!(key in raw))
      errors.push({ type: 'missing', loc: ['body', key], msg: 'Field required', input: raw });
    else if (key === 'app_sha' && typeof raw[key] !== 'string')
      errors.push({
        type: 'string_type',
        loc: ['body', key],
        msg: 'Input should be a valid string',
        input: raw[key],
      });
    else if (key === 'mode' && raw[key] !== 'now' && raw[key] !== 'idle')
      errors.push({
        type: 'literal_error',
        loc: ['body', key],
        msg: "Input should be 'now' or 'idle'",
        input: raw[key],
        ctx: { expected: "'now' or 'idle'" },
      });
  }
  let confirm_activity = false;
  try {
    confirm_activity = confirmation(raw);
  } catch (error) {
    if (!(error instanceof HttpError) || !Array.isArray(error.detail)) throw error;
    errors.push(...error.detail);
  }
  if (errors.length) throw new HttpError(422, errors);
  return { app_sha: raw.app_sha as string, mode: raw.mode as UpdateMode, confirm_activity };
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
        if (error instanceof UpdateConflict) throw new HttpError(409, error.message);
        throw error;
      }
    };
    app.post('/updates/install', async (request) => {
      const value = install_body(request.body);
      requireOwner(services.identity(request));
      return act(() =>
        service.install(value.app_sha, value.mode, { confirm_activity: value.confirm_activity }),
      );
    });
    app.post<{ Params: { attempt_id: string } }>(
      '/updates/attempts/:attempt_id/cancel',
      async (request) => {
        requireOwner(services.identity(request));
        return act(() => service.cancel(request.params.attempt_id));
      },
    );
    app.post<{ Params: { attempt_id: string } }>(
      '/updates/attempts/:attempt_id/now',
      async (request) => {
        const confirm_activity = confirmation(body(request.body));
        requireOwner(services.identity(request));
        return act(() => service.update_now(request.params.attempt_id, { confirm_activity }));
      },
    );
    app.post<{ Params: { attempt_id: string } }>(
      '/updates/attempts/:attempt_id/retry',
      async (request) => {
        requireOwner(services.identity(request));
        return act(() => service.retry(request.params.attempt_id));
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
