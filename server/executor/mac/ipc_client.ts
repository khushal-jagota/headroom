/** CLI transport to one owner-only Unix socket. A retry only inspects its original write. */
import { Socket } from 'node:net';
import { randomUUID } from 'node:crypto';
import {
  CLI_RESULT_TIMEOUT_SECONDS,
  CLI_TRANSPORT_RESERVE_SECONDS,
  EXECUTOR_CAPABILITY_ENV,
  EXECUTOR_CHILD_ID_ENV,
  MAX_ENVELOPE_BYTES,
  executor_paths,
} from './configuration.ts';
import { decode_json, decode_value, encode_frame, ValueError } from '../protocol.ts';
import type { IpcOperation, IpcResponse, JsonObject, MacLocalStatus } from '../wire.generated.ts';

export function cli_mutation(method: string, path: string, body: unknown): [string, JsonObject] {
  const blocker = /^\/api\/collections\/blockers\/([^/]+)\/([^/]+)$/.exec(path);
  if (blocker && (method === 'PUT' || method === 'DELETE'))
    return [
      method === 'PUT' ? 'ticket.block' : 'ticket.unblock',
      {
        ticket_id: blocker[1],
        blocker_id: blocker[2],
        body: {},
      },
    ];
  if (method === 'POST' && path === '/api/tickets')
    return ['ticket.create', { body } as JsonObject];
  const proposal = /^\/api\/tickets\/([^/]+)\/propose$/.exec(path);
  const patch = /^\/api\/tickets\/([^/]+)$/.exec(path);
  if (method === 'POST' && proposal)
    return ['ticket.propose', { ticket_id: proposal[1], body } as JsonObject];
  if (method === 'PATCH' && patch)
    return ['ticket.edit', { ticket_id: patch[1], body } as JsonObject];
  if (method === 'POST' && path === '/api/messages/send')
    return ['message.send', { body } as JsonObject];
  const help = /^\/api\/tickets\/([^/]+)\/request-help$/.exec(path);
  if (method === 'POST' && help)
    return ['ticket.request_help', { ticket_id: help[1], body } as JsonObject];
  throw new ValueError(`This executor does not support ${method} ${path}`);
}

/** Both runtimes use the OS monotonic clock for the deadline across this process boundary. */
const monotonic = () => Number(process.hrtime.bigint()) / 1_000_000_000;
export async function request(
  operation: IpcOperation,
  payload: JsonObject,
  environment: NodeJS.ProcessEnv = process.env,
  limits = {
    result_seconds: CLI_RESULT_TIMEOUT_SECONDS,
    reserve_seconds: CLI_TRANSPORT_RESERVE_SECONDS,
  },
): Promise<IpcResponse> {
  const deadline = monotonic() + limits.result_seconds;
  const request_id = randomUUID();
  let submission_id: string = request_id;
  if (operation === 'write') decode_value(payload, 'IpcWritePayload');
  else if (operation === 'retry')
    submission_id = decode_value(payload, 'IpcRetryPayload').submission_id;
  // Validation and frame limits precede socket creation and possible emission.
  const encoded = encode_frame(
    {
      request_id,
      operation,
      child_id: environment[EXECUTOR_CHILD_ID_ENV] ?? null,
      capability: environment[EXECUTOR_CAPABILITY_ENV] ?? null,
      payload,
      response_deadline: deadline,
    },
    'IpcRequest',
  );
  const socket_path = executor_paths(environment).ipc_socket;
  const transport_deadline = deadline + limits.reserve_seconds;
  let emission_possible = false;
  try {
    return await new Promise<IpcResponse>((resolve, reject) => {
      const socket = new Socket();
      const header = Buffer.alloc(4);
      let header_bytes = 0;
      let body: Buffer | undefined;
      let body_bytes = 0;
      let response: IpcResponse | undefined;
      let failure: unknown;
      let finished = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (error?: unknown) => {
        if (finished) return;
        finished = true;
        failure = error;
        clearTimeout(timer);
        socket.destroy();
      };
      socket.once('close', () => {
        clearTimeout(timer);
        if (response !== undefined) resolve(response);
        else reject(failure ?? new Error('executor IPC closed without a response'));
      });
      socket.on('error', finish);
      socket.on('end', () => finish(new Error('executor IPC closed without a response')));
      socket.on('data', (chunk: Buffer) => {
        if (finished) return;
        try {
          let offset = 0;
          if (header_bytes < 4) {
            const count = Math.min(4 - header_bytes, chunk.length);
            chunk.copy(header, header_bytes, 0, count);
            header_bytes += count;
            offset += count;
            if (header_bytes < 4) return;
            const length = header.readUInt32BE();
            if (!length || length > MAX_ENVELOPE_BYTES)
              throw new ValueError('executor IPC response exceeds frame limit');
            body = Buffer.alloc(length);
          }
          const count = Math.min(body!.length - body_bytes, chunk.length - offset);
          chunk.copy(body!, body_bytes, offset, offset + count);
          body_bytes += count;
          if (body_bytes !== body!.length) return;
          const decoded = decode_json(body!, 'IpcResponse', { strict: true });
          if (decoded.request_id !== request_id)
            throw new ValueError('executor IPC response identity mismatch');
          if (
            decoded.unconfirmed != null &&
            (decoded.unconfirmed.request_id !== request_id ||
              decoded.unconfirmed.submission_id !== submission_id)
          )
            throw new ValueError('executor IPC publication identity mismatch');
          response = decoded;
          finish();
        } catch (error) {
          finish(error);
        }
      });
      const seconds = transport_deadline - monotonic();
      if (!Number.isFinite(seconds) || seconds <= 0) {
        finish(
          Object.assign(new Error('executor IPC response deadline expired'), {
            name: 'TimeoutError',
          }),
        );
        return;
      }
      timer = setTimeout(
        () => finish(Object.assign(new Error('timed out'), { name: 'TimeoutError' })),
        seconds * 1000,
      );
      socket.connect(socket_path, () => {
        if (finished) return;
        // write can fail after a prefix. Never infer refusal from that failure.
        emission_possible = true;
        socket.write(encoded, (error) => {
          if (error) finish(error);
        });
      });
    });
  } catch (error) {
    if (emission_possible && (operation === 'write' || operation === 'retry'))
      return {
        request_id,
        result: null,
        error: null,
        unconfirmed: {
          request_id,
          submission_id,
          publication: 'unknown',
          acceptance: 'unconfirmed',
        },
      };
    throw error;
  }
}

export async function local_status(
  environment: NodeJS.ProcessEnv = process.env,
): Promise<MacLocalStatus> {
  const response = await request('health', {}, environment);
  if (response.error !== null || response.result === null) {
    const { python_repr } = await import('../protocol_errors.ts');
    throw new ValueError(
      response.error && Object.keys(response.error).length
        ? python_repr(response.error)
        : 'executor health returned nothing',
    );
  }
  return decode_value(response.result, 'MacLocalStatus');
}
