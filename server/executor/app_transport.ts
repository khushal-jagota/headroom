/** Machine credentials select an executor owner before work-incarnation admission. */
import type { FastifyInstance, FastifyPluginAsync, FastifyReply, FastifyRequest } from 'fastify';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Duplex } from 'node:stream';
import { Readable } from 'node:stream';
import type { Config } from '../core/config.ts';
import type { Database } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import { sendFile } from '../core/http/static.ts';
import { trusted_ingress_login } from '../core/http/middleware.ts';
import { is_os_error } from '../core/system_errors.ts';
import { integer_text, decimal_digit } from '../core/python_integer.ts';
import { strip_unicode_space } from '../core/whitespace.ts';
import {
  admit_unidentified_call,
  websocket_refusal,
} from '../core/unidentified_agent_calls/admission.ts';
import type { Endpoint } from '../core/unidentified_agent_calls/contracts.ts';
import { UpdateConflict } from '../environments/updates/attempts.ts';
import {
  authenticate_executor,
  close_original_handover,
  issue_original_credential,
} from '../machines/executor_credentials.ts';
import type { VpsExecutorRegistry } from './registry.ts';
import {
  decode_bounded,
  decode_json,
  encode_bounded,
  ExecutorValidationError,
  parse_identity,
} from './protocol.ts';
import type { WireInput, WireRootName, WireTypes } from './protocol.ts';
import type { InputFile } from './contracts.ts';
import {
  FILE_TRANSFER_STALL_SECONDS,
  TRANSFER_CHUNK_BYTES,
  MAX_ENVELOPE_BYTES,
  MAX_COMMAND_OUTCOME_REPORT_BYTES,
  MAX_ITEM_BATCH_BYTES,
  MAX_MAILBOX_POLL_BYTES,
  MAX_MAC_UPDATE_RESPONSE_BYTES,
  EXECUTOR_HANDOVER_CLOSE_PATH,
} from './configuration.ts';

const EXECUTOR_ID = 'x-panels-executor-id';
const INCARNATION = 'x-panels-executor-incarnation';
const PROTOCOL = 'x-panels-executor-protocol';
const CREDENTIAL = 'x-panels-executor-credential';
const PLATFORM = 'x-panels-executor-platform';
const DESCRIPTION = 'x-panels-file-upload';
const SINGLETON = new Set([
  'tailscale-user-login',
  'origin',
  EXECUTOR_ID,
  INCARNATION,
  PROTOCOL,
  CREDENTIAL,
]);
export interface ExecutorHttpServices {
  readonly config: Config;
  readonly database: Database;
  readonly registry: VpsExecutorRegistry | null;
}
function header_map(request: IncomingMessage): Map<string, string[]> {
  const headers = new Map<string, string[]>();
  for (let i = 0; i < request.rawHeaders.length; i += 2) {
    const name = request.rawHeaders[i]!.toLowerCase();
    headers.set(name, [...(headers.get(name) ?? []), request.rawHeaders[i + 1]!]);
  }
  return headers;
}
/** Credentials bind this claim to a destination after trusted ingress admission. */
export function executor_ingress_claim(raw: IncomingMessage, trusted_login: string | null): string {
  if (!trusted_login) throw new ExecutorValidationError('trusted executor ingress is required');
  const headers = header_map(raw);
  for (const [name, values] of headers)
    if (SINGLETON.has(name) && values.length !== 1)
      throw new ExecutorValidationError('duplicate executor security header is not allowed');
  if (headers.has('origin'))
    throw new ExecutorValidationError('browser origin is not allowed on executor routes');
  const claimed_id = headers.get(EXECUTOR_ID)?.[0];
  if (!claimed_id || claimed_id.length > 128)
    throw new ExecutorValidationError('executor identity is required');
  return claimed_id;
}
/** Only the original configured executor can ask for its bootstrap credential. */
export function require_executor_ingress(
  raw: IncomingMessage,
  executor_id: string | null,
  trusted_login: string | null,
): string {
  const claimed_id = executor_ingress_claim(raw, trusted_login);
  if (!executor_id || claimed_id !== executor_id)
    throw new ExecutorValidationError('executor identity does not match configuration');
  return claimed_id;
}
function authenticated_machine(
  raw: IncomingMessage,
  trusted_login: string | null,
  services: ExecutorHttpServices,
): string {
  let claimed_id: string;
  try {
    claimed_id = executor_ingress_claim(raw, trusted_login);
  } catch (error) {
    if (!(error instanceof ExecutorValidationError)) throw error;
    throw new PlannerError('agent_forbidden', error.message);
  }
  const headers = header_map(raw);
  return services.database.write((tx) =>
    authenticate_executor(
      tx,
      claimed_id,
      headers.get(CREDENTIAL)?.[0] ?? null,
      services.config.executor_id,
      headers.get(INCARNATION)?.[0] ?? null,
      { authenticated_platform: headers.get(PLATFORM)?.[0] ?? null },
    ),
  );
}
class BodyTooLarge extends PlannerError {
  constructor() {
    super('validation', 'executor request body exceeds its size limit');
  }
}
function error_reply(reply: FastifyReply, error: PlannerError): FastifyReply {
  const status =
    error instanceof BodyTooLarge
      ? 413
      : ((
          {
            executor_version_refused: 409,
            executor_incarnation_not_current: 409,
            agent_forbidden: 403,
            not_found: 404,
            retry_later: 503,
          } as Record<string, number>
        )[error.code] ?? 400);
  return reply.code(status).send(error.toPayload());
}
function typed_reply<K extends WireRootName>(
  reply: FastifyReply,
  value: WireInput<K>,
  root: K,
  limit = MAX_ENVELOPE_BYTES,
  status = 200,
): FastifyReply {
  return reply
    .code(status)
    .type('application/json')
    .send(Buffer.from(encode_bounded(value, root, { limit })));
}
function source(request: FastifyRequest): Readable {
  return request.body instanceof Readable ? request.body : request.raw;
}
async function body(request: FastifyRequest, limit: number): Promise<Buffer> {
  const input = source(request),
    chunks: Buffer[] = [];
  let size = 0;
  for await (const part of input.iterator({ destroyOnReturn: false })) {
    const chunk = Buffer.from(part as Uint8Array);
    size += chunk.length;
    if (size > limit) {
      input.resume();
      throw new BodyTooLarge();
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
function decode<K extends WireRootName>(bytes: Uint8Array, root: K, limit: number): WireTypes[K] {
  try {
    return decode_bounded(bytes, root, { limit });
  } catch (error) {
    if (!(error instanceof ExecutorValidationError)) throw error;
    throw new PlannerError('validation', `malformed executor request: ${error.message}`);
  }
}
function description(text: string): Buffer {
  if (/[^\x00-\x7f]/u.test(text))
    throw new PlannerError('validation', 'invalid upload description');
  const bytes = text.replaceAll('-', '+').replaceAll('_', '/');
  const cleaned = bytes.replace(/[^A-Za-z0-9+/=]/g, '');
  // Python's permissive decoder ignores nonalphabet bytes, but rejects a lone final sextet.
  const prefix = cleaned.split('=')[0]!;
  if (prefix.length % 4 === 1) throw new PlannerError('validation', 'invalid upload description');
  return Buffer.from(bytes + '='.repeat(((-text.length % 4) + 4) % 4), 'base64');
}
function poll_parameters(request: FastifyRequest): {
  after: WireTypes['FileManifest']['byte_count'];
  wait: number;
} {
  const params = new URL(request.url, 'http://executor').searchParams;
  const after = params.getAll('after').at(-1) ?? '';
  const raw_wait = params.getAll('wait').at(-1) ?? '0';
  const cursor = /[\u001c-\u001f]/u.test(after) ? null : integer_text(after);
  const text = strip_unicode_space(raw_wait).replace(/\p{Decimal_Number}/gu, decimal_digit);
  const grammar =
    /^[+-]?(?:(?:[0-9](?:_?[0-9])*)?(?:\.(?:[0-9](?:_?[0-9])*)?)?|[0-9](?:_?[0-9])*)(?:[eE][+-]?[0-9](?:_?[0-9])*)?$/;
  const numeric = text.replaceAll('_', '');
  let wait: number;
  if (/^[+-]?(?:inf(?:inity)?|nan)$/i.test(numeric))
    wait = /nan/i.test(numeric) ? NaN : numeric.startsWith('-') ? -Infinity : Infinity;
  else if (text === '' || !/[0-9]/.test(text) || !grammar.test(text))
    throw new PlannerError('validation', 'invalid poll parameters');
  else wait = Number(numeric);
  if (cursor === null || !Number.isSafeInteger(Number(cursor)))
    throw new PlannerError('validation', 'invalid poll parameters');
  return { after: Number(cursor), wait };
}
async function send_chunk(response: ServerResponse, chunk: Uint8Array | null): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(
      () => finish(new Error('Executor input transfer stopped moving.')),
      FILE_TRANSFER_STALL_SECONDS * 1000,
    );
    const finish = (error?: Error | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      response.off('close', closed);
      response.off('error', failed);
      if (error) reject(error);
      else resolve();
    };
    const closed = () => finish(new Error('Executor input response disconnected.'));
    const failed = (error: Error) => finish(error);
    response.once('close', closed);
    response.once('error', failed);
    if (response.destroyed) return closed();
    try {
      if (chunk === null) response.end(() => finish());
      else response.write(chunk, (error) => finish(error));
    } catch (error) {
      finish(error instanceof Error ? error : new Error(String(error)));
    }
  });
}
async function input_response(reply: FastifyReply, file: InputFile): Promise<void> {
  reply.hijack();
  const response = reply.raw;
  try {
    response.writeHead(200, {
      'content-type': 'application/octet-stream',
      'content-length': String(file.byte_count),
      'x-panels-file-sha256': file.sha256,
    });
    for await (const chunk of file.chunks)
      for (let offset = 0; offset < chunk.length; offset += TRANSFER_CHUNK_BYTES)
        await send_chunk(response, chunk.subarray(offset, offset + TRANSFER_CHUNK_BYTES));
    await send_chunk(response, null);
  } catch (error) {
    response.destroy(error instanceof Error ? error : new Error(String(error)));
  } finally {
    await file.close();
  }
}
function update_failure(error: unknown): never {
  if (
    error instanceof UpdateConflict ||
    error instanceof ExecutorValidationError ||
    is_os_error(error)
  )
    throw new HttpError(409, (error as Error).message);
  throw error;
}
/** No ordinary actor route can bypass the dedicated ingress admission below. */
export function create_executor_routes(services: ExecutorHttpServices): FastifyPluginAsync {
  return async (app) => {
    app.removeAllContentTypeParsers();
    app.addContentTypeParser('*', (_request, payload, done) => done(null, payload));
    const authenticate = (request: FastifyRequest) =>
      authenticated_machine(
        request.raw,
        (request as FastifyRequest & { trustedLogin?: string }).trustedLogin ?? null,
        services,
      );
    const endpoint_for = async (request: FastifyRequest) => {
      const machine_id = authenticate(request);
      if (services.registry === null)
        throw new PlannerError('retry_later', 'executor endpoint unavailable');
      return services.registry.ensure(machine_id);
    };
    const current_for = async (request: FastifyRequest) => {
      const endpoint = await endpoint_for(request);
      return {
        endpoint,
        identity: endpoint.require_current({
          incarnation_header: (request.headers[INCARNATION] as string) ?? null,
          protocol_header: (request.headers[PROTOCOL] as string) ?? null,
        }),
      };
    };
    const updates_for = async (request: FastifyRequest) => {
      let machine_id: string;
      try {
        machine_id = authenticate(request);
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        throw new HttpError(403, error.message);
      }
      if (services.registry === null)
        throw new PlannerError('retry_later', 'executor endpoint unavailable');
      const endpoint = await services.registry.ensure(machine_id);
      endpoint.record_contact();
      const updates = services.registry.update_service_for(machine_id);
      if (!updates) throw new HttpError(503, 'Mac update service unavailable');
      return { endpoint, updates };
    };
    const work =
      (handler: (request: FastifyRequest, reply: FastifyReply) => Promise<unknown>) =>
      async (request: FastifyRequest, reply: FastifyReply) => {
        try {
          return await handler(request, reply);
        } catch (error) {
          if (error instanceof PlannerError) return error_reply(reply, error);
          throw error;
        }
      };
    app.post(
      '/api/executor/credential',
      work(async (request, reply) => {
        try {
          require_executor_ingress(
            request.raw,
            services.config.executor_id,
            (request as FastifyRequest & { trustedLogin?: string }).trustedLogin ?? null,
          );
          if (header_map(request.raw).has(CREDENTIAL))
            throw new PlannerError('agent_forbidden', 'Credential handover is already closed');
          const raw = decode(
            await body(request, MAX_ENVELOPE_BYTES),
            'JsonObject',
            MAX_ENVELOPE_BYTES,
          );
          const identity = parse_identity(raw);
          const grant = issue_original_credential(services.database, identity.executor_incarnation);
          return reply.send(grant);
        } catch (error) {
          if (error instanceof ExecutorValidationError)
            throw new PlannerError('agent_forbidden', error.message);
          throw error;
        }
      }),
    );
    app.post(
      EXECUTOR_HANDOVER_CLOSE_PATH,
      work(async (request, reply) => {
        const headers = header_map(request.raw);
        const credential = headers.get(CREDENTIAL)?.[0];
        if (credential === undefined)
          throw new PlannerError('agent_forbidden', 'Executor credential is required');
        const machine_id = authenticate(request);
        close_original_handover(
          services.database,
          machine_id,
          credential,
          headers.get(INCARNATION)?.[0] ?? null,
        );
        return reply.send({ closed: true });
      }),
    );
    app.post(
      '/api/executor/lease',
      work(async (request, reply) => {
        const endpoint = await endpoint_for(request);
        endpoint.record_contact();
        const response = await endpoint.lease(
          decode(await body(request, MAX_ENVELOPE_BYTES), 'LeaseRequest', MAX_ENVELOPE_BYTES),
        );
        return 'error' in response
          ? typed_reply(reply, response, 'LeaseRefused', MAX_ENVELOPE_BYTES, 409)
          : typed_reply(reply, response, 'LeaseReply');
      }),
    );
    app.get(
      '/api/executor/commands',
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request),
          { after, wait } = poll_parameters(request);
        const cancellation = new AbortController();
        const disconnected = () => cancellation.abort();
        reply.raw.once('close', disconnected);
        if (reply.raw.destroyed) disconnected();
        try {
          return typed_reply(
            reply,
            await endpoint.poll(identity, after, wait, { signal: cancellation.signal }),
            'MailboxPollReply',
            MAX_MAILBOX_POLL_BYTES,
          );
        } finally {
          reply.raw.off('close', disconnected);
        }
      }),
    );
    app.post(
      '/api/executor/commands/outcomes',
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request);
        return typed_reply(
          reply,
          await endpoint.record_outcomes(
            identity,
            decode(
              await body(request, MAX_COMMAND_OUTCOME_REPORT_BYTES),
              'CommandOutcomeReport',
              MAX_COMMAND_OUTCOME_REPORT_BYTES,
            ),
          ),
          'CommandOutcomeReply',
        );
      }),
    );
    app.post(
      '/api/executor/conversations/:conversation_id/items',
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request),
          { conversation_id } = request.params as { conversation_id: string };
        return typed_reply(
          reply,
          await endpoint.accept_items(
            identity,
            conversation_id,
            decode(await body(request, MAX_ITEM_BATCH_BYTES), 'ItemBatch', MAX_ITEM_BATCH_BYTES),
          ),
          'ItemBatchReply',
          MAX_ITEM_BATCH_BYTES,
        );
      }),
    );
    app.put(
      '/api/executor/uploads/:sha256',
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request),
          { sha256 } = request.params as { sha256: string };
        if (request.headers['content-encoding'] || !/^[0-9a-f]{64}$/.test(sha256))
          throw new PlannerError('validation', 'an upload is raw bytes named by digest');
        const upload = decode(
          description(String(request.headers[DESCRIPTION] ?? '')),
          'FileUploadRequest',
          MAX_ENVELOPE_BYTES,
        );
        const length = request.headers['content-length'];
        const count = typeof length === 'string' && /^[0-9]+$/.test(length) ? Number(length) : null;
        try {
          await endpoint.upload(identity, sha256, upload, count, source(request));
        } catch (error) {
          if (request.raw.aborted) return reply.code(400).send();
          throw error;
        }
        return reply.code(204).send();
      }),
    );
    app.get(
      '/api/executor/commands/:command_id/files/:stored_file_id',
      { config: { uncompressed: true } },
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request),
          params = request.params as { command_id: string; stored_file_id: string };
        await input_response(
          reply,
          await endpoint.input_file(identity, params.command_id, params.stored_file_id),
        );
        return reply;
      }),
    );
    app.post(
      '/api/executor/reads',
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request);
        const value = await endpoint.read(
          identity,
          decode(await body(request, MAX_ENVELOPE_BYTES), 'LiveReadRequest', MAX_ENVELOPE_BYTES),
        );
        return reply.type('application/json').send(JSON.stringify(value));
      }),
    );
    app.post(
      '/api/executor/conversations/:conversation_id/live',
      work(async (request, reply) => {
        const { endpoint, identity } = await current_for(request),
          { conversation_id } = request.params as { conversation_id: string };
        await endpoint.live(
          identity,
          conversation_id,
          decode(await body(request, MAX_ENVELOPE_BYTES), 'LiveEventBatch', MAX_ENVELOPE_BYTES),
        );
        return reply.code(204).send();
      }),
    );
    app.get('/api/executor/update', { config: { uncompressed: true } }, async (request, reply) => {
      try {
        const { updates } = await updates_for(request);
        return typed_reply(reply, updates.check(), 'MacUpdateCheck');
      } catch (error) {
        update_failure(error);
      }
    });
    const archive = async (request: FastifyRequest, reply: FastifyReply, prefetch: boolean) => {
      try {
        const params = request.params as { request_id: string; archive_sha256: string };
        const { updates } = await updates_for(request);
        const path = await (prefetch
          ? updates.prefetch_archive(params.archive_sha256)
          : updates.archive(params.request_id));
        await sendFile(request, reply, path, { mime: 'application/gzip' });
        return reply;
      } catch (error) {
        update_failure(error);
      }
    };
    app.get(
      '/api/executor/update/:request_id/archive',
      { config: { uncompressed: true } },
      (request, reply) => archive(request, reply, false),
    );
    app.get(
      '/api/executor/update/prefetch/:archive_sha256',
      { config: { uncompressed: true } },
      (request, reply) => archive(request, reply, true),
    );
    app.post('/api/executor/update', { config: { uncompressed: true } }, async (request, reply) => {
      const { endpoint, updates } = await updates_for(request);
      let bytes: Uint8Array;
      try {
        bytes = await body(request, MAX_MAC_UPDATE_RESPONSE_BYTES);
      } catch (error) {
        if (error instanceof BodyTooLarge)
          throw new HttpError(413, 'Mac update report exceeds its size limit');
        throw error;
      }
      let report: WireTypes['MacUpdateReport'];
      try {
        report = decode_json(bytes, 'MacUpdateReport');
        // This read and report call share one uninterrupted synchronous continuation.
        updates.report(report, endpoint.health());
      } catch (error) {
        update_failure(error);
      }
      return reply.send({ confirmed: report.phase === 'current' });
    });
    install_old_websocket_refusal(app, services);
  };
}
function close_before_accept(socket: Duplex, refusal: { code: 1008; reason?: string }): void {
  void refusal;
  socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
}
function install_old_websocket_refusal(app: FastifyInstance, services: ExecutorHttpServices): void {
  const upgrade = (request: IncomingMessage, socket: Duplex): void => {
    if ((request.url ?? '').split('?')[0] !== '/api/executor/connect') return;
    const operation = async () => {
      const headers = header_map(request),
        config = services.config;
      let trusted_login: string | null = null;
      try {
        trusted_login = trusted_ingress_login(config, headers, 'websocket');
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        return close_before_accept(socket, { code: 1008, reason: error.message });
      }
      const endpoint = (host?: string, port?: number): Endpoint | null =>
        host === undefined || port === undefined ? null : [host, port];
      const connection = request.socket;
      const admitted = await admit_unidentified_call(config.refuse_unidentified_agent_calls, {
        kind: 'websocket',
        authenticated_owner: trusted_login !== null,
        headers: [...headers].flatMap(([name, values]) =>
          values.map((value) => [name, value] as const),
        ),
        client: endpoint(connection.remoteAddress, connection.remotePort),
        server: endpoint(connection.localAddress, connection.localPort),
      });
      if (!admitted.admitted) return close_before_accept(socket, websocket_refusal());
      let machine_id: string;
      try {
        machine_id = authenticated_machine(request, trusted_login, services);
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        return close_before_accept(socket, { code: 1008 });
      }
      try {
        if (services.registry !== null) {
          const owner = await services.registry.ensure(machine_id);
          await owner.refuse_old_websocket();
        }
      } finally {
        close_before_accept(socket, { code: 1008 });
      }
    };
    void (app.request_lifetime ? app.request_lifetime.run(operation) : operation()).catch(() =>
      socket.destroy(),
    );
  };
  app.server.on('upgrade', upgrade);
  app.addHook('onClose', async () => {
    app.server.off('upgrade', upgrade);
  });
}
