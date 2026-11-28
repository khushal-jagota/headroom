import type { LiveSink } from '../live_updates/contracts.ts';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices, PreparedAreaRoutes } from '../core/authority/register.ts';
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
import { HttpError, log_server_error } from '../core/http/errors.ts';
import { scalarQueries } from '../list_reads/http.ts';
import { validateInstallation } from './publication.ts';
import { install_request_lifetime } from '../core/http/lifetime.ts';
import { snapshot } from './data/read.ts';
function query(values: Record<string, string>, names: string[]): Record<string, string> {
  const absent = names.filter((name) => values[name] === undefined);
  if (absent.length)
    throw new HttpError(
      422,
      absent.map((name) => ({
        type: 'missing',
        loc: ['query', name],
        msg: 'Field required',
        input: null,
      })),
    );
  return values;
}
function view(raw: Record<string, unknown>): ViewRequest {
  const value = query(scalarQueries(raw) as Record<string, string>, ['view']);
  if (Buffer.byteLength(value.view) > 128 * 1024)
    throw new HttpError(413, 'navigation view request is too large');
  try {
    return parseView(JSON.parse(value.view));
  } catch (error) {
    throw new HttpError(400, (error as Error).message);
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
export function createRoutes(): PreparedAreaRoutes {
  let begin: (() => void) | undefined;
  let started = false;
  const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
    const delivery = new ScopedDelivery(services.database, install_request_lifetime(app));
    const fences = new SnapshotFences(app);
    let closed = false;
    begin = () => {
      if (closed) throw new Error('Navigation delivery is shut down');
      delivery.start();
    };
    app.addHook('onClose', async () => {
      closed = true;
      delivery.close();
    });
    app.addHook('preClose', async () => {
      closed = true;
      delivery.close();
    });
    app.get('/snapshot', async (request, reply) => {
      const value = view(request.query as Record<string, unknown>);
      const authority: NavigationAuthority | undefined = services.signalAuthority;
      if (!authority) return reply.code(503).send({ detail: 'navigation view is unavailable' });
      try {
        const { current, token } = capture(authority, services, value);
        fences.hold(request, reply, authority, token);
        return reply.header('cache-control', 'no-store').send(current);
      } catch (error) {
        if (!(error instanceof NavigationUnavailable)) throw error;
        log_server_error(request, 503, error);
        return reply.code(503).send({ detail: 'navigation view is unavailable' });
      }
    });
    /** Authority comes first, before any stream header. A failure is the old route's answer. */
    const begin_events = () => {
      const authority: NavigationAuthority | undefined = services.signalAuthority;
      if (!authority) throw new HttpError(503, 'navigation view is unavailable');
      try {
        const token = authority.capture();
        authority.validate(token);
        return { authority, token };
      } catch (error) {
        console.error('navigation view is unavailable', error);
        throw new HttpError(503, 'navigation view is unavailable');
      }
    };
    const open_events = (
      value: ViewRequest,
      { authority, token }: ReturnType<typeof begin_events>,
      sink: LiveSink,
    ) =>
      // As in Python, only authority is captured before stream headers. The initial
      // scoped read is lazy. An invalid durable view then closes the started stream.
      delivery.open(
        sink,
        authority,
        token,
        value,
        () => capture(authority, services, value, token).current,
      );
    services.live_subscriptions?.register('navigation', (request, sink) => {
      const value = view(request.params);
      return open_events(value, begin_events(), sink);
    });
    app.get('/content', async (request, reply) => {
      const value = query(
        scalarQueries(request.query as Record<string, unknown>) as Record<string, string>,
        ['kind', 'identity', 'version'],
      );
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
  return {
    routes,
    start() {
      if (started) throw new Error('Navigation delivery already started');
      if (!begin) throw new Error('Navigation routes are not registered');
      started = true;
      begin();
    },
  };
}
