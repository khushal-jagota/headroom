/** Export-only hosted proof. The existing Python fixture owns each process/listener. */
import { existsSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { startServer } from '../../core/start.ts';
import type { TestCompositionContext } from '../../core/start.ts';
import { Deferred } from '../../conversation/runtime/state.ts';
import { VideoUploadService } from '../../conversation/video_uploads/service.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../../conversation/snapshot/composition.ts';

export async function stop_host_proof(spec: {
  root: string;
  mode: 'http' | 'failure' | 'hard';
}): Promise<void> {
  const put = (name: string, value: unknown) => {
    const path = join(spec.root, name + '.json');
    writeFileSync(path + '.tmp', JSON.stringify(value));
    renameSync(path + '.tmp', path);
  };
  const handler = new Deferred<void>(),
    iterator = new Deferred<void>(),
    slow = new Deferred<void>(),
    late = new Deferred<void>(),
    backend = new Deferred<void>();
  const gates = {
    'release-handler': handler,
    'release-iterator': iterator,
    'release-slow': slow,
    'release-late': late,
    'release-backend': backend,
  };
  // File gates must not conceal missing application ownership with a referenced timer.
  const polling = setInterval(() => {
    for (const [name, gate] of Object.entries(gates))
      if (existsSync(join(spec.root, name))) gate.resolve();
    if (existsSync(join(spec.root, 'ping'))) put('pong', true);
  }, 10);
  polling.unref();
  const finished = new Deferred<void>();
  let context!: TestCompositionContext;
  let server!: Awaited<ReturnType<typeof startServer>>;
  let reentered: Promise<void> | undefined;
  let reenter = false;
  let closing: Promise<void> | undefined;
  let closed = false;
  const begin = () => {
    closing = server.close();
    void closing.then(
      () => {
        closed = true;
        put('closed', server.shutdown_state);
        finished.resolve();
      },
      (error: unknown) => {
        closed = true;
        process.exitCode = 1;
        put('failed', { state: server.shutdown_state, error: String(error) });
        finished.resolve();
      },
    );
    put('stopping', { state: server.shutdown_state, closed, reentry: reentered === closing });
  };
  const original_drain = VideoUploadService.prototype.drain;
  if (spec.mode === 'failure')
    VideoUploadService.prototype.drain = () => {
      throw new Error('scripted video cleanup failure');
    };
  try {
    server = await startServer({
      createBackendServices(value) {
        const runtime = create_backend_runtime({
          database: value.database,
          config: value.config,
          now: () => value.clock.now(),
          providers: unavailable_backend_providers(),
          one_shot_children: value.one_shot_children,
        });
        if (spec.mode !== 'failure') return runtime;
        let actual = Promise.resolve();
        return {
          ...runtime,
          shutdown(options) {
            actual = backend.promise.then(async () => {
              const observed = runtime.shutdown?.(options);
              void observed?.catch(() => {});
              await runtime.settled();
            });
            if (!reenter) {
              reenter = true;
              reentered = server.close({ deadline: performance.now() / 1000 + 100 });
            }
            put('backend-fenced', true);
            return observe_shutdown(
              actual,
              options ?? {},
              'Backend resources remain unfinished at the shutdown deadline.',
            );
          },
          settled: () => actual,
        };
      },
      testAreas(value) {
        context = value;
        const start_native = () => {
          const marker = join(spec.root, 'native-identities.json');
          const script = [
            "const fs=require('node:fs'),{spawn}=require('node:child_process');",
            "const descendant=spawn(process.execPath,['-e','setInterval(()=>{},2147483647)'],{stdio:'ignore'});",
            "const identity=pid=>{const fields=fs.readFileSync('/proc/'+pid+'/stat','utf8').split(')').slice(1).join(')').trim().split(/\\s+/);return{pid,parent:Number(fields[1]),start_time:Number(fields[19])}};",
            `const marker=${JSON.stringify(marker)};`,
            "try{fs.writeFileSync(marker+'.tmp',JSON.stringify({leader:identity(process.pid),descendant:identity(descendant.pid)}));",
            "fs.renameSync(marker+'.tmp',marker)}catch(error){descendant.kill('SIGKILL');throw error}setInterval(()=>{},2147483647);",
          ].join('');
          const child = value.child_processes.spawn(process.execPath, ['-e', script], {
            stdio: 'ignore',
          });
          put('owned-native', { pid: child.pid });
        };
        return [
          {
            prefix: '/proof',
            routes: async (app) => {
              app.addHook('onRequest', async (request) => {
                if (request.url === '/proof/late') {
                  put('late-entered', true);
                  await late.promise;
                }
              });
              app.get('/held', async (_request, reply) => {
                reply.raw.once('close', () => put('handler-disconnected', true));
                put('handler-entered', true);
                await handler.promise;
                const result = value.database.read((sql) => sql.get('SELECT 1 AS alive'));
                put('handler-settled', result);
                return result;
              });
              app.get('/slow', async () => {
                put('slow-entered', true);
                await slow.promise;
                return { complete: 'ordinary response during grace' };
              });
              app.get('/late', async () => ({ unexpected: 'admitted after seal' }));
              app.get('/tail', async (request, reply) => {
                async function* output() {
                  try {
                    yield 'ready\n';
                    put('iterator-entered', true);
                    await iterator.promise;
                    put(
                      'iterator-settled',
                      value.database.read((sql) => sql.get('SELECT 1 AS alive')),
                    );
                    yield 'done\n';
                  } finally {
                    put('iterator-returned', true);
                  }
                }
                const stream = Readable.from(request.request_lifetime!.iterator(output()));
                reply.raw.once('close', () => {
                  put('iterator-disconnected', true);
                  stream.destroy();
                });
                return reply.type('text/plain').send(stream);
              });
              app.post('/native', async () => {
                start_native();
                return { accepted: true };
              });
              app.post('/stop', async () => {
                setImmediate(begin);
                return { accepted: true };
              });
            },
          },
        ];
      },
    });
    process.on('exit', (code) => {
      let database: unknown;
      try {
        database = context.database.read((sql) => sql.get('SELECT 1 AS alive'));
      } catch (error) {
        database = String(error);
      }
      put('exit', { code, database, state: server.shutdown_state });
    });
    process.prependOnceListener('SIGTERM', () => put('first-term', true));
    await finished.promise;
    if (spec.mode !== 'http') return;
    // The first lifetime demonstrates complete ordinary responses and combined
    // disconnected handler/iterator ownership. The second FIRST stops by SIGTERM.
    delete process.env.PLAN_SERVER_LISTENER_FD;
    process.env.PLAN_PORT = '0';
    server = await startServer({
      testAreas(value) {
        context = value;
        return [];
      },
    });
    put('idle-ready', { address: server.address });
  } finally {
    VideoUploadService.prototype.drain = original_drain;
  }
}
