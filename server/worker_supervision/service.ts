/** Launch authorization and durable custody wrap the caller's existing native owner. */
import { accessSync, constants, statSync } from 'node:fs';
import { lstat, mkdir, readdir } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { setTimeout as pause } from 'node:timers/promises';
import { observe_shutdown } from '../conversation/shutdown.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { monotonic_seconds, SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
import { observe_promise } from '../core/promise_observer.ts';
import type { LocalChildIdentity } from '../conversation/local_child_contracts.ts';
import type {
  CleanupResult,
  InspectionContext,
  ProcessObservation,
  WorkerProcessRecord,
} from './contracts.ts';
import {
  INSPECTION_SECONDS,
  NATIVE_STOP_SECONDS,
  RECORD_ROOT_ENV,
  START_SECONDS,
  STOP_SECONDS,
} from './config.ts';
import { alive, same_process, validate_pending_shape } from './logic/ownership.ts';
import {
  cleanup_result,
  failure_message,
  inspect_record,
  observe_unlocked,
  stop_record,
} from './inspection.ts';
import * as records from './records.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';
import { local_process_identity } from './identity.ts';
import { cleanup_transport } from '../platform_runtime/darwin/launchd/transport.ts';

const expand_home = (path: string): string =>
  path === '~' ? homedir() : path.startsWith('~/') ? join(homedir(), path.slice(2)) : path;
/**
 * Removes a finished record no custody row names. Pruning is best effort: a busy or unreadable
 * record, or an unknown set of records in use, leaves the record for a later scan.
 */
async function prune_complete_record(
  path: string,
  runtime: WorkerSupervisionRuntime,
  record_paths_in_use: () => ReadonlySet<string>,
): Promise<void> {
  // Record names are random and unique, so a name stays the same under any spelling of the
  // root, including a symlinked or relocated data folder. A path string would not.
  const in_use = () => new Set([...record_paths_in_use()].map((named) => basename(named)));
  try {
    if (in_use().has(basename(path))) return;
    await records.locked(
      path,
      runtime.clock() + INSPECTION_SECONDS,
      async () => {
        let record: WorkerProcessRecord;
        try {
          record = await records.read(path);
        } catch (error) {
          // Locking a removed record created a fresh lock file. Remove that too.
          if ((error as NodeJS.ErrnoException).code === 'ENOENT') await records.remove(path);
          return;
        }
        // Ask again under the lock. A driver names its record before it spawns, so a finished
        // record is named already or never will be; the earlier answer can miss a newer launch.
        if (record.state !== 'complete' || in_use().has(basename(path))) return;
        // A finished Mac job's transport directory is named only by its record.
        if (record.launchd_socket_directory)
          await cleanup_transport(record.launchd_socket_directory);
        await records.remove(path);
        runtime.active_jobs.delete(path);
      },
      { clock: runtime.clock, wait: false },
    );
  } catch {
    /* Keeping a finished record costs one more scan entry; it never fails a launch. */
  }
}
export class WorkerSupervisor {
  readonly environment: Record<string, string>;
  readonly working_directory: string;
  readonly root: string;
  readonly path: string;
  readonly #runtime: WorkerSupervisionRuntime;
  readonly #native_stops = new Set<Promise<void>>();
  #ended = false;
  #stopping: Promise<CleanupResult> | null = null;
  #proxy_pid: number | null = null;
  private constructor(
    environment: Readonly<Record<string, string>>,
    working_directory: string,
    root: string,
    path: string,
    runtime: WorkerSupervisionRuntime,
  ) {
    this.environment = { ...environment, [RECORD_ROOT_ENV]: root };
    delete this.environment.TMUX;
    delete this.environment.STY;
    this.working_directory = working_directory;
    this.root = root;
    this.path = path;
    this.#runtime = runtime;
  }
  static create(
    environment: Readonly<Record<string, string>>,
    working_directory: string,
    options: {
      runtime: WorkerSupervisionRuntime;
      conversation_id: string;
      /** From `worker_ticket_id(resolved_start)`. Null keys the record by conversation. */
      worker_ticket_id: string | null;
    },
  ): Promise<WorkerSupervisor> {
    const { runtime, conversation_id, worker_ticket_id } = options;
    // A Worker's record is its Ticket's: a new Worker stops the previous one. Every other
    // record writes no Ticket, so it is keyed by its own conversation alone.
    const ticket_id = worker_ticket_id ?? '';
    const same_key = (record: WorkerProcessRecord): boolean =>
      record.ticket_id === ticket_id &&
      (worker_ticket_id !== null || record.conversation_id === conversation_id);
    // Only a Worker launch speaks of its Ticket and of Worker custody.
    const words =
      worker_ticket_id === null
        ? {
            holder: 'Conversation',
            process_kind: 'agent',
            subject: 'Agent',
            supervision: 'Conversation supervision',
            foreign: 'Prior custody of this conversation belongs to another machine',
          }
        : {
            holder: 'Ticket',
            process_kind: 'Worker',
            subject: 'Worker',
            supervision: 'Worker supervision',
            foreign: 'Prior Worker custody belongs to another machine',
          };
    return runtime.operation(async () => {
      if (runtime.closed) throw new Error(`${words.supervision} is closed`);
      const configured = environment[RECORD_ROOT_ENV] || process.env[RECORD_ROOT_ENV];
      const db = environment.PLAN_DB_PATH;
      if (!configured && !db) throw new Error(`${words.subject} process record root is missing`);
      const root = configured
        ? resolve(expand_home(configured))
        : join(dirname(resolve(expand_home(db!))), 'worker-processes');
      await mkdir(root, { mode: 0o700, recursive: true });
      const info = await lstat(root);
      if (!info.isDirectory() || info.uid !== process.getuid!() || (info.mode & 0o077) !== 0)
        throw new Error(`${words.subject} process record root must be private and owned`);
      const context = await runtime.context(runtime.clock() + INSPECTION_SECONDS);
      for (const name of (await readdir(root)).filter((name) => name.endsWith('.json')).sort()) {
        const prior = join(root, name);
        let previous: WorkerProcessRecord;
        try {
          previous = await records.read(prior);
        } catch (error) {
          console.error('Worker process record could not be read', prior, error);
          continue;
        }
        if (previous.state === 'complete') {
          if (same_key(previous)) {
            validate_pending_shape(previous);
            runtime.active_jobs.delete(prior);
          }
          if (runtime.record_paths_in_use)
            await prune_complete_record(prior, runtime, runtime.record_paths_in_use);
          continue;
        }
        if (!same_key(previous)) continue;
        if (previous.launchd && runtime.active_jobs.has(prior))
          throw new Error(`${words.holder} already has retained ${words.process_kind} job custody`);
        const inspected = await inspect_record(prior, { runtime, context });
        if (inspected.complete) continue;
        if (context.host.host !== previous.host) throw new Error(words.foreign);
        if (!previous.launchd && context.host.boot === previous.boot) {
          const table = await context.primitives.snapshot(runtime.clock() + INSPECTION_SECONDS);
          if (previous.owner != null && alive(previous.owner, table))
            throw new Error(
              `${words.holder} already has retained ${words.process_kind} process custody`,
            );
        }
        const stopped = await stop_record(prior, { runtime, context });
        if (!stopped.complete) throw new Error(stopped.reason);
      }
      const owner = runtime.jobs
        ? null
        : (await context.primitives.snapshot(runtime.clock() + INSPECTION_SECONDS)).get(
            process.pid,
          );
      if (!runtime.jobs && !owner)
        throw new Error(`${words.subject} supervisor identity is unavailable`);
      if (runtime.closed) throw new Error(`${words.supervision} is closed`);
      const path = join(root, randomUUID().replaceAll('-', '') + '.json');
      await records.write(path, {
        version: 1,
        ticket_id,
        host: context.host.host,
        boot: context.host.boot,
        owner,
        root: null,
        processes: [],
        isolated: false,
        state: 'pending',
        launch_phase: 'pending',
        conversation_id,
        source_child_id: environment.PLAN_EXECUTOR_CHILD_ID ?? null,
        source_incarnation: environment.PLAN_EXECUTOR_SOURCE_INCARNATION ?? null,
        reason: 'Launch pending',
        worker_priority: worker_ticket_id !== null,
        ...(runtime.jobs ? { launchd: runtime.jobs.identity(root, path.slice(-37, -5)) } : {}),
      });
      if (runtime.jobs) runtime.active_jobs.add(path);
      return new WorkerSupervisor(environment, working_directory, root, path, runtime);
    });
  }
  argv(original: readonly string[]): readonly string[] {
    if (!original.length) throw new Error('Worker command is empty');
    let executable = original[0];
    const search_path = this.environment.PATH ?? '/bin:/usr/bin';
    const candidates = executable.includes('/')
      ? [isAbsolute(executable) ? executable : resolve(this.working_directory, executable)]
      : search_path === ''
        ? []
        : search_path
            .split(':')
            .map((entry) =>
              entry === '' ? executable : `${entry}${entry.endsWith('/') ? '' : '/'}${executable}`,
            );
    executable =
      candidates.find((candidate) => {
        try {
          return statSync(candidate).isFile() && (accessSync(candidate, constants.X_OK), true);
        } catch {
          return false;
        }
      }) ?? '';
    if (!executable) throw new Error(`Worker executable is unavailable: ${original[0]}`);
    return [
      process.execPath,
      fileURLToPath(
        new URL(
          this.#runtime.jobs ? '../platform_runtime/darwin/launchd/proxy.ts' : './launch.ts',
          import.meta.url,
        ),
      ),
      this.path,
      executable,
      ...original.slice(1),
    ];
  }
  cancel_unspawned(): Promise<void> {
    return this.#runtime.operation(() =>
      records.locked(
        this.path,
        this.#runtime.clock() + INSPECTION_SECONDS,
        async () => {
          const record = await records.read(this.path);
          validate_pending_shape(record);
          if (record.launchd) {
            record.launch_revoked = true;
            await records.write(this.path, record);
            return;
          }
          if (record.root != null) throw new Error('Cannot cancel a registered Worker process');
          Object.assign(record, {
            state: 'complete',
            launch_revoked: true,
            reason: 'The OS refused spawn before a child existed',
          });
          await records.write(this.path, record);
        },
        { clock: this.#runtime.clock },
      ),
    );
  }
  spawned(pid: number): Promise<void> {
    if (this.#runtime.jobs) {
      this.#proxy_pid = pid;
      return Promise.resolve();
    }
    return this.#runtime.operation(async () => {
      const deadline = this.#runtime.clock() + INSPECTION_SECONDS;
      await records.locked(
        this.path,
        deadline,
        async () => {
          const record = await records.read(this.path);
          if (record.launch_revoked || record.state === 'complete')
            throw new Error('Worker launch authorization was revoked');
          validate_pending_shape(record);
          if (record.root != null) {
            if (record.root.pid !== pid)
              throw new Error('Worker launch registered a different root');
            return;
          }
          const first = (await this.#runtime.primitives.snapshot(deadline)).get(pid);
          const second = (await this.#runtime.primitives.snapshot(deadline)).get(pid);
          if (first && same_process(first, second) && first.parent === process.pid) {
            Object.assign(record, { root: first, processes: [first], state: 'spawned' });
            await records.write(this.path, record);
          }
        },
        { clock: this.#runtime.clock },
      );
    });
  }
  started(pid: number): Promise<void> {
    return this.#runtime.operation(async () => {
      await this.spawned(pid);
      const deadline = this.#runtime.clock() + START_SECONDS;
      while (this.#runtime.clock() < deadline) {
        const record = await records.read(this.path);
        if (record.launchd) {
          if (record.state === 'bootstrap_failed' || record.launch_revoked || this.#ended)
            throw new Error(record.reason ?? 'Worker launch failed');
          if (
            record.state === 'running' &&
            (await this.#runtime.jobs!.status(record.launchd, deadline)) === 'running'
          )
            return;
          await pause(10);
          continue;
        }
        if (
          record.root != null &&
          (record.state === 'running' || record.state === 'bootstrap_failed')
        ) {
          if (record.root.pid !== pid) throw new Error('Worker launch registered a different root');
          if (record.state === 'bootstrap_failed')
            throw new Error(record.reason ?? 'Worker bootstrap failed');
          await this.observe();
          if (!this.#ended)
            this.#runtime.observer.register(this.path, (context, observation) =>
              this.#periodic(context, observation),
            );
          return;
        }
        await pause(10);
      }
      await records.locked(
        this.path,
        this.#runtime.clock() + INSPECTION_SECONDS,
        async () => {
          const record = await records.read(this.path);
          record.launch_failed = true;
          await records.write(this.path, record);
        },
        { clock: this.#runtime.clock },
      );
      throw new Error('Worker process registration deadline expired');
    });
  }
  identity(): Promise<LocalChildIdentity | null> {
    return this.#runtime.operation(async () => {
      try {
        const record = await records.read(this.path),
          root = record.root;
        if (record.launchd && record.boot && this.#proxy_pid !== null) {
          // Preserve the external custody shape. The proxy incarnation is diagnostic;
          // all lifecycle authority comes from the exact job and journal.
          const diagnostic = await local_process_identity(this.#proxy_pid, this.#runtime);
          return diagnostic === null
            ? null
            : {
                ...diagnostic,
                unit: `${record.launchd.domain}/${record.launchd.label}`,
                supervision_record: this.path,
              };
        }
        return root == null || !record.boot
          ? null
          : {
              pid: root.pid,
              birth: root.birth,
              boot: record.boot,
              unit: null,
              supervision_record: this.path,
            };
      } catch (error) {
        console.error('Worker process identity could not be read', this.path, error);
        return null;
      }
    });
  }
  observe(): Promise<void> {
    return this.#runtime.operation(async () => {
      if (this.#ended) return;
      const deadline = this.#runtime.clock() + INSPECTION_SECONDS;
      await records.locked(
        this.path,
        deadline,
        async () => {
          await observe_unlocked(this.path, { runtime: this.#runtime, deadline });
        },
        { clock: this.#runtime.clock },
      );
    });
  }
  async #periodic(
    context: InspectionContext | null,
    observation: ProcessObservation | null,
  ): Promise<void> {
    if (this.#ended) return;
    const deadline = this.#runtime.clock() + INSPECTION_SECONDS;
    try {
      if (!context || !observation) throw new Error('A process observation failed');
      await records.locked(
        this.path,
        deadline,
        async () => {
          if (!this.#ended)
            await observe_unlocked(this.path, {
              runtime: this.#runtime,
              deadline,
              context,
              observation,
            });
        },
        { clock: this.#runtime.clock, wait: false },
      );
    } catch (error) {
      if (error instanceof Error && error.name === 'TimeoutError') return;
      try {
        await records.locked(
          this.path,
          deadline,
          async () => {
            const record = await records.read(this.path);
            const observation_error = failure_message(error);
            if (record.observation_error !== observation_error) {
              console.error('Worker process observation failed', this.path, error);
              record.observation_error = observation_error;
              await records.write(this.path, record);
            }
          },
          { clock: this.#runtime.clock, wait: false },
        );
      } catch (diagnostic) {
        console.error(
          'Worker process observation diagnostic was not recorded',
          this.path,
          diagnostic,
        );
        /* Retain prior custody when another writer or an I/O failure prevents a diagnostic. */
      }
    }
  }
  stop(
    native_stop?: () => Promise<void>,
    options: { deadline?: number } = {},
  ): Promise<CleanupResult> {
    if (this.#stopping) return this.#stopping;
    const task = this.#runtime.operation(() =>
      this.#stop(native_stop, options.deadline ?? this.#runtime.clock() + STOP_SECONDS),
    );
    this.#stopping = task;
    void task.then(
      (result) => {
        if (!result.complete && this.#stopping === task) this.#stopping = null;
      },
      () => {
        if (this.#stopping === task) this.#stopping = null;
      },
    );
    return task;
  }
  async #stop(
    native_stop: (() => Promise<void>) | undefined,
    deadline: number,
  ): Promise<CleanupResult> {
    this.#ended = true;
    await this.#runtime.observer.unregister(this.path);
    let context: InspectionContext;
    try {
      context = await this.#runtime.context(deadline);
      await records.locked(
        this.path,
        deadline,
        async () => {
          await observe_unlocked(this.path, {
            runtime: this.#runtime,
            deadline,
            context,
            fresh: true,
          });
        },
        { clock: this.#runtime.clock },
      );
    } catch (error) {
      try {
        await records.locked(
          this.path,
          deadline,
          async () => {
            const record = await records.read(this.path);
            Object.assign(record, {
              state: 'not_proven',
              observation_error: failure_message(error),
            });
            await records.write(this.path, record);
          },
          { clock: this.#runtime.clock },
        );
      } catch (diagnostic) {
        console.error('Worker process observation failure was not recorded', this.path, diagnostic);
        /* Missing ownership evidence never grants native cleanup authority. */
      }
      return cleanup_result(false, `Process observation failed: ${failure_message(error)}`);
    }
    const diagnostics: string[] = [];
    if (native_stop) {
      const native = Promise.resolve().then(native_stop);
      this.#native_stops.add(native);
      void native.finally(() => this.#native_stops.delete(native)).catch(() => {});
      try {
        const observed = await observe_promise(
          native,
          Math.max(0, Math.min(NATIVE_STOP_SECONDS, deadline - this.#runtime.clock())) * 1000,
        );
        if (!observed.completed) diagnostics.push('Native Worker stop deadline expired');
      } catch (error) {
        diagnostics.push(`Native Worker stop failed: ${failure_message(error)}`);
      }
    }
    const result = await stop_record(this.path, { runtime: this.#runtime, deadline, context });
    return diagnostics.length
      ? { ...result, reason: result.reason + '; ' + diagnostics.join('; ') }
      : result;
  }
  settled(
    options: StopOptions = { deadline: monotonic_seconds() + SHUTDOWN_GRACE_SECONDS },
  ): Promise<void> {
    return observe_shutdown(
      this.shutdown_settled,
      options,
      'Native Worker cleanup remains unsettled',
    );
  }
  get shutdown_settled(): Promise<void> {
    return this.#settle_native();
  }
  async #settle_native(): Promise<void> {
    while (this.#native_stops.size) await Promise.allSettled([...this.#native_stops]);
  }
}
