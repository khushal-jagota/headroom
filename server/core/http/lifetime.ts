import type { FastifyInstance, FastifyRequest } from 'fastify';
import { Readable } from 'node:stream';
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
  #sealed = false;
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
      return scope.run(() => handler.call(this, request, reply));
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
