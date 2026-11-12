import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import type { FastifyPluginAsync, FastifyReply, FastifyRequest } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import type {
  CurrentAuthority,
  CurrentViewSnapshot,
  ViewRequest,
} from '../../web/src/lib/navigation/contracts.ts';
import type { NavigationAuthority } from './contracts.ts';
import { ScopedDelivery } from './delivery/runtime.ts';
import { SnapshotFences } from './delivery/snapshot_fence.ts';
import { DELIVERY } from './delivery/configuration.ts';
import { parseView } from './logic/view.ts';
import { NavigationUnavailable } from './logic/errors.ts';
import { jsonFailure } from '../core/http/json.ts';
import { scalarQueries } from '../list_reads/http.ts';
import { validateInstallation } from './publication.ts';
import { snapshot } from './data/read.ts';
function query(
  request: FastifyRequest,
  reply: FastifyReply,
  names: string[],
): Record<string, string> | null {
  const values = scalarQueries(request.query as Record<string, unknown>);
  const absent = names.filter((name) => values[name] === undefined);
  if (absent.length) {
    reply.code(422).send({
      detail: absent.map((name) => ({
        type: 'missing',
        loc: ['query', name],
        msg: 'Field required',
        input: null,
      })),
    });
    return null;
  }
  return values as Record<string, string>;
}
function view(request: FastifyRequest, reply: FastifyReply): ViewRequest | null {
  const value = query(request, reply, ['view']);
  if (!value) return null;
  if (Buffer.byteLength(value.view) > 128 * 1024) {
    reply.code(413).send({ detail: 'navigation view request is too large' });
    return null;
  }
  try {
    return parseView(JSON.parse(value.view));
  } catch (error) {
    let detail = (error as Error).message;
    if (error instanceof SyntaxError) {
      const failure = jsonFailure(value.view);
      const before = [...value.view].slice(0, failure.position).join('');
      const line = before.split('\n').length,
        column = [...before.slice(before.lastIndexOf('\n') + 1)].length + 1;
      detail = `${failure.message}: line ${line} column ${column} (char ${failure.position})`;
    }
    reply.code(400).send({ detail });
    return null;
  }
}
function expectedSnapshotFailure(error: unknown): boolean {
  return (
    error instanceof NavigationUnavailable ||
    error instanceof SyntaxError ||
    error instanceof TypeError ||
    error instanceof RangeError ||
    (error instanceof Error &&
      (error.constructor === Error ||
        String((error as Error & { code?: string }).code ?? '').startsWith('SQLITE_')))
  );
}
function capture(
  authority: NavigationAuthority,
  services: AreaServices,
  value: ViewRequest,
  captured?: CurrentAuthority,
): { current: CurrentViewSnapshot; token: CurrentAuthority } {
  let token: CurrentAuthority;
  try {
    token = captured ?? authority.capture();
    authority.validate(token);
  } catch (error) {
    throw new NavigationUnavailable((error as Error).message);
  }
  try {
    const current = services.database.read((sql) => {
      validateInstallation(sql, token);
      return snapshot(sql, value, token);
    });
    try {
      authority.validate(token);
    } catch (error) {
      throw new NavigationUnavailable((error as Error).message);
    }
    return { current, token };
  } catch (error) {
    if (!expectedSnapshotFailure(error)) throw error;
    throw new NavigationUnavailable((error as Error).message);
  }
}
export function createRoutes(): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    const delivery = new ScopedDelivery(services.database);
    const fences = new SnapshotFences(app);
    app.addHook('onReady', async () => {
      delivery.start();
    });
    app.addHook('onClose', async () => {
      delivery.close();
    });
    app.get('/snapshot', async (request, reply) => {
      const value = view(request, reply);
      if (!value) return;
      const authority: NavigationAuthority | undefined = services.signalAuthority;
      if (!authority) return reply.code(503).send({ detail: 'navigation view is unavailable' });
      try {
        const { current, token } = capture(authority, services, value);
        fences.hold(request, reply, authority, token);
        return reply.header('cache-control', 'no-store').send(current);
      } catch (error) {
        if (!(error instanceof NavigationUnavailable)) throw error;
        return reply.code(503).send({ detail: 'navigation view is unavailable' });
      }
    });
    app.get('/events', async (request, reply) => {
      const value = view(request, reply);
      if (!value) return;
      const authority: NavigationAuthority | undefined = services.signalAuthority;
      if (!authority) return reply.code(503).send({ detail: 'navigation view is unavailable' });
      let token: CurrentAuthority;
      try {
        token = authority.capture();
        authority.validate(token);
      } catch {
        return reply.code(503).send({ detail: 'navigation view is unavailable' });
      }
      // As in Python, only authority is captured before stream headers. The initial
      // scoped read is lazy. An invalid durable view then closes the started stream.
      reply.hijack();
      reply.raw.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Accel-Buffering': 'no',
      });
      delivery.open(
        reply.raw,
        authority,
        token,
        value,
        () => capture(authority, services, value, token).current,
      );
    });
    app.get('/content', async (request, reply) => {
      const value = query(request, reply, ['kind', 'identity', 'version']);
      if (!value) return;
      if (
        !['ticket', 'manager'].includes(value.kind) ||
        [...value.identity].length > 256 ||
        value.version.length !== 64
      )
        return reply.code(400).send({ detail: 'invalid recap reference' });
      const row = services.database.read((sql) =>
        sql.get<{ value: string }>('SELECT value FROM navigation_records WHERE kind=? AND id=?', [
          value.kind,
          value.identity,
        ]),
      );
      if (!row) return reply.code(409).send({ detail: 'recap owner is absent' });
      const body = String((JSON.parse(row.value) as { recap: string }).recap);
      if (createHash('sha256').update(body).digest('hex') !== value.version)
        return reply.code(409).send({ detail: 'recap source changed' });
      // Python slices Unicode scalars, with four-byte characters bounded by the chunk budget.
      function* chunks(): Generator<string> {
        let chunk = '',
          count = 0;
        for (const scalar of body) {
          chunk += scalar;
          count += 1;
          if (count === DELIVERY.contentChunkBytes / 4) {
            yield chunk;
            chunk = '';
            count = 0;
          }
        }
        if (chunk) yield chunk;
      }
      return reply
        .header('cache-control', 'no-store')
        .header('etag', value.version)
        .type('text/plain; charset=utf-8')
        .send(Readable.from(chunks()));
    });
  };
}
