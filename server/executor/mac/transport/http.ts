/** Protocol-22 Mac client. Each request retains its loop's independent session owner. */
import type { WorkerSetupReport } from '../../wire.generated.ts';
import { CLIENT_IDENTITY_HEADER } from '../../../client_versions/contracts.ts';
import type { ClientIdentity } from '../../../client_versions/contracts.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { is_os_error } from '../../../core/system_errors.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { read_credential, write_credential } from '../../credential_file.ts';
import { decode_bounded, encode_bounded, ExecutorValidationError } from '../../protocol.ts';
import type {
  CommandOutcomeReply,
  ChildReference,
  ItemBatch,
  LeaseRequest,
  LiveEventBatch,
  LiveReadRequest,
  WorkspaceIdentity,
  WorkspaceState,
  WorkspaceStatesReply,
} from '../../wire.generated.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import * as settings from '../configuration.ts';
import type { MacHttpOptions, HttpRequest } from './contracts.ts';
import { HttpSessions, read_bounded } from './sessions.ts';
import { ExecutorHttpError, HttpTimeout, http_error } from './errors.ts';
export { ExecutorHttpError, failure_name } from './errors.ts';
export const CONNECTORS = {
  lease: settings.MAC_LEASE_CONNECTOR,
  poll: settings.MAC_POLL_CONNECTOR,
  outcomes: settings.MAC_OUTCOME_CONNECTOR,
  items: settings.MAC_ITEMS_CONNECTOR,
  downloads: settings.MAC_DOWNLOADS_CONNECTOR,
  reads: settings.MAC_READS_CONNECTOR,
  live: settings.MAC_LIVE_CONNECTOR,
};
const AUTHENTICATION_HEADERS = new Set([
  'X-Panels-Executor-ID',
  'X-Panels-Executor-Credential',
  'X-Panels-Executor-Platform',
  CLIENT_IDENTITY_HEADER,
]);
const CERTIFICATE_ERRORS = new Set([
  'CERT_HAS_EXPIRED',
  'CERT_NOT_YET_VALID',
  'CERT_REJECTED',
  'CERT_SIGNATURE_FAILURE',
  'CERT_UNTRUSTED',
  'CRL_HAS_EXPIRED',
  'CRL_NOT_YET_VALID',
  'DEPTH_ZERO_SELF_SIGNED_CERT',
  'SELF_SIGNED_CERT_IN_CHAIN',
  'UNABLE_TO_GET_CRL',
  'UNABLE_TO_GET_ISSUER_CERT',
  'UNABLE_TO_GET_ISSUER_CERT_LOCALLY',
  'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
]);
export function executor_request_failure(error: unknown): boolean {
  if (
    error instanceof ExecutorHttpError ||
    error instanceof HttpTimeout ||
    error instanceof ExecutorValidationError ||
    is_os_error(error)
  )
    return true;
  if (!(error instanceof Error) || !('code' in error) || typeof error.code !== 'string')
    return false;
  return (
    /^(?:ERR_TLS_|ERR_SSL_|HPE_)/u.test(error.code) ||
    error.code === 'ERR_STREAM_PREMATURE_CLOSE' ||
    CERTIFICATE_ERRORS.has(error.code)
  );
}
function granted_credential(body: Uint8Array): string {
  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(body));
  } catch (error) {
    if (
      error instanceof SyntaxError ||
      (error instanceof TypeError &&
        'code' in error &&
        error.code === 'ERR_ENCODING_INVALID_ENCODED_DATA')
    )
      throw new ExecutorValidationError('The home returned an invalid machine credential');
    throw error;
  }
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    !('credential' in value) ||
    typeof value.credential !== 'string'
  )
    throw new ExecutorValidationError('The home returned an invalid machine credential');
  return value.credential;
}
export class MacHttpClient {
  readonly sessions: HttpSessions;
  readonly server_url: string;
  private request_headers: Readonly<Record<string, string>>;
  private readonly identity: Readonly<ExecutorIdentity>;
  constructor(
    server_url: string,
    executor_id: string,
    identity: ExecutorIdentity,
    options: MacHttpOptions = {},
  ) {
    this.server_url = settings.validate_server_url(server_url).replace(/\/$/, '');
    this.identity = { ...identity };
    this.request_headers = Object.freeze({
      [CLIENT_IDENTITY_HEADER]: JSON.stringify({
        kind: 'machine',
        device_id: executor_id,
        version: identity.application_revision,
        protocol_version: identity.protocol_version,
        outbox_format: identity.spool_format_version,
      } satisfies ClientIdentity),
      'X-Panels-Executor-ID': executor_id,
      'X-Panels-Executor-Incarnation': identity.executor_incarnation,
      'X-Panels-Executor-Protocol': String(identity.protocol_version),
      'X-Panels-Executor-Platform': process.platform,
      'Accept-Encoding': 'identity',
    });
    this.sessions = new HttpSessions(this.server_url, CONNECTORS, options);
  }
  get headers(): Readonly<Record<string, string>> {
    return this.request_headers;
  }
  authentication_headers(): Readonly<Record<string, string>> {
    return Object.fromEntries(
      Object.entries(this.headers).filter(([name]) => AUTHENTICATION_HEADERS.has(name)),
    );
  }
  async ensure_credential(path: string): Promise<void> {
    return this.sessions.own('lease', async (require_open) => {
      const saved = await read_credential(path);
      require_open();
      const headers = { ...this.headers };
      delete headers['X-Panels-Executor-Credential'];
      this.request_headers = Object.freeze(headers);
      let credential: string;
      try {
        const identity = this.identity;
        const reply = await this.json({
          loop: 'lease',
          method: 'POST',
          path: '/api/executor/credential',
          timeout: settings.LEASE_TIMEOUT_SECONDS,
          body: Buffer.from(
            JSON.stringify({
              protocol_version: identity.protocol_version,
              application_revision: identity.application_revision,
              spool_format_version: identity.spool_format_version,
              executor_incarnation: identity.executor_incarnation,
            }),
          ),
        });
        require_open();
        if (reply.status !== 200) throw http_error(reply.status, reply.body);
        const granted = granted_credential(reply.body);
        if (granted !== saved) await write_credential(path, granted);
        credential = granted;
      } catch (error) {
        if (saved === null || !executor_request_failure(error)) throw error;
        require_open();
        credential = saved;
      }
      this.request_headers = Object.freeze({
        ...this.headers,
        'X-Panels-Executor-Credential': credential,
      });
    });
  }
  private json(input: HttpRequest, limit = settings.MAX_ENVELOPE_BYTES) {
    return this.sessions.request(
      input,
      { ...this.headers, ...(input.body ? { 'Content-Type': 'application/json' } : {}) },
      async (response) => ({
        status: response.statusCode!,
        body: await read_bounded(response, limit),
      }),
    );
  }
  async lease(request: LeaseRequest, signal?: AbortSignal) {
    const reply = await this.json({
      loop: 'lease',
      method: 'POST',
      path: '/api/executor/lease',
      body: encode_bounded(request, 'LeaseRequest'),
      timeout: settings.LEASE_TIMEOUT_SECONDS,
      signal,
    });
    if (reply.status === 200) return decode_bounded(reply.body, 'LeaseReply');
    if (reply.status === 409) {
      try {
        return decode_bounded(reply.body, 'LeaseRefused');
      } catch (error) {
        if (!(error instanceof Error)) throw error;
      }
    }
    throw http_error(reply.status, reply.body);
  }
  async poll_commands(
    after: number,
    hold_seconds: number,
    signal: AbortSignal,
  ): Promise<Uint8Array> {
    const query = new URLSearchParams({
      after: String(after),
      wait: String(Math.trunc(hold_seconds)),
    });
    const reply = await this.json(
      {
        loop: 'poll',
        method: 'GET',
        path: `/api/executor/commands?${query}`,
        timeout: settings.COMMAND_POLL_TIMEOUT_SECONDS,
        signal,
      },
      settings.MAX_MAILBOX_POLL_BYTES,
    );
    if (reply.status !== 200) throw http_error(reply.status, reply.body);
    return reply.body;
  }
  async report_outcomes(body: CanonicalReader, signal: AbortSignal): Promise<CommandOutcomeReply> {
    if (
      !Number.isSafeInteger(body.byte_count) ||
      body.byte_count <= 0 ||
      body.byte_count > settings.MAX_COMMAND_OUTCOME_REPORT_BYTES
    )
      throw new ExecutorValidationError('body exceeds its size limit');
    const reply = await this.json({
      loop: 'outcomes',
      method: 'POST',
      path: '/api/executor/commands/outcomes',
      body,
      byte_count: body.byte_count,
      headers: { 'Content-Length': String(body.byte_count) },
      timeout: settings.COMMAND_OUTCOME_TIMEOUT_SECONDS,
      signal,
    });
    if (reply.status !== 200) throw http_error(reply.status, reply.body);
    return decode_bounded(reply.body, 'CommandOutcomeReply');
  }
  async post_items(conversation_id: string, batch: ItemBatch, signal?: AbortSignal) {
    const reply = await this.json(
      {
        loop: 'items',
        method: 'POST',
        path: `/api/executor/conversations/${encodeURIComponent(conversation_id)}/items`,
        body: encode_bounded(batch, 'ItemBatch', { limit: settings.MAX_ITEM_BATCH_BYTES }),
        timeout: settings.ITEM_BATCH_TIMEOUT_SECONDS,
        signal,
      },
      settings.MAX_ITEM_BATCH_BYTES,
    );
    if (reply.status !== 200) throw http_error(reply.status, reply.body);
    return decode_bounded(reply.body, 'ItemBatchReply', { limit: settings.MAX_ITEM_BATCH_BYTES });
  }
  async read(request: LiveReadRequest, signal?: AbortSignal) {
    const reply = await this.json({
      loop: 'reads',
      method: 'POST',
      path: '/api/executor/reads',
      body: encode_bounded(request, 'LiveReadRequest'),
      timeout: settings.LIVE_READ_TIMEOUT_SECONDS,
      signal,
    });
    if (reply.status !== 200) throw http_error(reply.status, reply.body);
    return decode_bounded(reply.body, 'JsonObject');
  }
  async workspace_states(
    ticket_ids: readonly string[],
    signal?: AbortSignal,
  ): Promise<WorkspaceStatesReply> {
    const reply = await this.json(
      {
        loop: 'reads',
        method: 'POST',
        path: '/api/executor/workspaces/states',
        body: encode_bounded({ ticket_ids: [...ticket_ids] }, 'WorkspaceStatesRequest', {
          limit: settings.MAX_WORKSPACE_STATES_BYTES,
        }),
        timeout: settings.LIVE_READ_TIMEOUT_SECONDS,
        signal,
      },
      settings.MAX_WORKSPACE_STATES_BYTES,
    );
    if (reply.status !== 200) throw http_error(reply.status, reply.body);
    return decode_bounded(reply.body, 'WorkspaceStatesReply', {
      limit: settings.MAX_WORKSPACE_STATES_BYTES,
    });
  }
  async register_workspace(
    child: ChildReference,
    workspace: WorkspaceIdentity,
    signal?: AbortSignal,
  ): Promise<void> {
    const reply = await this.json({
      loop: 'reads',
      method: 'POST',
      path: '/api/executor/workspaces/register',
      body: encode_bounded({ child, workspace }, 'WorkspaceRegistrationRequest'),
      timeout: settings.LIVE_READ_TIMEOUT_SECONDS,
      signal,
    });
    if (reply.status !== 204) throw http_error(reply.status, reply.body);
  }
  async report_worker_setup(child: ChildReference, step: WorkerSetupReport['step']): Promise<void> {
    const reply = await this.json({
      loop: 'reads',
      method: 'POST',
      path: '/api/executor/workspaces/setup',
      body: encode_bounded({ child, step }, 'WorkerSetupReport'),
      timeout: settings.LIVE_READ_TIMEOUT_SECONDS,
    });
    if (reply.status !== 204) throw http_error(reply.status, reply.body);
  }
  async retire_workspace(
    state: WorkspaceState,
    workspace: WorkspaceIdentity,
    options: { replay?: boolean; signal?: AbortSignal } = {},
  ): Promise<boolean> {
    const reply = await this.json({
      loop: 'reads',
      method: 'POST',
      path: '/api/executor/workspaces/retire',
      body: encode_bounded(
        { state, workspace, replay: options.replay ?? false },
        'WorkspaceRetirementRequest',
      ),
      timeout: settings.LIVE_READ_TIMEOUT_SECONDS,
      signal: options.signal,
    });
    if (reply.status !== 200) throw http_error(reply.status, reply.body);
    return decode_bounded(reply.body, 'WorkspaceRetirementReply').authorized;
  }
  async post_live(
    conversation_id: string,
    batch: LiveEventBatch,
    signal?: AbortSignal,
  ): Promise<void> {
    const reply = await this.json({
      loop: 'live',
      method: 'POST',
      path: `/api/executor/conversations/${encodeURIComponent(conversation_id)}/live`,
      body: encode_bounded(batch, 'LiveEventBatch'),
      timeout: settings.LIVE_EVENT_TIMEOUT_SECONDS,
      signal,
    });
    if (reply.status !== 204) throw http_error(reply.status, reply.body);
  }
  reset(): Promise<void> {
    return this.sessions.reset();
  }
  stop(options: StopOptions): Promise<void> {
    return observe_shutdown(
      this.sessions.close(),
      options,
      'Mac HTTP requests remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.sessions.settled();
  }
}
