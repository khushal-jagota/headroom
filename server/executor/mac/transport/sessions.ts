/** One native agent per loop. Request ownership includes producer and response settlement. */
import http from 'node:http';
import WebSocket from 'ws';
import {
  MOVE_NUMBER_HEADER,
  SERVER_ID_HEADER,
  SERVER_LOCATION_HEADER,
  SERVER_LOCATION_PATH,
} from '../../../server_link/contracts.ts';
import { local_server_address } from '../../../server_link/logic.ts';
import https from 'node:https';
import type { ClientRequest, IncomingMessage } from 'node:http';
import type { Socket } from 'node:net';
import { ExecutorValidationError } from '../../protocol.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { HTTP_CONNECT_TIMEOUT_SECONDS } from '../configuration.ts';
import type {
  ConnectorTable,
  HttpRequest,
  MacHttpOptions,
  MacLoop,
  ReplyReader,
} from './contracts.ts';
import { HttpTimeout, http_error } from './errors.ts';
import { ExecutorLinkAgent } from './executor_link.ts';
/** Requests to the local link name `localhost`; the agent connects to the socket. */
function request_origin(address: string): string {
  return local_server_address(address) === null ? new URL(address).origin : 'http://localhost';
}
interface Session {
  /** The server address this session's agent connects to. */
  readonly address: string;
  readonly agent: http.Agent;
  readonly sockets: Map<Socket, Promise<void>>;
  readonly requests: Set<ClientRequest>;
  readonly websockets: Set<WebSocket>;
  readonly actual: Set<Promise<unknown>>;
  readonly idle: Map<Socket, ReturnType<typeof setTimeout>>;
  readonly force_close: boolean;
  closing: boolean;
}
export async function read_bounded(response: IncomingMessage, limit: number): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let count = 0;
  for await (const chunk of response) {
    const block = Buffer.from(chunk as Uint8Array);
    count += block.length;
    if (count > limit)
      throw new ExecutorValidationError('executor response exceeds its size bound');
    chunks.push(block);
  }
  return Buffer.concat(chunks, count);
}
export async function read_server_location_probe(incoming: IncomingMessage): Promise<unknown> {
  const body = await read_bounded(incoming, 16384);
  if (incoming.statusCode !== 200 && incoming.statusCode !== 503)
    throw http_error(incoming.statusCode ?? 0, body);
  let location: unknown;
  try {
    location = JSON.parse(body.toString('utf8')) as unknown;
  } catch {
    if (incoming.statusCode === 503) throw http_error(503, body);
    throw new ExecutorValidationError('The server identity probe is invalid.');
  }
  if (incoming.statusCode === 503 && (location as { state?: unknown } | null)?.state !== 'moved')
    throw http_error(503, body);
  return location;
}
export class HttpSessions {
  private readonly sessions = new Map<MacLoop, Session>();
  private readonly closing = new Set<Promise<void>>();
  private closed = false;
  private readonly initial_address: string;
  /** The current server address: an HTTPS origin, or the local link's socket path. */
  get address(): string {
    return this.options.server_link?.current_url ?? this.initial_address;
  }
  get origin(): string {
    return request_origin(this.address);
  }
  private readonly connectors: ConnectorTable;
  private readonly options: MacHttpOptions;
  constructor(address: string, connectors: ConnectorTable, options: MacHttpOptions = {}) {
    this.initial_address = local_server_address(address) ?? new URL(address).origin;
    this.connectors = connectors;
    this.options = options;
  }
  async websocket(path: string, headers: Readonly<Record<string, string>>): Promise<WebSocket> {
    const session = this.session('reads');
    const address = this.address;
    const target = new URL(path, this.origin);
    target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:';
    const current = this.options.server_link?.current();
    const socket = new WebSocket(target, {
      agent: session.agent,
      perMessageDeflate: false,
      maxPayload: 65540,
      handshakeTimeout: HTTP_CONNECT_TIMEOUT_SECONDS * 1000,
      headers: {
        ...headers,
        ...(current
          ? {
              [SERVER_ID_HEADER]: current.server_id!,
              [MOVE_NUMBER_HEADER]: String(current.highest_move_number),
            }
          : {}),
      },
    });
    session.websockets.add(socket);
    const actual = new Promise<void>((resolve) =>
      socket.once('close', () => {
        session.websockets.delete(socket);
        resolve();
      }),
    );
    session.actual.add(actual);
    void actual.finally(() => session.actual.delete(actual));
    socket.on('error', () => {});
    socket.once('upgrade', (response) => {
      try {
        this.options.server_link?.verify(response.headers[SERVER_LOCATION_HEADER], address);
      } catch {
        socket.terminate();
      }
    });
    try {
      await new Promise<void>((resolve, reject) => {
        socket.once('open', resolve);
        socket.once('error', reject);
        socket.once('close', () => reject(new Error('Preview channel closed before acceptance')));
      });
      return socket;
    } catch (error) {
      socket.terminate();
      await actual;
      throw error;
    }
  }
  private session(loop: MacLoop): Session {
    if (this.closed) throw new ServerLifecycleError('Mac HTTP transport is stopped.');
    let session = this.sessions.get(loop);
    if (session) return session;
    const address = this.address,
      link_socket = local_server_address(address),
      settings = this.connectors[loop],
      Agent = this.origin.startsWith('https:') ? https.Agent : http.Agent;
    const agent_options: https.AgentOptions = {
      keepAlive: !settings.force_close,
      maxSockets: settings.limit || Infinity,
      maxFreeSockets: settings.limit || 256,
      ...(this.origin.startsWith('https:') ? this.options.tls : {}),
      // Explicit true also overrides NODE_TLS_REJECT_UNAUTHORIZED=0.
      rejectUnauthorized: true,
    };
    session = {
      address,
      agent:
        link_socket === null
          ? new Agent(agent_options)
          : new ExecutorLinkAgent(link_socket, agent_options),
      sockets: new Map(),
      requests: new Set(),
      websockets: new Set(),
      actual: new Set(),
      idle: new Map(),
      force_close: settings.force_close,
      closing: false,
    };
    const retained = session;
    // Python's keepalive timeout measures idle time, not a socket's total lifetime.
    retained.agent.on('free', (socket: Socket) => {
      if (retained.closing) socket.destroy();
      else if (settings.keepalive_timeout !== null) {
        clearTimeout(retained.idle.get(socket));
        retained.idle.set(
          socket,
          setTimeout(() => socket.destroy(), settings.keepalive_timeout * 1000),
        );
      }
    });
    this.sessions.set(loop, session);
    return session;
  }
  private retain<T>(session: Session, operation: Promise<T>): Promise<T> {
    session.actual.add(operation);
    void operation.then(
      () => session.actual.delete(operation),
      () => session.actual.delete(operation),
    );
    return operation;
  }
  /** File work shares the captured session's settlement without an HTTP deadline. */
  async own<T>(loop: MacLoop, work: (require_open: () => void) => Promise<T>): Promise<T> {
    const session = this.session(loop);
    const require_open = () => {
      if (this.closed || session.closing || this.sessions.get(loop) !== session)
        throw new ServerLifecycleError('Mac HTTP session is stopped or replaced.');
    };
    return this.retain(
      session,
      Promise.resolve().then(() => {
        require_open();
        return work(require_open);
      }),
    );
  }
  request<T>(
    request: HttpRequest,
    headers: Readonly<Record<string, string>>,
    consume: ReplyReader<T>,
  ): Promise<T> {
    let session: Session;
    try {
      request.signal?.throwIfAborted();
      session = this.session(request.loop);
    } catch (error) {
      return Promise.reject(error);
    }
    let resolve!: (value: T) => void, reject!: (error: unknown) => void;
    const reply = new Promise<T>((yes, no) => {
      resolve = yes;
      reject = no;
    });
    let failure_marked = false;
    const note_failure = () => {
      if (!failure_marked && !this.closed && !session.closing && !request.signal?.aborted) {
        failure_marked = true;
        this.options.server_link?.request_failed();
      }
    };
    const operation = this.retain(
      session,
      this.perform(session, request, headers, consume, resolve, reject, note_failure),
    );
    void operation.then(resolve, (error: unknown) => {
      note_failure();
      reject(error);
    });
    return reply;
  }
  private async perform<T>(
    session: Session,
    input: HttpRequest,
    headers: Readonly<Record<string, string>>,
    consume: ReplyReader<T>,
    resolve: (value: T) => void,
    reject: (error: unknown) => void,
    note_failure: () => void,
  ): Promise<T> {
    const probe = async (origin: string): Promise<unknown> => {
      // A separate retained session avoids a limit-one lease reply blocking its own probe.
      const owner = new HttpSessions(origin, this.connectors, { tls: this.options.tls });
      try {
        return await owner.request(
          {
            loop: 'lease',
            method: 'GET',
            path: SERVER_LOCATION_PATH,
            timeout: HTTP_CONNECT_TIMEOUT_SECONDS,
            signal: input.signal,
          },
          { 'Accept-Encoding': 'identity' },
          read_server_location_probe,
        );
      } finally {
        await owner.close();
      }
    };
    const link = this.options.server_link;
    const accepted = link?.needs_probe() ? await link.prepare(probe) : link?.current();
    const current = link?.current();
    if (
      accepted &&
      current &&
      (accepted.server_id !== current.server_id ||
        accepted.highest_move_number !== current.highest_move_number ||
        accepted.server_url !== current.server_url)
    )
      throw new ExecutorValidationError('The server authority changed before this request.');
    if (this.closed || session.closing)
      throw new ServerLifecycleError('Machine HTTP session is stopped.');
    const origin = accepted?.server_url ?? this.address;
    if (local_server_address(origin) !== local_server_address(session.address)) {
      // The address moved between the local link and HTTPS. Fresh sessions get the right agent.
      void this.reset();
      throw new ServerLifecycleError('The server address changed; the HTTP sessions restart.');
    }
    const target = new URL(input.path, request_origin(origin));
    if (target.origin !== request_origin(origin))
      throw new Error('Executor request leaves configured origin.');
    const transport = target.protocol === 'https:' ? https : http;
    const request = transport.request(target, {
      method: input.method,
      agent: session.agent,
      headers: {
        ...headers,
        ...input.headers,
        ...(accepted
          ? {
              [SERVER_ID_HEADER]: accepted.server_id!,
              [MOVE_NUMBER_HEADER]: String(accepted.highest_move_number),
            }
          : {}),
        ...(session.force_close ? { Connection: 'close' } : {}),
      },
    });
    session.requests.add(request);
    let response: IncomingMessage | undefined, consumer: Promise<T> | undefined;
    let read_socket: Socket | undefined;
    const read_timeout = () => fail(new HttpTimeout());
    const closed = new Promise<void>((resolve) => request.once('close', resolve));
    let connect: ReturnType<typeof setTimeout> | undefined,
      total: ReturnType<typeof setTimeout> | undefined,
      stall: ReturnType<typeof setTimeout> | undefined;
    const fail = (error: Error) => {
      note_failure();
      // An early response can already resolve result while its producer waits.
      // Reject the public waiter independently of that inner response promise.
      reject(error);
      request.destroy(error);
      response?.destroy(error);
    };
    const progress = () => {
      if (input.stall_timeout !== undefined) {
        clearTimeout(stall);
        stall = setTimeout(() => fail(new HttpTimeout()), input.stall_timeout * 1000);
      }
    };
    progress();
    const abort = () =>
      fail(
        input.signal!.reason instanceof Error
          ? input.signal!.reason
          : new DOMException('Aborted', 'AbortError'),
      );
    input.signal?.addEventListener('abort', abort, { once: true });
    if (input.timeout !== undefined)
      total = setTimeout(() => fail(new HttpTimeout()), input.timeout * 1000);
    request.once('socket', (socket) => {
      socket.setTimeout(0);
      clearTimeout(session.idle.get(socket));
      session.idle.delete(socket);
      if (!session.sockets.has(socket)) {
        const actual = new Promise<void>((resolve) =>
          socket.once('close', () => {
            clearTimeout(session.idle.get(socket));
            session.idle.delete(socket);
            session.sockets.delete(socket);
            resolve();
          }),
        );
        session.sockets.set(socket, actual);
      }
      if (session.closing) socket.destroy();
      if (socket.connecting) {
        connect = setTimeout(() => fail(new HttpTimeout()), HTTP_CONNECT_TIMEOUT_SECONDS * 1000);
        socket.once(target.protocol === 'https:' ? 'secureConnect' : 'connect', () =>
          clearTimeout(connect),
        );
      }
      if (input.read_timeout !== undefined) {
        read_socket = socket;
        socket.setTimeout(input.read_timeout * 1000);
        socket.once('timeout', read_timeout);
      }
    });
    request.once('error', (error) => {
      note_failure();
      reject(error);
    });
    const result = new Promise<T>((resolve, reject) => {
      request.once('error', reject);
      request.once('response', (incoming) => {
        response = incoming;
        if ((incoming.statusCode ?? 0) >= 400) note_failure();
        consumer = (async () => {
          // A proxy can answer while the server is down. This is no authority to consume
          // work, but its temporary availability status must remain retryable.
          if (
            link &&
            incoming.headers[SERVER_LOCATION_HEADER] === undefined &&
            [502, 503, 504].includes(incoming.statusCode ?? 0)
          )
            throw http_error(incoming.statusCode!, await read_bounded(incoming, 16384));
          if (link) {
            await link.accept(incoming.headers[SERVER_LOCATION_HEADER], origin, probe);
            link.verify(incoming.headers[SERVER_LOCATION_HEADER], origin);
          }
          return consume(incoming);
        })();
        void consumer.then(resolve, (error: unknown) => {
          fail(error instanceof Error ? error : new Error(String(error)));
          reject(error);
        });
      });
    });
    // The rejection handler is installed before a producer can encounter a native failure.
    void result.catch(() => {});
    const producer = (async () => {
      const body = input.body;
      if (body && Symbol.asyncIterator in body) {
        let count = 0;
        for await (const chunk of body) {
          if (request.destroyed) throw new DOMException('Request closed', 'AbortError');
          count += chunk.length;
          if (input.byte_count !== undefined && count > input.byte_count)
            throw new ExecutorValidationError('executor request exceeds its length');
          progress();
          await new Promise<void>((resolve, reject) =>
            request.write(chunk, (error) => (error ? reject(error) : resolve())),
          );
        }
        if (input.byte_count !== undefined && count !== input.byte_count)
          throw new ExecutorValidationError('executor request is short');
        request.end();
      } else request.end(body);
    })();
    void producer.catch((error: unknown) =>
      fail(error instanceof Error ? error : new Error(String(error))),
    );
    try {
      const value = await result;
      await producer;
      resolve(value);
      return value;
    } catch (error) {
      // A route timeout ends its waiter. Actual producers/readers remain owned
      // below, so a host shutdown cannot mistake cancellation for cleanup.
      reject(error);
      throw error;
    } finally {
      clearTimeout(connect);
      clearTimeout(total);
      clearTimeout(stall);
      read_socket?.removeListener('timeout', read_timeout);
      input.signal?.removeEventListener('abort', abort);
      if (!response?.complete) {
        response?.destroy();
        request.destroy();
      }
      await Promise.allSettled([producer, consumer, closed]);
      session.requests.delete(request);
    }
  }
  reset(): Promise<void> {
    const sessions = [...this.sessions.values()];
    this.sessions.clear();
    const actual = (async () => {
      for (const session of sessions) {
        session.closing = true;
        for (const timer of session.idle.values()) clearTimeout(timer);
        session.idle.clear();
        for (const request of session.requests)
          request.destroy(new DOMException('HTTP sessions reset', 'AbortError'));
        for (const socket of session.websockets) socket.terminate();
        session.agent.destroy();
      }
      for (const session of sessions) {
        await Promise.allSettled([...session.actual]);
        await Promise.all([...session.sockets.values()]);
      }
    })();
    this.closing.add(actual);
    void actual.then(
      () => this.closing.delete(actual),
      () => this.closing.delete(actual),
    );
    return actual;
  }
  close(): Promise<void> {
    this.closed = true;
    void this.reset();
    return this.settled();
  }
  async settled(): Promise<void> {
    await Promise.all([...this.closing]);
  }
}
