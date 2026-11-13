/** A timed-out lifecycle observer must not release the actual resource join. */
import { mkdirSync } from 'node:fs';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
import { setImmediate } from 'node:timers/promises';
import { Worker } from 'node:worker_threads';
import { expect, test, vi } from 'vitest';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { UsageService } from '../conversation/usage/UsageService.ts';
import { CredentialRequestOperations } from '../credentials/requests/service.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { RecordingBackendProbeEnvironment } from '../conversation/snapshot/recording_environment.ts';
import { loadConfig } from '../core/config.ts';
import { setup } from './conversation_core_fixture.ts';

// Gap: individual shutdown tests do not establish one deadline contract or prove
// that every service retains its raw completion after the observer rejects.
// Reuse one database, the existing deferred/core fixture, and one usage worker.
// No listener, browser, provider process, or transcript scan participates.
test('four service deadlines reject without completing their actual settlement', async () => {
  const f = setup();
  const release = new Deferred<void>();
  const video_entered = new Deferred<void>();
  const credential_entered = new Deferred<void>();
  const usage_entered = new Deferred<void>();
  const state = f.database_path + '.usage';
  mkdirSync(state);
  const usage = new UsageService(state, [], {
    ratesFetcher: async () => {
      usage_entered.resolve();
      await release.promise;
      return null;
    },
  });
  const video = new VideoUploadService(f.database, f.database_path, {
    now: () => 100,
    disk_free_bytes: () => 1024,
    require_probe: () => '/recorded/ffprobe',
    probe_video: async () => 'video/mp4',
    limits: {
      max_video_bytes: 4,
      video_budget_bytes: 4,
      free_space_floor_bytes: 0,
      transfer_chunk_bytes: 4,
      progress_record_bytes: 4,
    },
  });
  const reserved = video.reserve({
    destination: { kind: 'chief', id: 'chief', expected_conversation_id: null },
    filename: 'synthetic.mp4',
    byte_count: 4,
  });
  async function* chunks() {
    video_entered.resolve();
    await release.promise;
    yield Buffer.from('part');
  }
  const credentials = new CredentialRequestOperations({
    database: f.database,
    core: f.core,
    writes: new ConversationHttpWriteScope(),
    key_path: f.database_path + '.credential-key',
    now: () => 100,
  });
  const credential_work = credentials.shield_operation(async () => {
    credential_entered.resolve();
    await release.promise;
  });
  const backend_failure = new Error('Synthetic environment cleanup failure');
  const usage_failure = new Error('Synthetic worker termination failure after exit');
  const terminate = Worker.prototype.terminate;
  const termination = vi.spyOn(Worker.prototype, 'terminate').mockImplementation(async function (
    this: Worker,
  ) {
    await terminate.call(this);
    throw usage_failure;
  });
  const backend = create_backend_runtime({
    database: f.database,
    config: loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: f.database_path }),
    now: () => new Date(100_000),
    environment: Object.assign(new RecordingBackendProbeEnvironment(), {
      shutdown: () => {
        throw backend_failure;
      },
    }),
    providers: {
      ...unavailable_backend_providers(),
      codex_sign_in: {
        read: () => null,
        start: async () => {
          throw new Error('No sign-in is part of this proof');
        },
        cancel: async () => {
          throw new Error('No sign-in is part of this proof');
        },
        shutdown: () => release.promise,
      },
    },
  });
  const video_work = video.transfer(reserved.upload_id, chunks());
  const usage_work = usage.refreshRates().catch((error: unknown) => error);
  const work = [video_work, credential_work, usage_work];
  try {
    await Promise.all([video_entered.promise, credential_entered.promise, usage_entered.promise]);
    const options = { deadline: performance.now() / 1000 + 0.01 };
    const observers = [
      video.drain(options),
      usage.stop(options),
      credentials.finish_operations(options),
      backend.shutdown!(options),
    ];
    const completed = [false, false, false, false];
    const settlement = [video, usage, credentials, backend].map((service, index) =>
      service.settled().finally(() => {
        completed[index] = true;
      }),
    );
    const outcomes = await Promise.allSettled(observers);
    for (const outcome of outcomes) {
      expect(outcome.status).toBe('rejected');
      if (outcome.status === 'rejected')
        expect(outcome.reason).toBeInstanceOf(ServerLifecycleError);
    }
    await setImmediate();
    expect(completed).toEqual([false, false, false, false]);
    release.resolve();
    const actual_results = await Promise.allSettled(settlement);
    await Promise.allSettled(work);
    expect(completed).toEqual([true, true, true, true]);
    expect(actual_results).toMatchObject([
      { status: 'fulfilled' },
      { status: 'rejected', reason: { errors: [usage_failure] } },
      { status: 'fulfilled' },
      { status: 'rejected', reason: { errors: [backend_failure] } },
    ]);
  } finally {
    release.resolve();
    await Promise.allSettled(work);
    await Promise.allSettled([
      video.settled(),
      usage.stop({ deadline: performance.now() / 1000 + 1 }),
      credentials.settled(),
      backend.shutdown!({ deadline: performance.now() / 1000 + 1 }),
    ]);
    await Promise.allSettled([usage.settled(), backend.settled()]);
    termination.mockRestore();
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
