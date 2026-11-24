/** Only display state crosses this boundary. Provider credentials stay on the machine. */
import type { BackendRequest, JsonObject } from './wire.generated.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import type { BackendSnapshotServiceContract } from '../conversation/snapshot/contracts.ts';
import type { ExecutorCatalogSource } from '../drivers/core/executor.ts';
import {
  SignInReturnUrlRefused,
  SignInUnavailable,
  UnknownSignInSession,
} from '../drivers/core/sign_in.ts';
import { sign_in_json } from '../conversation/snapshot/presentation.ts';
export async function run_backend_request(
  request: BackendRequest,
  source: ExecutorCatalogSource | undefined,
  signal?: AbortSignal,
  resolve_secret?: (id: string) => Promise<string>,
  snapshots?: Pick<BackendSnapshotServiceContract, 'update_backend'>,
): Promise<JsonObject> {
  try {
    if (request.operation === 'update_backend') {
      if (snapshots === undefined)
        throw new SignInUnavailable('This machine cannot update providers.');
      return JSON.parse(
        JSON.stringify(
          await snapshots.update_backend(parse_backend_instance_id(request.backend_key), {
            signal,
          }),
        ),
      ) as JsonObject;
    }
    if (request.operation === 'capabilities')
      return {
        can_sign_in: source?.sign_in !== undefined,
        reports_allowance_usage: source?.allowance_usage !== undefined,
      };
    if (request.operation === 'refresh_usage') {
      if (source?.allowance_usage === undefined)
        return {
          outcome: 'unavailable',
          backend_key: request.backend_key,
          detail: 'This backend does not report allowance.',
          observed_at: null,
          windows: [],
        };
      return JSON.parse(
        JSON.stringify(await source.allowance_usage.refresh({ signal })),
      ) as JsonObject;
    }
    const service = source?.sign_in;
    if (service === undefined)
      throw new SignInUnavailable('This backend cannot sign in from Panels.');
    switch (request.operation) {
      case 'sign_in_read': {
        const session = service.read();
        return session === null ? { sign_in: null } : sign_in_json(session);
      }
      case 'sign_in_start':
        return sign_in_json(await service.start());
      case 'sign_in_cancel':
        return sign_in_json(await service.cancel(request.session_id ?? ''));
      case 'sign_in_return_url':
        if (service.submit_return_url === undefined)
          throw new SignInUnavailable('This backend does not accept a return address.');
        if (!request.secret_id || !resolve_secret)
          throw new SignInUnavailable('The sign-in return address is unavailable.');
        return sign_in_json(
          await service.submit_return_url(
            request.session_id ?? '',
            await resolve_secret(request.secret_id),
          ),
        );
      case 'sign_out':
        if (service.sign_out === undefined)
          throw new SignInUnavailable('This backend cannot sign out from Panels.');
        await service.sign_out();
        return { sign_in: null };
    }
  } catch (error) {
    if (error instanceof UnknownSignInSession)
      return { error: { status: 409, message: 'That sign-in is no longer in force.' } };
    if (error instanceof SignInReturnUrlRefused)
      return { error: { status: 422, message: error.message } };
    if (error instanceof SignInUnavailable)
      return { error: { status: 503, message: error.message } };
    throw error;
  }
}
