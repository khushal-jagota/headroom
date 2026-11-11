import { once } from 'node:events';
import { createHash } from 'node:crypto';
import type { FastifyPluginAsync, FastifyReply, FastifyRequest } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import type { CurrentAuthority, CurrentViewSnapshot, ViewRequest } from '../../web/src/lib/navigation/contracts.ts';
import type { NavigationAuthority } from './contracts.ts';
import { parseView } from './logic/view.ts';
import { compact } from './logic/json.ts';
import { NavigationUnavailable } from './logic/errors.ts';
import { jsonFailure } from '../core/http/json.ts';
import { scalarQueries } from '../list_reads/http.ts';
import { navigationSources } from './index.ts';
import { snapshot } from './data/read.ts';
let authority: NavigationAuthority | undefined;
export function configureNavigationAuthority(provider: NavigationAuthority): void { authority = provider; }
function query(request: FastifyRequest, reply: FastifyReply, names: string[]): Record<string, string> | null {
  const values = scalarQueries(request.query as Record<string, unknown>);
  const absent = names.filter(name => values[name] === undefined);
  if (absent.length) { reply.code(422).send({ detail: absent.map(name => ({ type: 'missing', loc: ['query', name], msg: 'Field required', input: null })) }); return null; }
  return values as Record<string, string>;
}
function view(request: FastifyRequest, reply: FastifyReply): ViewRequest | null {
  const value = query(request, reply, ['view']);
  if (!value) return null;
  if (Buffer.byteLength(value.view) > 128 * 1024) { reply.code(413).send({ detail: 'navigation view request is too large' }); return null; }
  try { return parseView(JSON.parse(value.view)); }
  catch (error) {
    let detail = (error as Error).message;
    if (error instanceof SyntaxError) {
      const failure = jsonFailure(value.view);
      const before = [...value.view].slice(0, failure.position).join('');
      const line = before.split('\n').length, column = [...before.slice(before.lastIndexOf('\n') + 1)].length + 1;
      detail = `${failure.message}: line ${line} column ${column} (char ${failure.position})`;
    }
    reply.code(400).send({ detail }); return null;
  }
}
function capture(services: AreaServices, value: ViewRequest): { current: CurrentViewSnapshot; token: CurrentAuthority } {
  if (!authority) throw new NavigationUnavailable('navigation has no Signal knowledge source');
  let token: CurrentAuthority;
  try { token = authority.capture(); } catch (error) { throw new NavigationUnavailable((error as Error).message); }
  const current = services.database.read(sql => snapshot(sql, value, token, navigationSources));
  try { authority.validate(token); } catch (error) { throw new NavigationUnavailable((error as Error).message); }
  return { current, token };
}
function frame(current: object & { event: string }): string { return `event: ${current.event}\ndata: ${compact(current)}\n\n`; }
function difference(previous: CurrentViewSnapshot, current: CurrentViewSnapshot): Record<string, unknown> & { event: string } {
  const { event: _event, mode: _mode, content, ...common } = current;
  if (current.content_version === previous.content_version) return { ...common, event: 'checkpoint', previous_coverage: previous.coverage };
  const old = new Map(previous.content.records.map(row => [compact([row.kind, row.id]), compact(row)]));
  const fresh = new Set(content.records.map(row => compact([row.kind, row.id])));
  return { ...common, event: 'patch', previous_coverage: previous.coverage, previous_content_version: previous.content_version, upserts: content.records.filter(row => old.get(compact([row.kind, row.id])) !== compact(row)), evictions: previous.content.records.filter(row => !fresh.has(compact([row.kind, row.id]))).map(({ kind, id }) => ({ kind, id })), windows: content.windows, counts: content.counts, review_context: content.review_context, review_summary: content.review_summary };
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  const streams = new Set<() => void>();
  app.addHook('onClose', async () => { for (const close of streams) close(); });
  app.get('/snapshot', async (request, reply) => {
    const value = view(request, reply); if (!value) return;
    try {
      const { current } = capture(services, value);
      return reply.header('cache-control', 'no-store').send(current);
    } catch (error) { if (!(error instanceof NavigationUnavailable)) throw error; return reply.code(503).send({ detail: 'navigation view is unavailable' }); }
  });
  app.get('/events', async (request, reply) => {
    const value = view(request, reply); if (!value) return;
    let admitted: ReturnType<typeof capture>;
    try { admitted = capture(services, value); } catch (error) { if (!(error instanceof NavigationUnavailable)) throw error; return reply.code(503).send({ detail: 'navigation view is unavailable' }); }
    reply.hijack();
    reply.raw.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no' });
    let previous = admitted.current, closed = false, sending = false, heartbeat = Date.now();
    const abort = new AbortController();
    let unwatch: () => void = () => {};
    const close = () => { if (closed) return; closed = true; abort.abort(); clearInterval(timer); unwatch(); streams.delete(close); reply.raw.end(); };
    const send = async (body: string) => {
      authority!.validate(admitted.token);
      if (!reply.raw.write(body)) {
        const deadline = setTimeout(() => abort.abort(), 10_000);
        try { await once(reply.raw, 'drain', { signal: abort.signal }); }
        finally { clearTimeout(deadline); }
      }
      authority!.validate(admitted.token);
    };
    const timer = setInterval(() => { void (async () => {
      if (closed || sending) return;
      sending = true;
      try {
        authority!.validate(admitted.token);
        const current = capture(services, value).current;
        if (current.coverage.epoch !== previous.coverage.epoch) { close(); return; }
        if (current.coverage.revision !== previous.coverage.revision) { await send(frame(difference(previous, current))); previous = current; heartbeat = Date.now(); }
        else if (Date.now() - heartbeat >= 15_000) { await send(': heartbeat\n\n'); heartbeat = Date.now(); }
      } catch { close(); }
      finally { sending = false; }
    })(); }, 1000);
    unwatch = authority!.watch(admitted.token, close);
    if (closed) { unwatch(); return; }
    streams.add(close);
    reply.raw.once('close', close);
    sending = true;
    try { await send(frame(previous)); } catch { close(); } finally { sending = false; }
  });
  app.get('/content', async (request, reply) => {
    const value = query(request, reply, ['kind', 'identity', 'version']); if (!value) return;
    if (!['ticket', 'manager'].includes(value.kind) || [...value.identity].length > 256 || value.version.length !== 64) return reply.code(400).send({ detail: 'invalid recap reference' });
    const row = services.database.read(sql => sql.get<{ value: string }>('SELECT value FROM navigation_records WHERE kind=? AND id=?', [value.kind, value.identity]));
    if (!row) return reply.code(409).send({ detail: 'recap owner is absent' });
    const body = String((JSON.parse(row.value) as { recap: string }).recap);
    if (createHash('sha256').update(body).digest('hex') !== value.version) return reply.code(409).send({ detail: 'recap source changed' });
    return reply.header('cache-control', 'no-store').header('etag', value.version).type('text/plain; charset=utf-8').send(body);
  });
};
