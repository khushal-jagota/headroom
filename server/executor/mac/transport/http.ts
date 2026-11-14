/** Protocol-22 Mac client. Each request retains its loop's independent session owner. */
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { decode_bounded, encode_bounded, ExecutorValidationError } from '../../protocol.ts';
import type {
  CommandOutcomeReply,
  ItemBatch,
  LeaseRequest,
  LiveEventBatch,
  LiveReadRequest,
} from '../../wire.generated.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import * as settings from '../configuration.ts';
import type { MacHttpOptions, HttpRequest } from './contracts.ts';
import { HttpSessions, read_bounded } from './sessions.ts';
import { http_error } from './errors.ts';
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
export class MacHttpClient {
  readonly sessions: HttpSessions;
  readonly headers: Readonly<Record<string, string>>;
  readonly server_url: string;
  constructor(
    server_url: string,
    executor_id: string,
    identity: ExecutorIdentity,
    options: MacHttpOptions = {},
  ) {
    this.server_url = settings.validate_server_url(server_url).replace(/\/$/, '');
    this.headers = Object.freeze({
      'X-Panels-Executor-ID': executor_id,
      'X-Panels-Executor-Incarnation': identity.executor_incarnation,
      'X-Panels-Executor-Protocol': String(identity.protocol_version),
      'Accept-Encoding': 'identity',
    });
    this.sessions = new HttpSessions(this.server_url, CONNECTORS, options);
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
