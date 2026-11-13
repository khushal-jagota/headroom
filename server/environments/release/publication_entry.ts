/** One lifecycle for the fixed build and receipt entries, without module-load work. */
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { writeSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspect } from 'node:util';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import { probe_environment } from './admission_process.ts';
import { remove_owned_tree } from './archive.ts';
import { PUBLICATION_ENTRY_SECONDS } from './publication_configuration.ts';
import type { ReleaseExecution } from './contracts.ts';

export async function with_publication_entry(
  action: (
    execution: ReleaseExecution,
    register_output: (owned_directory: string) => void,
  ) => Promise<void>,
): Promise<0 | 1> {
  const deadline = performance.now() / 1000 + PUBLICATION_ENTRY_SECONDS;
  const owner = new ChildProcesses(),
    controller = new AbortController(),
    failures = new Set<unknown>();
  let interrupted: 'SIGINT' | 'SIGTERM' | undefined,
    scratch: string | undefined,
    output: string | undefined,
    forced = false;
  const force = () => {
    if (forced) return;
    forced = true;
    try {
      owner.force_kill(performance.now() / 1000);
    } catch (error) {
      failures.add(error);
    }
  };
  const stop = (signal: 'SIGINT' | 'SIGTERM') => {
    if (interrupted) return;
    interrupted = signal;
    const reason = new Error(`Publication entry interrupted by ${signal}`);
    failures.add(reason);
    owner.seal();
    controller.abort(reason);
    force();
  };
  const interrupt = () => stop('SIGINT'),
    terminate = () => stop('SIGTERM');
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  let actual_settled = false;
  const actual = (async (): Promise<0 | 1> => {
    try {
      scratch = await mkdtemp(join(tmpdir(), 'panels-publication-'));
      for (const name of [
        'config',
        'cache',
        'codex',
        'claude',
        'hermes',
        'data',
        'logs',
        'backups',
        'app',
        'workspace',
        'scratch',
      ])
        await mkdir(join(scratch, name), { mode: 0o700 });
      await writeFile(join(scratch, 'config.yaml'), '{}\n', { mode: 0o600 });
      const environment = probe_environment(scratch),
        native_spawn = owner.spawn.bind(owner);
      function spawn(
        command: string,
        args: readonly string[],
        options: SpawnOptionsWithoutStdio,
      ): ChildProcessWithoutNullStreams;
      function spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess;
      function spawn(
        command: string,
        args: readonly string[],
        options: SpawnOptions,
      ): ChildProcess {
        return native_spawn(command, args, { ...options, env: { ...environment, ...options.env } });
      }
      owner.spawn = spawn;
      controller.signal.throwIfAborted();
      await action({ child_processes: owner, signal: controller.signal, deadline }, (directory) => {
        // Fixed entry glue registers only a newly created output after its producer returns.
        output = directory;
      });
    } catch (error) {
      failures.add(error);
    } finally {
      owner.seal();
      if (failures.size) force();
      try {
        await owner.settled();
      } catch (error) {
        failures.add(error);
      }
      if (scratch) {
        try {
          await rm(scratch, { recursive: true, force: true });
        } catch (error) {
          failures.add(error);
        }
      }
      if (output && failures.size) {
        try {
          await remove_owned_tree(output);
        } catch (error) {
          failures.add(error);
        }
      }
      process.off('SIGINT', interrupt);
      process.off('SIGTERM', terminate);
    }
    const errors = [...failures],
      primary = errors[0],
      error =
        errors.length > 1
          ? new AggregateError(errors, 'Publication entry did not close cleanly', {
              cause: primary,
            })
          : primary;
    if (interrupted) {
      try {
        writeSync(
          2,
          inspect(error, {
            depth: null,
            colors: false,
            maxArrayLength: null,
            maxStringLength: null,
          }) + '\n',
        );
      } catch {
        /* Original signal still owns termination. */
      }
      process.kill(process.pid, interrupted);
      return 1;
    }
    if (errors.length) throw error;
    return 0;
  })().finally(() => {
    actual_settled = true;
  });
  try {
    await observe_shutdown(
      actual,
      { deadline },
      'Publication entry exceeded its original deadline',
    );
    return await actual;
  } catch (error) {
    if (performance.now() / 1000 < deadline) throw error;
    failures.add(error);
    owner.seal();
    controller.abort(error);
    force();
    try {
      writeSync(
        2,
        inspect(
          {
            message:
              'Publication entry deadline expired; cleanup is not confirmed. Retain remaining owned state.',
            deadline,
            actual_entry_settled: actual_settled,
            scratch,
            output,
            errors: [...failures],
          },
          { depth: null, colors: false, maxArrayLength: null, maxStringLength: null },
        ) + '\n',
      );
    } finally {
      // The observer is not settlement. Do not delete underneath the actual flight.
      process.exit(1);
    }
  }
}
