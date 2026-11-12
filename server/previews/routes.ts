import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { PreviewError, base_path, type PreviewRecord } from './contracts.ts';
import { LIFECYCLE_SECONDS } from './config.ts';
import { PreviewService } from './service.ts';
import { PreviewTransport } from './transport.ts';
import { require_preview_ingress } from './ingress.ts';
const result = (record: PreviewRecord | null) =>
  record === null
    ? { preview: null }
    : { ...record, base_path: base_path(record), url: record.public_origin + base_path(record) };
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  const service = new PreviewService(
    services.database,
    services.config.trusted_ingress_canonical_origin ?? '',
    services.config.port,
  );
  const transport = new PreviewTransport(service);
  let timer: NodeJS.Timeout | undefined;
  let unsubscribe: (() => void) | undefined;
  let checking = false;
  const recheck = async (): Promise<void> => {
    if (checking) return;
    checking = true;
    try {
      for (const generation of await service.recheck_all()) transport.revoke(generation);
    } catch (error) {
      app.log.error(error, 'Preview lifecycle inspection refused; retrying');
    } finally {
      checking = false;
    }
  };
  app.addHook('onReady', async () => {
    await service.start();
    unsubscribe = services.changes?.subscribe(() => void recheck());
    timer = setInterval(() => void recheck(), LIFECYCLE_SECONDS * 1000);
    timer.unref();
  });
  app.addHook('onClose', async () => {
    clearInterval(timer);
    unsubscribe?.();
    app.server.off('upgrade', upgrade);
    transport.close();
    await service.close();
  });
  // onClose runs after listener shutdown; terminate WS first so server.close can finish.
  app.addHook('preClose', async () => {
    transport.close();
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
    const ticket_id = ticket(request);
    const body = request.body;
    if (
      body === null ||
      typeof body !== 'object' ||
      Array.isArray(body) ||
      Object.keys(body).length !== 1 ||
      !('port' in body) ||
      !Number.isInteger(body.port)
    )
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
  const upgrade = (
    request: import('node:http').IncomingMessage,
    socket: import('node:stream').Duplex,
    head: Buffer,
  ): void => {
    const path = (request.url ?? '').split('?')[0]!;
    const match = /^\/dev\/tickets\/([^/]+)\/([^/]+)\/(.*)$/.exec(path);
    if (!match) return;
    try {
      require_preview_ingress(request, services.config);
      void transport.websocket(
        request,
        socket,
        head,
        decodeURIComponent(match[1]!),
        decodeURIComponent(match[2]!),
      );
    } catch {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
    }
  };
  app.server.on('upgrade', upgrade);
};
