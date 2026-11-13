import { spawn, spawnSync } from 'node:child_process';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import type { OwnedChildProcesses, OwnedWorkerUnit, OwnedUnitControl } from './contracts.ts';
import { CONTROL_TIMEOUT_SECONDS } from '../conversation/worker_unit/configuration.ts';

interface ChildRegistration {
  child: ChildProcess | null;
  exit_observed: boolean;
  closed: Promise<void>;
}
interface UnitRegistration {
  actual: Promise<void>;
  control: OwnedUnitControl;
}
/** Actual native lifetimes belong to the invocation, independently of adapter outcomes. */
export class ChildProcesses implements OwnedChildProcesses {
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
    if (this.#forcing) throw new Error('Native process admission is closed at hard shutdown');
    let release!: () => void;
    const registration: ChildRegistration = {
      child: null,
      exit_observed: false,
      closed: new Promise<void>((resolve) => {
        release = resolve;
      }),
    };
    this.#children.add(registration);
    let child: ChildProcess;
    try {
      child = spawn(command, args, { ...options, detached: process.platform !== 'win32' });
    } catch (error) {
      this.#children.delete(registration);
      release();
      throw error;
    }
    registration.child = child;
    child.once('exit', () => {
      registration.exit_observed = true;
    });
    child.on('error', () => {
      if (child.pid === undefined) registration.exit_observed = true;
    });
    child.once('close', () => {
      registration.exit_observed = true;
      this.#children.delete(registration);
      release();
    });
    if (this.#forcing) this.#kill(registration);
    return child;
  }
  retain_worker_unit(unit: OwnedWorkerUnit): OwnedUnitControl {
    if (!/^panels-worker-[a-f0-9]{32}\.service$/.test(unit.name))
      throw new Error('Invalid owned worker unit identity');
    if (this.#forcing) throw new Error('Worker unit admission is closed at hard shutdown');
    if (this.#sealed) throw new Error('Worker unit admission is closed at shutdown');
    const existing = this.#units.get(unit);
    if (existing) return existing.control;
    let release!: () => void;
    const actual = new Promise<void>((resolve) => {
      release = resolve;
    });
    const name = unit.name;
    const control: OwnedUnitControl = {
      spawn: (action, extra) => {
        if (this.#units.get(unit) !== registration)
          throw new Error('Retained Worker unit cleanup authority is no longer active');
        if (
          !['show', 'stop', 'reset-failed'].includes(action) ||
          (action === 'show'
            ? extra.length !== 1 || extra[0] !== '--property=LoadState'
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
    const child = registration.child;
    if (
      !child?.pid ||
      registration.exit_observed ||
      child.exitCode !== null ||
      child.signalCode !== null
    )
      return;
    try {
      if (process.platform === 'win32') child.kill('SIGKILL');
      else process.kill(-child.pid, 'SIGKILL');
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ESRCH')) throw error;
    }
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
