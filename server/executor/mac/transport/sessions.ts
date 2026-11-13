/** One native agent per loop. Request ownership includes producer and response settlement. */
import http from 'node:http';
import https from 'node:https';
import type { ClientRequest, IncomingMessage } from 'node:http';
import type { Socket } from 'node:net';
import { ValueError } from '../../protocol.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { HTTP_CONNECT_TIMEOUT_SECONDS } from '../configuration.ts';
import type {
  ConnectorTable,
  HttpRequest,
  MacHttpOptions,
  MacLoop,
  ReplyReader,
} from './contracts.ts';
import { HttpTimeout } from './errors.ts';
interface Session {
  readonly agent: http.Agent;
  readonly sockets: Map<Socket, Promise<void>>;
  readonly requests: Set<ClientRequest>;
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
    if (count > limit) throw new ValueError('executor response exceeds its size bound');
    chunks.push(block);
  }
  return Buffer.concat(chunks, count);
}
export class HttpSessions {
  private readonly sessions = new Map<MacLoop, Session>();
  private readonly closing = new Set<Promise<void>>();
  private closed = false;
  readonly origin: string;
  private readonly connectors: ConnectorTable;
  private readonly options: MacHttpOptions;
  constructor(origin: string, connectors: ConnectorTable, options: MacHttpOptions = {}) {
    this.origin = new URL(origin).origin;
    this.connectors = connectors;
    this.options = options;
  }
  private session(loop: MacLoop): Session {
    if (this.closed) throw new ServerLifecycleError('Mac HTTP transport is stopped.');
    let session = this.sessions.get(loop);
    if (session) return session;
    const settings = this.connectors[loop],
      Agent = this.origin.startsWith('https:') ? https.Agent : http.Agent;
    session = {
      agent: new Agent({
        keepAlive: !settings.force_close,
        maxSockets: settings.limit || Infinity,
        maxFreeSockets: settings.limit || 256,
        ...(this.origin.startsWith('https:') ? this.options.tls : {}),
      }),
      sockets: new Map(),
      requests: new Set(),
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
    const operation = this.perform(session, request, headers, consume, resolve, reject);
    session.actual.add(operation);
    void operation.then(
      (value) => {
        resolve(value);
        session.actual.delete(operation);
      },
      (error: unknown) => {
        reject(error);
        session.actual.delete(operation);
      },
    );
    return reply;
  }
  private async perform<T>(
    session: Session,
    input: HttpRequest,
    headers: Readonly<Record<string, string>>,
    consume: ReplyReader<T>,
    resolve: (value: T) => void,
    reject: (error: unknown) => void,
  ): Promise<T> {
    const target = new URL(input.path, this.origin);
    if (target.origin !== this.origin)
      throw new Error('Executor request leaves configured origin.');
    const transport = target.protocol === 'https:' ? https : http;
    const request = transport.request(target, {
      method: input.method,
      agent: session.agent,
      headers: {
        ...headers,
        ...input.headers,
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
    request.once('error', reject);
    const result = new Promise<T>((resolve, reject) => {
      request.once('error', reject);
      request.once('response', (incoming) => {
        response = incoming;
        consumer = consume(incoming);
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
            throw new ValueError('executor request exceeds its length');
          progress();
          await new Promise<void>((resolve, reject) =>
            request.write(chunk, (error) => (error ? reject(error) : resolve())),
          );
        }
        if (input.byte_count !== undefined && count !== input.byte_count)
          throw new ValueError('executor request is short');
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
