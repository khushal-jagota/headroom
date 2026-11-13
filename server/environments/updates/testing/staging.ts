/** One receipt sequence. Archive children are real; candidate Python is never run. */
import { copyFile, readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SpawnOptions } from 'node:child_process';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { AppValidationError } from '../../release/contracts.ts';
import { RuntimeProbeUnsettled } from '../../release/admission_process.ts';
import { parse_release } from '../codec.ts';
import { create_release_staging } from '../staging.ts';
import { create_migration_status } from '../migration_status.ts';

interface Probe {
  response: unknown;
  entered?: () => void;
  calls: { argv: string[]; state: string; deadline: number; owner: unknown }[];
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  void promise.catch(() => {});
  return { promise, resolve, reject };
}
export async function staging_parity(
  spec: { root: string; artifact: string; release: Record<string, unknown>; schema_error: string },
  probe: Probe,
): Promise<unknown> {
  const owner = new ChildProcesses(),
    one_shot_children = new OneShotChildren(owner),
    spawn = owner.spawn.bind(owner);
  const entry = fileURLToPath(new URL('../../release/archive_unpack_main.ts', import.meta.url));
  let spawned = 0,
    closed = 0,
    admissions = 0,
    downloads = 0,
    refusing = false,
    stopping = false;
  let shutdown_failure: unknown;
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (command !== process.execPath || args[0] !== entry)
      throw new Error('Unexpected native launch');
    const child = spawn(command, args, options);
    spawned++;
    child.once('close', () => {
      closed++;
    });
    return child;
  }) as typeof owner.spawn;
  const stop = () => {
    stopping = true;
    owner.seal();
    one_shot_children.seal();
    try {
      owner.force_kill(performance.now() / 1000 + 5);
    } catch (error) {
      shutdown_failure = error;
    }
  };
  process.on('SIGTERM', stop);
  try {
    const receipt = parse_release(spec.release),
      rows = [];
    let held_admission:
      { error: RuntimeProbeUnsettled; entered: (root: string) => void } | undefined;
    const stage = create_release_staging(owner, one_shot_children.signal, async (root) => {
      admissions++;
      if (held_admission) {
        held_admission.entered(root);
        throw held_admission.error;
      }
      if (refusing) throw new AppValidationError('runtime probe failed');
    });
    for (const kind of ['fresh', 'reuse', 'retained-refusal', 'manifest', 'admission']) {
      if (stopping) throw new Error('Staging fixture terminated');
      const current = join(
        spec.root,
        'typescript',
        ['fresh', 'reuse', 'retained-refusal'].includes(kind) ? 'current' : kind,
      );
      refusing = kind === 'retained-refusal' || kind === 'admission';
      const release = {
        ...receipt,
        manifest:
          kind === 'manifest'
            ? { ...receipt.manifest, source_digest: '1'.repeat(64) }
            : receipt.manifest,
      };
      let error: string | null = null;
      try {
        await stage.stage(
          {
            available: async () => null,
            download_artifact: async (_id, destination) => {
              if (dirname(dirname(destination)) !== current)
                throw new Error('Stage changed filesystem');
              downloads++;
              await copyFile(spec.artifact, destination);
            },
          },
          release,
          current,
        );
      } catch (cause) {
        if (!(cause instanceof AppValidationError)) throw cause;
        error = cause.message;
      }
      rows.push([kind, error, (await readdir(current)).sort(), downloads, admissions]);
    }
    const migration = create_migration_status(owner),
      frontier = [];
    for (const name of ['~/schema.sqlite', 'different.sqlite']) {
      try {
        await migration.require_exact_schema_frontier(
          join(spec.root, name),
          join(spec.root, 'graph'),
        );
        frontier.push(null);
      } catch (error) {
        if (!(error instanceof AppValidationError)) throw error;
        frontier.push(error.message);
      }
    }
    const schema = [],
      clock = Object.getOwnPropertyDescriptor(performance, 'now');
    Object.defineProperty(performance, 'now', { configurable: true, value: () => 100_000 });
    try {
      for (const response of [
        { schema_change_pending: false },
        { error: spec.schema_error },
        { schema_change_pending: 'false' },
      ]) {
        probe.response = response;
        try {
          schema.push(
            await migration.schema_change_pending(
              '~/schema.sqlite',
              join(spec.root, 'typescript/current/.app-next-' + receipt.manifest.app_sha),
            ),
          );
        } catch (error) {
          if (!(error instanceof AppValidationError)) throw error;
          schema.push(error.message);
        }
      }
    } finally {
      if (clock) Object.defineProperty(performance, 'now', clock);
      else Reflect.deleteProperty(performance, 'now');
    }
    const call = probe.calls[0]!;
    if (
      probe.calls.length !== 3 ||
      probe.calls.some(
        (item) => item.owner !== owner || item.deadline !== 145 || existsSync(item.state),
      )
    )
      throw new Error('Schema probe lost owner, original deadline or temporary cleanup');
    // These observations use the same host and real consumers, without candidate execution.
    const cleanup_began = performance.now(),
      cleanup_spawned = spawned;
    const held_stage = async (current: string, retained: boolean) => {
      const cleanup = deferred<void>(),
        entered = deferred<string>();
      const original = new RuntimeProbeUnsettled(
        cleanup.promise,
        new AppValidationError('Held staged runtime probe'),
      );
      held_admission = { error: original, entered: entered.resolve };
      let completed = false;
      const actual = stage
        .stage(
          {
            available: async () => null,
            download_artifact: async (_id, destination) => {
              if (retained) throw new Error('Retained stage downloaded again');
              await copyFile(spec.artifact, destination);
            },
          },
          receipt,
          current,
        )
        .then(
          () => {
            completed = true;
            return null;
          },
          (error: unknown) => {
            completed = true;
            return error;
          },
        );
      const candidate = await entered.promise;
      try {
        await new Promise<void>((resolve) => setImmediate(resolve));
        if (completed || !existsSync(candidate) || !existsSync(dirname(candidate)))
          throw new Error('Staging removed or completed an unsettled candidate');
      } finally {
        cleanup.resolve();
      }
      const failure = await actual;
      held_admission = undefined;
      if (
        failure !== original ||
        existsSync(candidate) !== retained ||
        existsSync(dirname(candidate)) !== retained
      )
        throw new Error('Staging lost original error or actual cleanup ordering');
      return {
        held: true,
        original_error: true,
        candidate_after: retained,
        workspace_after: retained,
      };
    };
    const fresh_cleanup = await held_stage(join(spec.root, 'typescript/admission'), false);
    const retained_cleanup = await held_stage(join(spec.root, 'typescript/current'), true);
    const cleanup = deferred<void>(),
      entered = deferred<void>();
    const original = new RuntimeProbeUnsettled(
      cleanup.promise,
      new AppValidationError('Held schema runtime probe'),
    );
    const cleanup_failure = new Error('Recorded schema capture cleanup failure');
    probe.response = original;
    probe.entered = () => entered.resolve();
    let schema_completed = false;
    const actual = migration
      .schema_change_pending(
        '~/schema.sqlite',
        join(spec.root, 'typescript/current/.app-next-' + receipt.manifest.app_sha),
      )
      .then(
        () => {
          schema_completed = true;
          return null;
        },
        (error: unknown) => {
          schema_completed = true;
          return error;
        },
      );
    await entered.promise;
    const state = probe.calls.at(-1)!.state;
    try {
      await new Promise<void>((resolve) => setImmediate(resolve));
      if (schema_completed || !existsSync(state))
        throw new Error('Schema status removed or completed unsettled probe state');
    } finally {
      cleanup.reject(cleanup_failure);
      delete probe.entered;
    }
    const failure = await actual;
    if (
      !(failure instanceof AggregateError) ||
      failure.errors.length !== 2 ||
      failure.errors[0] !== original ||
      failure.errors[1] !== cleanup_failure ||
      existsSync(state)
    )
      throw new Error('Schema status lost error identity or actual cleanup ordering');
    const held_cleanup = {
      fresh: fresh_cleanup,
      retained: retained_cleanup,
      schema: { held: true, original_error: true, cleanup_error: true, state_removed: true },
      seconds: (performance.now() - cleanup_began) / 1000,
      archive_children: spawned - cleanup_spawned,
    };
    // Stop cannot detach the actual provider operation or publish its late result.
    const cancellation_began = performance.now();
    const download = deferred<void>(),
      download_entered = deferred<string>();
    const admissions_before = admissions;
    let download_signal: AbortSignal | undefined,
      cancellation_completed = false;
    const current = join(spec.root, 'typescript/admission');
    const cancelled = stage
      .stage(
        {
          available: async () => null,
          download_artifact: async (_id, destination, signal) => {
            download_signal = signal;
            await copyFile(spec.artifact, destination);
            download_entered.resolve(destination);
            await download.promise;
          },
        },
        receipt,
        current,
      )
      .then(
        () => {
          cancellation_completed = true;
          return null;
        },
        (error: unknown) => {
          cancellation_completed = true;
          return error;
        },
      );
    const artifact = await download_entered.promise;
    let retained = false;
    try {
      one_shot_children.seal();
      await new Promise<void>((resolve) => setImmediate(resolve));
      retained = !cancellation_completed && existsSync(artifact) && existsSync(dirname(artifact));
    } finally {
      download.resolve();
    }
    const cancellation_failure = await cancelled;
    const published = existsSync(join(current, '.app-next-' + receipt.manifest.app_sha));
    if (
      download_signal !== one_shot_children.signal ||
      !download_signal.aborted ||
      !retained ||
      cancellation_failure !== download_signal.reason ||
      admissions !== admissions_before ||
      published ||
      existsSync(dirname(artifact))
    )
      throw new Error('Staging lost caller cancellation or raw download cleanup');
    const staging_cancellation = {
      signal: true,
      held: true,
      original_error: true,
      admissions: 0,
      published: false,
      temporary_removed: true,
      seconds: (performance.now() - cancellation_began) / 1000,
    };
    return {
      rows,
      frontier,
      schema,
      held_cleanup,
      staging_cancellation,
      argv: call.argv,
      spawned,
      closed,
      outside: await readFile(join(spec.root, 'outside/sentinel'), 'utf8'),
    };
  } finally {
    owner.seal();
    one_shot_children.seal();
    try {
      try {
        owner.force_kill(performance.now() / 1000 + 5);
      } finally {
        await owner.settled();
      }
    } finally {
      process.removeListener('SIGTERM', stop);
    }
    if (shutdown_failure) throw shutdown_failure;
  }
}
