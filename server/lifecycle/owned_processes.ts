import { spawn, spawnSync, fork, execFile } from 'node:child_process';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
  ForkOptions,
  ExecFileOptions,
  ExecFileException,
} from 'node:child_process';
import type {
  OwnedChildProcesses,
  OwnedWorkerUnit,
  OwnedUnitControl,
  OwnedOneShotChildren,
  StopOptions,
} from './contracts.ts';
import {
  monotonic_seconds,
  OWNED_PROCESS_KILL_WAIT_SECONDS,
  OWNED_PROCESS_STOP_GRACE_SECONDS,
} from './configuration.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { CONTROL_TIMEOUT_SECONDS } from '../conversation/worker_unit/configuration.ts';
import { worker_unit_prefix } from '../core/config.ts';

/** Native exit and stream closure are separate facts. Never signal a retired PID. */
export class ChildLifetime {
  readonly exited: Promise<void>;
  readonly closed: Promise<void>;
  #reaped: boolean;
  readonly child: ChildProcess;
  constructor(child: ChildProcess) {
    this.child = child;
    this.#reaped = child.exitCode != null || child.signalCode != null;
    const exit = Promise.withResolvers<void>();
    const close = Promise.withResolvers<void>();
    this.exited = exit.promise;
    this.closed = close.promise;
    if (this.#reaped) exit.resolve();
    let spawned = child.pid !== undefined;
    child.once('spawn', () => {
      spawned = true;
    });
    child.once('exit', () => {
      this.#reaped = true;
      exit.resolve();
    });
    child.on('error', (error) => {
      console.warn('owned process error', child.pid ?? child.spawnfile, error);
      if (!spawned && child.pid === undefined) {
        this.#reaped = true;
        exit.resolve();
      }
    });
    child.once('close', () => {
      this.#reaped = true;
      exit.resolve();
      close.resolve();
    });
  }
  signal(signal: NodeJS.Signals | number = 'SIGTERM', group = true): boolean {
    if (this.#reaped || this.child.exitCode != null || this.child.signalCode != null) return false;
    try {
      return group && process.platform !== 'win32' && this.child.pid !== undefined
        ? process.kill(-this.child.pid, signal)
        : this.child.kill(signal);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ESRCH') return false;
      throw error;
    }
  }
}
const lifetimes = new WeakMap<ChildProcess, ChildLifetime>();
export function child_lifetime(child: ChildProcess): ChildLifetime {
  let lifetime = lifetimes.get(child);
  if (!lifetime) {
    lifetime = new ChildLifetime(child);
    lifetimes.set(child, lifetime);
  }
  return lifetime;
}
export function signal_child(
  child: ChildProcess,
  signal: NodeJS.Signals | number = 'SIGTERM',
  group = true,
): boolean {
  return child_lifetime(child).signal(signal, group);
}
interface ChildRegistration {
  lifetime?: ChildLifetime;
  group: boolean;
  closed: Promise<void>;
}
interface UnitRegistration {
  actual: Promise<void>;
  control: OwnedUnitControl;
}
/** Actual native lifetimes belong to the invocation, independently of adapter outcomes. */
export class ChildProcesses implements OwnedChildProcesses {
  readonly #worker_unit_prefix = worker_unit_prefix();
  readonly #children = new Set<ChildRegistration>();
  readonly #units = new Map<OwnedWorkerUnit, UnitRegistration>();
  readonly #failures: unknown[] = [];
  #forcing = false;
  #sealed = false;
  seal(): void {
    this.#sealed = true;
  }
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
  ): ChildProcessWithoutNullStreams;
  spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess;
  spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess {
    if (this.#sealed) throw new Error('Native process admission is closed at shutdown');
    return this.#spawn(command, args, options);
  }
  #spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess {
    const detached = options.detached ?? process.platform !== 'win32';
    return this.#create(() => spawn(command, args, { ...options, detached }), detached);
  }
  fork(modulePath: string | URL, args: readonly string[], options: ForkOptions): ChildProcess {
    if (this.#sealed) throw new Error('Native process admission is closed at shutdown');
    return this.#create(() => fork(modulePath, [...args], options), options.detached === true);
  }
  execFile(
    command: string,
    args: readonly string[],
    options: ExecFileOptions,
    callback: (
      error: ExecFileException | null,
      stdout: string | Buffer,
      stderr: string | Buffer,
    ) => void,
  ): ChildProcess {
    if (this.#sealed) throw new Error('Native process admission is closed at shutdown');
    return this.#create(() => execFile(command, [...args], options, callback), false);
  }
  #create(create: () => ChildProcess, group: boolean): ChildProcess {
    if (this.#forcing) throw new Error('Native process admission is closed at hard shutdown');
    const { promise: closed, resolve } = Promise.withResolvers<void>();
    const registration: ChildRegistration = { group, closed };
    this.#children.add(registration);
    let child: ChildProcess;
    try {
      child = create();
    } catch (error) {
      this.#children.delete(registration);
      resolve();
      throw error;
    }
    registration.lifetime = child_lifetime(child);
    void registration.lifetime.closed.then(() => {
      this.#children.delete(registration);
      resolve();
    });
    if (this.#forcing) this.#kill(registration);
    return child;
  }
  retain_worker_unit(unit: OwnedWorkerUnit): OwnedUnitControl {
    if (
      !unit.name.startsWith(this.#worker_unit_prefix) ||
      !/^[a-f0-9]{32}\.service$/.test(unit.name.slice(this.#worker_unit_prefix.length))
    )
      throw new Error('Invalid owned worker unit identity');
    if (this.#forcing) throw new Error('Worker unit admission is closed at hard shutdown');
    if (this.#sealed) throw new Error('Worker unit admission is closed at shutdown');
    const existing = this.#units.get(unit);
    if (existing) return existing.control;
    const { promise: actual, resolve: release } = Promise.withResolvers<void>();
    const name = unit.name;
    const control: OwnedUnitControl = {
      spawn: (action, extra) => {
        if (this.#units.get(unit) !== registration)
          throw new Error('Retained Worker unit cleanup authority is no longer active');
        if (
          !['show', 'stop', 'reset-failed'].includes(action) ||
          (action === 'show'
            ? extra.length !== 1 ||
              ![
                '--property=LoadState',
                '--property=LoadState,ActiveState,SubState,Result,MainPID,ControlGroup,Slice,KillMode',
              ].includes(extra[0])
            : extra.length !== 0)
        )
          throw new Error('Invalid retained Worker unit cleanup command');
        return this.#spawn('systemctl', ['--user', action, name, ...extra], {
          stdio: ['ignore', 'pipe', 'pipe'],
        });
      },
    };
    const registration = { actual, control };
    this.#units.set(unit, registration);
    void unit.finished.then(
      () => {
        this.#units.delete(unit);
        release();
      },
      (error) => {
        // An unsuccessful stop is not evidence that the service no longer exists.
        this.#failures.push(error);
      },
    );
    return control;
  }
  async settled(): Promise<void> {
    while (this.#children.size || this.#units.size)
      await Promise.all(
        [...this.#children]
          .map((child) => child.closed)
          .concat([...this.#units.values()].map((unit) => unit.actual)),
      );
    if (this.#failures.length)
      throw new AggregateError(this.#failures, 'Owned native resources failed');
  }
  #kill(registration: ChildRegistration): void {
    registration.lifetime?.signal('SIGKILL', registration.group);
  }
  force_kill(force_deadline: number): void {
    this.#forcing = true;
    const failures: unknown[] = [];
    for (const child of this.#children) {
      try {
        this.#kill(child);
      } catch (error) {
        failures.push(error);
      }
    }
    const names = [...this.#units.keys()].map((unit) => unit.name);
    if (names.length) {
      const remaining = Math.min(
        CONTROL_TIMEOUT_SECONDS,
        force_deadline - performance.now() / 1000,
      );
      if (remaining <= 0)
        failures.push(new Error('Owned unit force budget expired before delivery'));
      else {
        const result = spawnSync(
          'systemctl',
          ['--user', 'kill', '--signal=SIGKILL', '--kill-whom=all', ...names],
          {
            shell: false,
            stdio: 'ignore',
            killSignal: 'SIGKILL',
            timeout: Math.max(1, Math.floor(remaining * 1000)),
          },
        );
        if (result.error) failures.push(result.error);
        else if (result.status !== 0)
          failures.push(
            new Error(
              `Owned unit force was not confirmed: status ${result.status}, signal ${result.signal}`,
            ),
          );
      }
    }
    if (failures.length)
      throw new AggregateError(
        failures,
        `Hard shutdown force was not fully confirmed: ${failures.map(String).join('; ')}`,
      );
  }
}

/** Only call with a child returned by the invocation's dedicated-group spawn. */
export function stop_one_shot_child(child: ChildProcess): void {
  signal_child(child, 'SIGKILL');
}

/** A command outcome is not a process-close boundary. This owner retains the latter. */
export class OneShotChildren implements OwnedOneShotChildren {
  readonly #controller = new AbortController();
  readonly #pending = new Set<Promise<void>>();
  readonly #stops = new Map<ChildProcess, () => void>();
  readonly #failures: unknown[] = [];
  #sealed = false;
  readonly child_processes: OwnedChildProcesses;
  constructor(child_processes: OwnedChildProcesses = new ChildProcesses()) {
    this.child_processes = child_processes;
  }
  get signal(): AbortSignal {
    return this.#controller.signal;
  }
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
    stop?: (child: ChildProcess) => void,
  ): ChildProcessWithoutNullStreams;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptions,
    stop?: (child: ChildProcess) => void,
  ): ChildProcess;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptions,
    stop: (child: ChildProcess) => void = stop_one_shot_child,
  ): ChildProcess {
    if (this.#sealed) throw new Error('One-shot process admission is closed');
    const { promise: actual, resolve: release } = Promise.withResolvers<void>();
    this.#pending.add(actual);
    let child: ChildProcess;
    try {
      child = this.child_processes.spawn(command, args, options);
    } catch (error) {
      this.#pending.delete(actual);
      release();
      throw error;
    }
    void child_lifetime(child).closed.then(() => {
      this.#stops.delete(child);
      this.#pending.delete(actual);
      release();
    });
    this.#stops.set(child, () => stop(child));
    // A synchronous injected spawn can reenter shutdown before it returns its child.
    if (this.#sealed) this.#stop(child);
    return child;
  }
  #stop(child: ChildProcess): void {
    try {
      this.#stops.get(child)?.();
    } catch (error) {
      this.#failures.push(error);
    }
  }
  seal(): void {
    if (this.#sealed) return;
    this.#sealed = true;
    // Stop the owned handles even if an abort listener throws or starts more work.
    try {
      this.#controller.abort(new Error('One-shot processes stopped at shutdown'));
    } finally {
      for (const child of this.#stops.keys()) this.#stop(child);
    }
  }
  async settled(): Promise<void> {
    while (this.#pending.size) await Promise.all([...this.#pending]);
    if (this.#failures.length)
      throw new AggregateError(this.#failures, 'One-shot children did not close cleanly');
  }
}

export interface ProcessStopOptions {
  grace_seconds: number;
  kill_wait_seconds: number;
  preserve_output?: boolean;
}
export class OwnedProcess {
  readonly child: ChildProcessWithoutNullStreams;
  readonly exited: Promise<void>;
  readonly output_failed: Promise<Error>;
  private output_failure: Error | null = null;
  private stop_task: Promise<void> | null = null;
  private signal_sent = false;
  constructor(child: ChildProcessWithoutNullStreams) {
    this.child = child;
    const { promise, resolve } = Promise.withResolvers<Error>();
    this.output_failed = promise;
    const failed = (error: Error) => {
      if (this.output_failure !== null) return;
      this.output_failure = error;
      resolve(error);
    };
    child.stdout.on('error', failed);
    child.stderr.on('error', failed);
    this.exited = child_lifetime(child).exited;
    child.stdin.on('error', () => {});
  }
  get output_error(): Error | null {
    return this.output_failure;
  }
  get killed(): boolean {
    return this.child.killed || this.signal_sent;
  }
  /** Defaults apply to omitted waits. A deadline clamps both waits to the time left. */
  stop(options: Partial<ProcessStopOptions> & Partial<StopOptions> = {}): Promise<void> {
    if (this.stop_task !== null) return this.stop_task;
    const remaining =
      options.deadline === undefined
        ? Infinity
        : Math.max(0, options.deadline - monotonic_seconds());
    const grace_seconds = Math.min(
      options.grace_seconds ?? OWNED_PROCESS_STOP_GRACE_SECONDS,
      remaining,
    );
    const task = this.stop_group({
      ...options,
      grace_seconds,
      kill_wait_seconds: Math.min(
        options.kill_wait_seconds ?? OWNED_PROCESS_KILL_WAIT_SECONDS,
        Math.max(0, remaining - grace_seconds),
      ),
    });
    this.stop_task = task;
    void task.catch(() => {
      this.stop_task = null;
    });
    return task;
  }
  signal(signal: NodeJS.Signals | number = 'SIGTERM'): boolean {
    const sent = signal_child(this.child, signal);
    this.signal_sent ||= sent;
    return sent;
  }
  private async stop_group({
    grace_seconds,
    kill_wait_seconds,
    preserve_output = false,
  }: ProcessStopOptions): Promise<void> {
    this.child.stdin.end();
    this.signal('SIGTERM');
    if (!(await observe_promise(this.exited, grace_seconds * 1000)).completed) {
      this.signal('SIGKILL');
      if (!(await observe_promise(this.exited, kill_wait_seconds * 1000)).completed)
        throw new Error('Child process termination remains unconfirmed');
    }
    // Never signal a retired group, even if descendants still hold output pipes.
    if (!preserve_output) {
      this.child.stdout.destroy();
      this.child.stderr.destroy();
    }
    this.child.stdin.destroy();
  }
}

export class ChildProcessWouldNotStart extends Error {}
export interface OwnedProcessSpawnOptions {
  child_processes: OwnedChildProcesses;
  /** The caller retains startup cleanup before any spawn-event wait. */
  on_created?: (owned: OwnedProcess) => void;
  environment: Readonly<Record<string, string>>;
  working_directory: string;
  /** A preflight receives EOF without changing the main child's piped stream API. */
  close_stdin?: boolean;
}
/** Each child owns a process group. Exit and pipe closure are independent facts. */
export async function spawn_owned_process(
  argv: readonly string[],
  options: OwnedProcessSpawnOptions,
): Promise<OwnedProcess> {
  if (argv.length === 0) throw new ChildProcessWouldNotStart('the command is empty');
  let child: ChildProcessWithoutNullStreams;
  try {
    child = options.child_processes.spawn(argv[0]!, argv.slice(1), {
      env: { ...options.environment },
      cwd: options.working_directory,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    });
  } catch (error) {
    throw new ChildProcessWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  const owned = new OwnedProcess(child);
  options.on_created?.(owned);
  try {
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    });
    if (options.close_stdin) child.stdin.end();
  } catch (error) {
    if (!options.on_created) await owned.stop();
    throw new ChildProcessWouldNotStart(error instanceof Error ? error.message : String(error));
  }
  return owned;
}
