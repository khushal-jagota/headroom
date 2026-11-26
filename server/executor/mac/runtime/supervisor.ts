/** An installed service starts its selected runtime and owns its lease trial. */
import { randomUUID } from 'node:crypto';
import { constants as os_constants, homedir } from 'node:os';
import { delimiter, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { OwnedProcess } from '../../../lifecycle/owned_processes.ts';
import { copy_environment } from '../../../core/environment.ts';
import { SUPERVISOR_APP_STOP_SECONDS } from '../../../lifecycle/configuration.ts';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import type { MacRuntimeSelection } from '../../wire.generated.ts';
import type { FilesService } from '../files.ts';
import { LINUX_EXECUTOR } from '../../../environments/release/contracts.ts';
import { LINUX_EXECUTOR_ENTRYPOINT } from '../../../environments/linux_executor/configuration.ts';
import { linux_executor_path } from '../../../environments/linux_executor/logic/paths.ts';
import {
  ExecutorPaths,
  LINUX_UPDATE_TERMINATE_SECONDS,
  MAC_RUNTIME_CHILD_ENV,
  MAC_RUNTIME_HANDOFF_ENV,
  MAC_RUNTIME_READY_ENV,
  MAC_RUNTIME_READY_TIMEOUT_SECONDS,
  MAC_RUNTIME_UPDATE_EXIT_CODE,
  MAC_UPDATE_POLL_SECONDS,
  MAC_UPDATE_TERMINATE_SECONDS,
  MACHINE_SERVICE_TRIAL_ENV,
  MACHINE_SERVICE_TRIAL_FILE,
} from '../configuration.ts';
import { atomic_record, read_record } from './records.ts';
import { failure_detail } from '../failure_detail.ts';

export interface MacRuntimeSupervisorOptions {
  paths: ExecutorPaths;
  baseline_root: string;
  /** The absolute admitted host Node also runs selected Linux releases. */
  baseline_node: string;
  environment: Readonly<NodeJS.ProcessEnv>;
  child_processes: OwnedChildProcesses;
  one_shot_children: OwnedOneShotChildren;
  /** Caller-owned; close only after supervise settles. */
  files: FilesService;
  signal: AbortSignal;
}
function revision_argument(args: readonly string[]): {
  index: number;
  inline: boolean;
  value: string;
} {
  const index = args.findIndex(
    (value) => value === '--application-revision' || value.startsWith('--application-revision='),
  );
  if (index < 0) throw new TypeError('Executor runtime needs its explicit application revision.');
  const inline = args[index]!.includes('=');
  const value = inline ? args[index]!.slice('--application-revision='.length) : args[index + 1];
  if (!value) throw new TypeError('Executor runtime application revision is empty.');
  return { index, inline, value };
}
function status(owned: OwnedProcess): number {
  return (
    owned.child.exitCode ??
    (owned.child.signalCode ? 128 + os_constants.signals[owned.child.signalCode] : 1)
  );
}
export async function supervise(
  args: string[],
  options: MacRuntimeSupervisorOptions,
): Promise<number> {
  const root = join(options.paths.root, 'updates');
  const files = options.files;
  const revision = revision_argument(args);
  let selected = (await read_record(
    files,
    join(root, 'selection.json'),
  )) as MacRuntimeSelection | null;
  const initial_pending = (await read_record(
    files,
    join(root, 'pending.json'),
  )) as MacRuntimeSelection | null;
  const custody = await read_record(files, join(root, 'handoff.json'));
  let handoff = custody?.replay_pending === true ? join(root, 'handoff.json') : null;
  // Only the service's freshly installed supervisor can accept the service trial.
  const service_trial = await read_record(files, join(root, MACHINE_SERVICE_TRIAL_FILE));
  const trial_selection = service_trial?.pending as MacRuntimeSelection | undefined;
  const owns_service_trial =
    service_trial?.started === true &&
    trial_selection?.runtime_path === resolve(options.baseline_root);
  const starts_service_trial =
    owns_service_trial && selected?.request_id !== trial_selection?.request_id;
  if (starts_service_trial && initial_pending?.request_id !== trial_selection?.request_id) return 1;
  if (initial_pending !== null && !starts_service_trial) {
    if (selected?.request_id !== initial_pending.request_id) {
      await atomic_record(files, join(root, 'failure.json'), {
        request_id: initial_pending.request_id,
        phase: 'reverted',
        detail: 'The runtime supervisor restarted before confirmation.',
      });
      handoff = join(root, 'handoff.json');
    }
    await files.remove_runtime_record(join(root, 'pending.json'));
  }
  let current: OwnedProcess | undefined;
  const launch = async (
    runtime: string | null,
    app_revision: string,
    ready: string | null,
    saved: string | null,
    service_trial = false,
  ): Promise<OwnedProcess> => {
    options.signal.throwIfAborted();
    const linux = process.platform === 'linux';
    if (runtime !== null)
      await files.read_runtime_revision(runtime, app_revision, linux ? LINUX_EXECUTOR : undefined);
    options.signal.throwIfAborted();
    const directory = runtime ?? options.baseline_root;
    const node =
      linux || runtime === null ? options.baseline_node : join(directory, 'node/bin/node');
    const environment = copy_environment(options.environment);
    environment[MAC_RUNTIME_CHILD_ENV] = '1';
    delete environment[MAC_RUNTIME_READY_ENV];
    delete environment[MAC_RUNTIME_HANDOFF_ENV];
    delete environment[MACHINE_SERVICE_TRIAL_ENV];
    if (service_trial) environment[MACHINE_SERVICE_TRIAL_ENV] = '1';
    environment.PLAN_EXECUTOR_ROOT = options.paths.root;
    environment.PLAN_APP_ROOT = directory;
    environment.PLAN_APP_SHA = app_revision;
    environment.PATH = linux
      ? linux_executor_path(directory, node, environment.HOME ?? homedir())
      : join(directory, 'bin') + delimiter + (environment.PATH ?? '');
    if (ready !== null) environment[MAC_RUNTIME_READY_ENV] = ready;
    if (saved !== null) environment[MAC_RUNTIME_HANDOFF_ENV] = saved;
    const forwarded = [...args];
    if (revision.inline) forwarded[revision.index] = '--application-revision=' + app_revision;
    else forwarded[revision.index + 1] = app_revision;
    const child = options.child_processes.spawn(
      node,
      [
        join(directory, linux ? LINUX_EXECUTOR_ENTRYPOINT : 'server/executor/mac/entrypoint.ts'),
        ...forwarded,
      ],
      { cwd: directory, env: environment },
    );
    const owned = new OwnedProcess(child);
    child.stdout.pipe(process.stdout, { end: false });
    child.stderr.pipe(process.stderr, { end: false });
    try {
      await new Promise<void>((resolve, reject) => {
        child.once('spawn', resolve);
        child.once('error', reject);
      });
      return owned;
    } catch (error) {
      await owned.stop();
      throw error;
    }
  };
  const wait_exit = async (owned: OwnedProcess): Promise<void> => {
    let interrupted!: () => void;
    const signal = new Promise<void>((resolve) => {
      interrupted = resolve;
    });
    options.signal.addEventListener('abort', interrupted, { once: true });
    try {
      if (!options.signal.aborted) await Promise.race([owned.exited, signal]);
      if (options.signal.aborted) await owned.stop({ grace_seconds: SUPERVISOR_APP_STOP_SECONDS });
      else await owned.stop();
    } finally {
      options.signal.removeEventListener('abort', interrupted);
    }
  };
  try {
    if (!starts_service_trial) {
      current = await launch(
        selected?.runtime_path ?? null,
        selected?.application_revision ?? revision.value,
        null,
        handoff,
      );
    }
    while (true) {
      const entering_service_trial = current === undefined;
      if (current !== undefined) {
        await wait_exit(current);
        if (options.signal.aborted || status(current) !== MAC_RUNTIME_UPDATE_EXIT_CODE)
          return status(current);
      }
      const pending = entering_service_trial
        ? initial_pending
        : ((await read_record(files, join(root, 'pending.json'))) as MacRuntimeSelection | null);
      if (pending === null)
        throw new Error('Runtime requested a swap without a durable selection.');
      const ready = join(root, 'ready', randomUUID() + '.json');
      let launched = false,
        confirmed = false;
      try {
        current = await launch(
          pending.runtime_path,
          pending.application_revision,
          ready,
          join(root, 'handoff.json'),
          entering_service_trial,
        );
        launched = true;
      } catch (error) {
        files.assert_healthy();
        if (options.signal.aborted) throw error;
        console.warn(
          `Mac runtime launch failed request=${pending.request_id}:`,
          failure_detail(error),
        );
      }
      let read_failure: unknown = null;
      const deadline = performance.now() + MAC_RUNTIME_READY_TIMEOUT_SECONDS * 1000;
      while (launched && !options.signal.aborted && performance.now() < deadline) {
        let marker: Record<string, unknown> | null = null;
        try {
          marker = await read_record(files, ready);
        } catch (error) {
          files.assert_healthy();
          read_failure = error;
        }
        if (
          marker?.request_id === pending.request_id &&
          marker.application_revision === pending.application_revision
        ) {
          confirmed = true;
          break;
        }
        if (!current || current.child.exitCode !== null || current.child.signalCode !== null) break;
        try {
          await delay(
            Math.min(MAC_UPDATE_POLL_SECONDS * 1000, Math.max(0, deadline - performance.now())),
            undefined,
            { signal: options.signal },
          );
        } catch (error) {
          if ((error as Error).cause !== options.signal.reason) throw error;
        }
      }
      await files.remove_runtime_record(ready);
      if (confirmed && current) {
        await atomic_record(files, join(root, 'selection.json'), pending);
        selected = pending;
        await files.remove_runtime_record(join(root, 'pending.json'));
        continue;
      }
      if (read_failure !== null)
        console.warn(
          `Mac runtime ready marker was unreadable request=${pending.request_id}:`,
          failure_detail(read_failure),
        );
      await current?.stop({
        grace_seconds:
          process.platform === 'linux'
            ? LINUX_UPDATE_TERMINATE_SECONDS
            : MAC_UPDATE_TERMINATE_SECONDS,
      });
      await atomic_record(files, join(root, 'failure.json'), {
        request_id: pending.request_id,
        phase: 'reverted',
        detail: 'The new runtime did not confirm its VPS connection before the deadline.',
      });
      await files.remove_runtime_record(join(root, 'pending.json'));
      if (entering_service_trial) return 1;
      if (options.signal.aborted) return 128 + os_constants.signals.SIGTERM;
      current = await launch(
        selected?.runtime_path ?? null,
        selected?.application_revision ?? revision.value,
        null,
        join(root, 'handoff.json'),
      );
    }
  } finally {
    if (current !== undefined) await current.stop({ grace_seconds: SUPERVISOR_APP_STOP_SECONDS });
  }
}
