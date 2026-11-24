/** Machine-scoped control requests share the authenticated mailbox and its journal. */
import type { SqliteCommandMailbox } from './vps_mailbox.ts';
import type { MacPresenceService } from './vps_presence.ts';
import type { BackendRequest, JsonObject } from './wire.generated.ts';
import { BackendControlSecrets } from './backend_control_secrets.ts';
import { BACKEND_CONTROL_LIMIT_SECONDS } from './configuration.ts';
import { HttpError } from '../core/http/errors.ts';
import { ServerLifecycleError, ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
export interface MachineBackendControls {
  request(request: BackendRequest, options?: { signal?: AbortSignal }): Promise<JsonObject>;
  readonly available: boolean;
  submit_return_url?(
    backend_key: string,
    session_id: string,
    return_url: string,
    options?: { signal?: AbortSignal },
  ): Promise<JsonObject>;
}
export class VpsBackendControlRelay implements MachineBackendControls {
  private readonly mailbox: Pick<
    SqliteCommandMailbox,
    'put_backend_request' | 'wait_result' | 'withdraw_waiting'
  >;
  private readonly presence: Pick<MacPresenceService, 'is_present' | 'supports_backend_controls'>;
  private readonly secrets = new BackendControlSecrets();
  private readonly cancellation = new AbortController();
  private readonly operations = new Set<Promise<JsonObject>>();
  private closing?: Promise<void>;
  constructor(
    mailbox: Pick<SqliteCommandMailbox, 'put_backend_request' | 'wait_result' | 'withdraw_waiting'>,
    presence: Pick<MacPresenceService, 'is_present' | 'supports_backend_controls'>,
  ) {
    this.mailbox = mailbox;
    this.presence = presence;
  }
  get available(): boolean {
    return (
      !this.cancellation.signal.aborted &&
      this.presence.is_present() &&
      this.presence.supports_backend_controls()
    );
  }
  request(request: BackendRequest, options: { signal?: AbortSignal } = {}): Promise<JsonObject> {
    if (this.cancellation.signal.aborted)
      return Promise.reject(new ServerLifecycleError('Machine backend controls are stopped.'));
    if (!this.available)
      return Promise.reject(new HttpError(503, 'The selected machine is unavailable.'));
    if (!this.presence.supports_backend_controls(request.operation))
      return Promise.reject(
        new HttpError(503, 'Update machine code before you update a provider CLI.'),
      );
    const signal =
      options.signal === undefined
        ? this.cancellation.signal
        : AbortSignal.any([options.signal, this.cancellation.signal]);
    const operation = this.invoke(request, signal);
    this.operations.add(operation);
    void operation.then(
      () => this.operations.delete(operation),
      () => this.operations.delete(operation),
    );
    return operation;
  }
  take_secret(id: string): string {
    return this.secrets.take('', id);
  }
  async submit_return_url(
    backend_key: string,
    session_id: string,
    return_url: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<JsonObject> {
    const secret_id = this.secrets.keep('', return_url);
    try {
      return await this.request(
        { backend_key, operation: 'sign_in_return_url', session_id, secret_id },
        options,
      );
    } finally {
      this.secrets.discard(secret_id);
    }
  }
  private async invoke(request: BackendRequest, signal: AbortSignal): Promise<JsonObject> {
    signal.throwIfAborted();
    // Retain the committed command ID even when the HTTP waiter cancels during this put.
    const command = await this.mailbox.put_backend_request(request);
    try {
      signal.throwIfAborted();
      const result = await this.mailbox.wait_result(
        command.command_id,
        request.operation === 'update_backend' ? null : BACKEND_CONTROL_LIMIT_SECONDS,
        { signal },
      );
      if (result?.backend_result == null)
        throw new HttpError(503, 'The selected machine did not answer.');
      const error = result.backend_result.error;
      if (error && typeof error === 'object' && !Array.isArray(error))
        throw new HttpError(
          typeof error.status === 'number' && [409, 422, 503].includes(error.status)
            ? error.status
            : 502,
          typeof error.message === 'string' ? error.message : 'Machine request failed.',
        );
      return result.backend_result;
    } finally {
      await this.mailbox.withdraw_waiting(command.command_id, 'delivery_withdrawn');
    }
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.closing) {
      this.cancellation.abort(new ServerLifecycleError('Machine backend controls stopped.'));
      this.closing = Promise.allSettled([...this.operations]).then(() => {});
    }
    return observe_shutdown(
      this.closing,
      options,
      new ShutdownDeadlineExceeded(['machine backend controls']).message,
      ['machine backend controls'],
    );
  }
  settled(): Promise<void> {
    return this.closing ?? Promise.allSettled([...this.operations]).then(() => {});
  }
}
