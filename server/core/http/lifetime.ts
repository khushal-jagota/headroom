import type { FastifyInstance, FastifyRequest } from 'fastify';
import { Readable } from 'node:stream';
import type { Socket } from 'node:net';
import type { ServerResponse } from 'node:http';
import { HttpError } from './errors.ts';

declare module 'fastify' {
  interface FastifyInstance {
    request_lifetime?: HttpRequestScope;
  }
  interface FastifyRequest {
    request_lifetime?: HttpRequestScope | null;
  }
}

/** Socket/response completion never substitutes for actual handler/iterator work. */
export class HttpRequestScope {
  readonly #pending = new Set<Promise<void>>();
  readonly #connections = new Map<Socket, number>();
  #sealed = false;
  own_connection(socket: Socket): void {
    this.#connections.set(socket, 0);
    socket.once('close', () => this.#connections.delete(socket));
  }
  /** Include raw Fastify refusals and pipelined replies, before any handler runs. */
  own_response(socket: Socket, response: ServerResponse): void {
    const count = this.#connections.get(socket);
    if (count === undefined) return;
    this.#connections.set(socket, count + 1);
    let completed = false;
    const finish = () => {
      if (completed) return;
      completed = true;
      const remaining = this.#connections.get(socket);
      if (remaining === undefined) return;
      this.#connections.set(socket, remaining - 1);
      if (this.#sealed) this.close_idle_connections();
    };
    response.once('finish', finish);
    response.once('close', finish);
  }
  /** end() flushes completed responses; active and queued responses retain their socket. */
  close_idle_connections(): void {
    for (const [socket, unfinished] of this.#connections)
      if (unfinished === 0 && !socket.destroyed && !socket.writableEnded) socket.end();
  }
  /** Only sockets accepted by this invocation, including upgraded connections. */
  force_close_connections(): void {
    for (const socket of this.#connections.keys()) socket.destroy();
  }
  get sealed(): boolean {
    return this.#sealed;
  }
  seal(): void {
    this.#sealed = true;
  }
  run<T>(operation: () => T | PromiseLike<T>): Promise<T> {
    if (this.#sealed) return Promise.reject(new HttpError(503, 'the server is shutting down'));
    return this.track(operation);
  }
  /** An already-owned callback can resume after seal. Retain it until it actually returns. */
  track<T>(operation: () => T | PromiseLike<T>): Promise<T> {
    let release!: () => void;
    const actual = new Promise<void>((resolve) => {
      release = resolve;
    });
    this.#pending.add(actual);
    const finish = () => {
      this.#pending.delete(actual);
      release();
    };
    try {
      const result = Promise.resolve(operation());
      void result.then(finish, finish);
      return result;
    } catch (error) {
      finish();
      return Promise.reject(error);
    }
  }
  iterator<T>(source: AsyncIterable<T>): AsyncIterable<T> {
    const scope = this;
    return {
      [Symbol.asyncIterator]() {
        const iterator = source[Symbol.asyncIterator]();
        return {
          next: (...args: [] | [undefined]) =>
            scope.sealed
              ? scope.track(
                  () =>
                    iterator.return?.() ??
                    Promise.resolve({ done: true as const, value: undefined }),
                )
              : scope.track(() => iterator.next(...args)),
          return: () =>
            scope.track(
              () =>
                iterator.return?.() ?? Promise.resolve({ done: true as const, value: undefined }),
            ),
          throw: (error: unknown) =>
            scope.track(() => (iterator.throw ? iterator.throw(error) : Promise.reject(error))),
        };
      },
    };
  }
  async settled(): Promise<void> {
    while (this.#pending.size) await Promise.all([...this.#pending]);
  }
}

export function install_request_lifetime(
  app: FastifyInstance,
  supplied?: HttpRequestScope,
): HttpRequestScope {
  if (app.request_lifetime) return app.request_lifetime;
  const scope = supplied ?? new HttpRequestScope();
  app.server.on('connection', (socket) => scope.own_connection(socket));
  app.server.prependListener('request', (request, response) =>
    scope.own_response(request.socket, response),
  );
  // Node's parser-idle list can include buffered response output. Both its
  // standard close() and Fastify must use completed owned responses instead.
  app.server.closeIdleConnections = () => scope.close_idle_connections();
  app.decorate('request_lifetime', scope);
  app.decorateRequest('request_lifetime', null);
  app.addHook('onRequest', async (request, reply) => {
    if (scope.sealed) {
      reply.code(503).send({ detail: 'the server is shutting down' });
      return;
    }
    request.request_lifetime = scope;
    void scope.track(
      () =>
        new Promise<void>((resolve) => {
          reply.raw.once('finish', resolve);
          reply.raw.once('close', resolve);
        }),
    );
  });
  app.addHook('onRoute', (route) => {
    const handler = route.handler;
    route.handler = function (request, reply) {
      const operation = () => handler.call(this, request, reply);
      return request.request_lifetime === scope ? scope.track(operation) : scope.run(operation);
    };
  });
  app.addHook('onSend', async (_request, _reply, payload) => {
    if (payload instanceof Readable) {
      void scope.track(
        () =>
          new Promise<void>((resolve) => {
            if (payload.closed) resolve();
            else {
              payload.once('end', resolve);
              payload.once('close', resolve);
            }
          }),
      );
    }
    return payload;
  });
  app.addHook('preClose', async () => {
    scope.seal();
  });
  return scope;
}
