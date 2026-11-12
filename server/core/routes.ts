import type { FastifyInstance } from 'fastify';
import { Readable } from 'node:stream';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import type { Config } from './config.ts';
import { SSE_HEARTBEAT_MS, pythonRepr } from './config.ts';
import type { Clock } from './clock.ts';
import { TestClock } from './clock.ts';
import { PlannerError } from './contracts.ts';
import { registerArea } from './authority/register.ts';
import type { Database } from './database/index.ts';
import { indexHtml, mountStatic, sendFile } from './http/static.ts';
export interface Changes {
  subscribe(callback: () => void): () => void;
  emit(): void;
}
export async function registerCoreRoutes(
  app: FastifyInstance,
  config: Config,
  clock: Clock,
  changes: Changes,
  root: string,
  database: Database,
): Promise<void> {
  app.get('/api/meta', async () => ({ test_mode: config.test_mode, app_sha: config.app_sha }));
  app.get('/api/health', async (request, reply) => {
    if (config.app_sha === null) {
      if (config.test_mode) return { ready: true, app_sha: null };
      return reply.code(503).send({ ready: false, error: 'missing app SHA' });
    }
    const rawExpected = (
      request.query as {
        expected_sha?: string | string[];
      }
    ).expected_sha;
    const expected = Array.isArray(rawExpected) ? rawExpected.at(-1) : rawExpected;
    if (expected !== config.app_sha)
      return reply.code(503).send({ ready: false, error: 'expected app SHA required' });
    return { ready: true, app_sha: config.app_sha };
  });
  const streams = new Set<Readable>();
  app.addHook('preClose', async () => {
    for (const stream of streams) stream.destroy();
  });
  app.get('/api/changes', { config: { uncompressed: true } }, async (_request, reply) => {
    let changed = false;
    let owing = false;
    let scheduled: NodeJS.Immediate | undefined;
    const stream = new Readable({
      read() {
        owing = false;
        if (changed) {
          changed = false;
          this.push('data: change\n\n');
        }
      },
    });
    const flush = (): void => {
      scheduled = undefined;
      if (!owing && !stream.destroyed) {
        changed = false;
        owing = !stream.push('data: change\n\n');
      }
    };
    const unsubscribe = changes.subscribe(() => {
      changed = true;
      if (!scheduled && !owing) scheduled = setImmediate(flush);
    });
    const timer = setInterval(() => {
      if (!stream.destroyed && !changed && !owing) owing = !stream.push(': keep-alive\n\n');
    }, SSE_HEARTBEAT_MS);
    timer.unref();
    streams.add(stream);
    stream.once('close', () => {
      clearInterval(timer);
      if (scheduled) clearImmediate(scheduled);
      unsubscribe();
      streams.delete(stream);
    });
    reply.raw.once('close', () => stream.destroy());
    reply.header('content-type', 'text/event-stream; charset=utf-8');
    // Node must send headers before the first heartbeat/event.
    reply.raw.setHeader('content-type', 'text/event-stream; charset=utf-8');
    reply.raw.flushHeaders();
    return reply.send(stream);
  });
  app.get('/', async (_request, reply) =>
    reply
      .header('content-type', 'text/html; charset=utf-8')
      .header('cache-control', 'no-cache, must-revalidate')
      .send(await indexHtml(root, config.app_sha)),
  );
  app.get('/service-worker.js', async (request, reply) =>
    sendFile(request, reply, join(root, 'static/service-worker.js'), {
      mime: 'application/javascript',
      cache: 'no-cache',
    }),
  );
  if (existsSync(join(root, 'web/dist')))
    await mountStatic(app, '/_app', join(root, 'web/dist'), true);
  await mountStatic(app, '/assets', join(root, 'assets'));
  await mountStatic(app, '/static', join(root, 'static'));
  if (config.test_mode) {
    registerArea(
      app,
      {
        prefix: '/api',
        routes: async (scope) => {
          scope.post('/test/set-now', async (request, reply) => {
            const body = request.body;
            if (body == null)
              return reply.code(422).send({
                detail: [{ type: 'missing', loc: ['body'], msg: 'Field required', input: null }],
              });
            if (typeof body !== 'object' || Array.isArray(body))
              return reply.code(422).send({
                detail: [
                  {
                    type: 'dict_type',
                    loc: ['body'],
                    msg: 'Input should be a valid dictionary',
                    input: body,
                  },
                ],
              });
            const raw = (
              body as {
                now?: unknown;
              }
            ).now;
            if (typeof raw !== 'string' || !raw.trim())
              throw new PlannerError('validation', "body must carry an ISO 'now' string");
            try {
              new TestClock(raw);
            } catch {
              throw new PlannerError('validation', `invalid ISO datetime: ${pythonRepr(raw)}`);
            }
            if (!(clock instanceof TestClock))
              throw new PlannerError(
                'validation',
                'clock is not a TestClock; start with PLAN_FAKE_NOW set',
              );
            clock.set(raw);
            return { now: clock.isoformat() };
          });
        },
      },
      { database, config, clock },
    );
  }
}
