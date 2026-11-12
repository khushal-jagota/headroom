/** Export-only hosted proof. The existing Python fixture owns the process and listener. */
import { existsSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { startServer } from '../../core/start.ts';
import type { TestCompositionContext } from '../../core/start.ts';
import { Deferred } from '../../conversation/runtime/state.ts';
import { VideoUploadService } from '../../conversation/video_uploads/service.ts';
import { SqliteProcessConversationSystem } from '../../conversation/system.ts';
import { ConversationMessageFiles } from '../../conversation/record/message_files.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../../conversation/snapshot/composition.ts';

export async function stop_host_proof(spec: { root: string }): Promise<void> {
  const put = (name: string, value: unknown) => {
    const path = join(spec.root, name + '.json');
    writeFileSync(path + '.tmp', JSON.stringify(value));
    renameSync(path + '.tmp', path);
  };
  const handler = new Deferred<void>(),
    iterator = new Deferred<void>(),
    failed_backend = new Deferred<void>();
  const gates = {
    'release-handler': handler,
    'release-iterator': iterator,
    'release-failure': failed_backend,
  };
  // This control channel cannot itself keep a Promise-only retained APP alive.
  const polling = setInterval(() => {
    for (const [name, gate] of Object.entries(gates))
      if (existsSync(join(spec.root, name))) gate.resolve();
    for (const name of ['first', 'failed'])
      if (
        existsSync(join(spec.root, name + '-ping')) &&
        !existsSync(join(spec.root, name + '-pong.json'))
      )
        put(name + '-pong', true);
  }, 10);
  polling.unref();
  const stopped = new Deferred<void>();
  let server!: Awaited<ReturnType<typeof startServer>>;
  server = await startServer({
    testAreas(context) {
      return [
        {
          prefix: '/proof',
          routes: async (app) => {
            app.get('/held', async (_request, reply) => {
              reply.raw.once('close', () => put('handler-disconnected', true));
              put('handler-entered', true);
              await handler.promise;
              const result = context.database.read((sql) => sql.get('SELECT 1 AS alive'));
              put('handler-settled', result);
              return result;
            });
            app.get('/tail', async (request, reply) => {
              async function* output() {
                try {
                  yield 'ready\n';
                  put('iterator-entered', true);
                  await iterator.promise;
                  put(
                    'iterator-settled',
                    context.database.read((sql) => sql.get('SELECT 1 AS alive')),
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
            app.post('/stop', async () => {
              setImmediate(() => {
                const began = performance.now();
                void server.close({ deadline: performance.now() / 1000 + 0.05 }).then(
                  () => {
                    put('unexpected-close', true);
                    stopped.resolve();
                  },
                  (error: unknown) => {
                    put('retained', {
                      state: server.shutdown_state,
                      error: String(error),
                      seconds: (performance.now() - began) / 1000,
                    });
                    stopped.resolve();
                  },
                );
              });
              return { accepted: true };
            });
          },
        },
      ];
    },
  });
  // A once-only observation does not mask the second signal's default action.
  process.prependOnceListener('SIGTERM', () => put('first-term', true));
  await stopped.promise;
  await Promise.all([handler.promise, iterator.promise]);
  await server.shutdown_settled;
  await server.finish_shutdown({ deadline: performance.now() / 1000 + 2 });
  put('clean-finish', server.shutdown_state);

  // Reuse this exact owned host/DB for an irrecoverable cleanup failure. No bypass
  // can dispose the failed owner; the Python fixture must terminate this process.
  delete process.env.PLAN_SERVER_LISTENER_FD;
  process.env.PLAN_PORT = '0';
  let context!: TestCompositionContext;
  const original_drain = VideoUploadService.prototype.drain;
  let reentered: Promise<void> | undefined;
  VideoUploadService.prototype.drain = () => {
    throw new Error('scripted video cleanup failure');
  };
  try {
    const failed = await startServer({
      createConversationCore(value) {
        const unused = () => {
          throw new Error('This lifetime proof never starts a provider');
        };
        return new SqliteProcessConversationSystem({
          store: value.record.store,
          message_files: new ConversationMessageFiles(value.config.db_path),
          backend_child_factories: { claude: unused, codex: unused, hermes: unused },
          on_shutdown: () => {
            value.revoke_signal();
            put('core-fenced', true);
          },
        });
      },
      createBackendServices(value) {
        const runtime = create_backend_runtime({
          database: value.database,
          config: value.config,
          now: () => value.clock.now(),
          providers: unavailable_backend_providers(),
        });
        let actual = Promise.resolve();
        return {
          ...runtime,
          shutdown: (options) => {
            actual = failed_backend.promise.then(() => {
              const observed = runtime.shutdown?.(options);
              void observed?.catch(() => {});
              return runtime.settled();
            });
            reentered = failed.close({ deadline: performance.now() / 1000 + 100 });
            void reentered.catch(() => {});
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
        return [];
      },
    });
    const closing = failed.close({ deadline: performance.now() / 1000 + 0.05 });
    put('reentry', { same_flight: reentered === closing });
    let actual_settled = false;
    const actual = failed.shutdown_settled.then(
      () => {
        actual_settled = true;
      },
      () => {
        actual_settled = true;
      },
    );
    await closing.catch((error: unknown) => {
      put('failure-observed', {
        actual_settled,
        state: failed.shutdown_state,
        error: String(error),
      });
    });
    await failed_backend.promise;
    await actual;
    const refused = await failed.finish_shutdown({ deadline: performance.now() / 1000 + 2 }).then(
      () => null,
      (error: unknown) => String(error),
    );
    put('failure-finished', {
      actual_settled,
      refused,
      state: failed.shutdown_state,
      database: context.database.read((sql) => sql.get('SELECT 1 AS alive')),
    });
  } finally {
    VideoUploadService.prototype.drain = original_drain;
  }
  // Only the application's retention handle remains referenced after return.
}
