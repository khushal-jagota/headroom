/** One receipt sequence. Archive children are real; candidate Python is never run. */
import { copyFile, readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SpawnOptions } from 'node:child_process';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { AppValidationError } from '../../release/contracts.ts';
import { parse_release } from '../codec.ts';
import { create_release_staging } from '../staging.ts';
import { create_migration_status } from '../migration_status.ts';

interface Probe {
  response: unknown;
  calls: { argv: string[]; state: string; deadline: number; owner: unknown }[];
}
export async function staging_parity(
  spec: { root: string; artifact: string; release: Record<string, unknown>; schema_error: string },
  probe: Probe,
): Promise<unknown> {
  const owner = new ChildProcesses(),
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
    const stage = create_release_staging(owner, async () => {
      admissions++;
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
    return {
      rows,
      frontier,
      schema,
      argv: call.argv,
      spawned,
      closed,
      outside: await readFile(join(spec.root, 'outside/sentinel'), 'utf8'),
    };
  } finally {
    owner.seal();
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
