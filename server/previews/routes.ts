import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices, PreparedAreaRoutes } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import { PreviewError, base_path, type PreviewRecord } from './contracts.ts';
import { LIFECYCLE_SECONDS } from './config.ts';
import { PreviewService } from './service.ts';
import { PreviewTransport } from './transport.ts';
import { require_preview_ingress } from './ingress.ts';
import { install_request_lifetime } from '../core/http/lifetime.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
const result = (record: PreviewRecord | null) =>
  record === null
    ? { preview: null }
    : { ...record, base_path: base_path(record), url: record.public_origin + base_path(record) };
export interface PreparedPreviewRoutes extends PreparedAreaRoutes {
  seal(): void;
  stop(options: StopOptions, http_actual: Promise<void>): Promise<void>;
  settled(): Promise<void>;
}
export function createPreviewRoutes(): PreparedPreviewRoutes {
  let begin: (() => Promise<void>) | undefined;
  let starting: Promise<void> | undefined;
  let producer_sealed = false;
  let seal_registered: (() => void) | undefined;
  let finish_registered: (() => Promise<void>) | undefined;
  let read_registered: (() => Promise<void>) | undefined;
  let stopping: Promise<void> | undefined;
  let actual_stop: Promise<void> | undefined;
  const seal = (): void => {
    producer_sealed = true;
    seal_registered?.();
  };
  const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
    const lifetime = install_request_lifetime(app);
    const service = new PreviewService(
      services.database,
      services.config.trusted_ingress_canonical_origin ?? '',
      services.config.port,
    );
    const transport = new PreviewTransport(service, lifetime);
    let timer: NodeJS.Timeout | undefined;
    let unsubscribe: (() => void) | undefined;
    let checking = false;
    let closing: Promise<void> | undefined;
    let upgrade: ((request: IncomingMessage, socket: Duplex, head: Buffer) => void) | undefined;
    let sealed = false;
    seal_registered = () => {
      if (sealed) return;
      sealed = true;
      service.seal_rechecks();
      const failures: unknown[] = [];
      for (const operation of [
        () => clearInterval(timer),
        () => unsubscribe?.(),
        () => {
          if (upgrade) app.server.off('upgrade', upgrade);
        },
        () => transport.close_websockets(),
      ]) {
        try {
          operation();
        } catch (error) {
          failures.push(error);
        }
      }
      if (failures.length) throw new AggregateError(failures, 'Preview producer shutdown failed');
    };
    finish_registered = () => {
      if (!closing) {
        service.seal();
        closing = Promise.allSettled([
          Promise.resolve().then(() => transport.close()),
          service.settled(),
        ]).then((results) => {
          const failures = results.filter((item) => item.status === 'rejected');
          if (failures.length)
            throw new AggregateError(
              failures.map((item) => item.reason),
              'Preview shutdown failed',
            );
        });
      }
      return closing;
    };
    read_registered = () => service.settled();
    const recheck = (): Promise<void> =>
      lifetime.track(async () => {
        if (producer_sealed || lifetime.sealed) return;
        if (checking) return;
        checking = true;
        try {
          for (const generation of await service.recheck_all()) transport.revoke(generation);
        } catch (error) {
          app.log.error(error, 'Preview lifecycle inspection refused; retrying');
        } finally {
          checking = false;
        }
      });
    begin = async () => {
      if (producer_sealed || lifetime.sealed) throw new Error('Preview runtime is shut down');
      await service.start();
      if (producer_sealed || lifetime.sealed) throw new Error('Preview runtime is shut down');
      unsubscribe = services.changes?.subscribe(() => void recheck());
      timer = setInterval(() => void recheck(), LIFECYCLE_SECONDS * 1000);
      timer.unref();
    };
    app.addHook('onClose', async () => {
      if (!actual_stop)
        throw new Error('Preview close requires the original stop deadline and HTTP settlement');
      await actual_stop;
      // Partial app registration can publish an inert owner after stop began.
      await finish_registered!();
    });
    // onClose runs after listener shutdown; terminate WS first so server.close can finish.
    app.addHook('preClose', async () => {
      seal();
    });
    app.setErrorHandler((error, _request, reply) => {
      if (error instanceof PreviewError) {
        reply
          .code(error.status_code)
          .send({ error: { code: 'preview_unavailable', message: error.message, detail: {} } });
        return;
      }
      throw error;
    });
    const ticket = (request: Parameters<AreaServices['identity']>[0]): string => {
      const principal = services.identity(request).principal;
      if (principal.kind !== 'ticket')
        throw new PlannerError(
          'agent_forbidden',
          'preview commands require your Ticket Worker identity',
        );
      return principal.id;
    };
    app.post('/api/previews/prepare', async (request) =>
      result(await service.prepare(ticket(request))),
    );
    app.post('/api/previews/register', async (request) => {
      const body = request.body;
      if (body === undefined || body === null)
        throw new HttpError(422, [
          { type: 'missing', loc: ['body'], msg: 'Field required', input: null },
        ]);
      if (typeof body !== 'object' || Array.isArray(body))
        throw new HttpError(422, [
          {
            type: 'dict_type',
            loc: ['body'],
            msg: 'Input should be a valid dictionary',
            input: body,
          },
        ]);
      const ticket_id = ticket(request);
      if (Object.keys(body).length !== 1 || !('port' in body) || !Number.isInteger(body.port))
        throw new PlannerError('validation', 'register requires one integer port');
      return result(await service.register(ticket_id, body.port as number));
    });
    app.get('/api/previews', async (request) => result(await service.show(ticket(request))));
    app.delete('/api/previews', async (request) => {
      const record = await service.stop(ticket(request));
      if (record) transport.revoke(record.generation);
      return result(record);
    });
    app.route<{ Params: { ticket_id: string; generation: string; '*': string } }>({
      method: ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
      url: '/dev/tickets/:ticket_id/:generation/*',
      config: { uncompressed: true },
      onRequest: async (request, reply) => {
        require_preview_ingress(request.raw, services.config);
        reply.hijack();
        await transport.http(
          request.raw,
          reply.raw,
          request.params.ticket_id,
          request.params.generation,
        );
      },
      handler: async () => {},
    });
    upgrade = (
      request: import('node:http').IncomingMessage,
      socket: import('node:stream').Duplex,
      head: Buffer,
    ): void => {
      const path = (request.url ?? '').split('?')[0]!;
      const match = /^\/dev\/tickets\/([^/]+)\/([^/]+)\/(.*)$/.exec(path);
      if (!match) return;
      try {
        require_preview_ingress(request, services.config);
        void transport
          .websocket(
            request,
            socket,
            head,
            decodeURIComponent(match[1]!),
            decodeURIComponent(match[2]!),
          )
          .catch(() => socket.destroy());
      } catch {
        socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      }
    };
    if (producer_sealed) seal_registered();
    else app.server.on('upgrade', upgrade);
  };
  return {
    routes,
    seal,
    stop(options, http_actual) {
      if (stopping) return stopping;
      const { promise, resolve, reject } = Promise.withResolvers<void>();
      actual_stop = promise;
      stopping = observe_shutdown(
        actual_stop,
        options,
        new ShutdownDeadlineExceeded(['preview services']).message,
        ['preview services'],
      );
      const failures: unknown[] = [];
      try {
        seal();
      } catch (error) {
        failures.push(error);
      }
      void (async () => {
        const http = await Promise.allSettled([http_actual]);
        for (const result of http) if (result.status === 'rejected') failures.push(result.reason);
        try {
          await finish_registered?.();
        } catch (error) {
          failures.push(error);
        }
        if (failures.length) throw new AggregateError(failures, 'Preview shutdown failed');
      })().then(resolve, reject);
      return stopping;
    },
    settled() {
      return actual_stop ?? read_registered?.() ?? Promise.resolve();
    },
    start() {
      if (starting) return starting;
      if (!begin) throw new Error('Preview routes are not registered');
      starting = Promise.resolve().then(() => begin!());
      return starting;
    },
  };
}
