import { legacy_direct_identity_matches } from './logic/legacy_agent_identity.ts';
/** One-release retirement of historical direct children. Starts no agent.
 * Delete after every home runs one start of this release.
 */
import { readFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  OwnedUnitControl,
  StopOptions,
} from '../lifecycle/contracts.ts';
import { path_is_within } from '../core/path_containment.ts';
import { INSPECTION_SECONDS, STOP_SECONDS } from '../worker_supervision/config.ts';
import type { WorkerProcess } from '../worker_supervision/contracts.ts';
import { inspect_record, stop_record } from '../worker_supervision/inspection.ts';
import * as records from '../worker_supervision/records.ts';
import type { WorkerSupervisionRuntime } from '../worker_supervision/runtime.ts';
import { ORPHAN_AGENT_TERMINATION_GRACE_SECONDS } from '../executor/mac/configuration.ts';
import { legacy_custody_process_inspector } from '../executor/mac/platform.ts';
import type { LocalChildIdentity, LocalHostBootIdentity } from './local_child_contracts.ts';
import type {
  LocalChildCustodyInspection,
  LocalChildCustodyRow,
} from './local_child_custody_contracts.ts';
import { BackendCommands } from './snapshot/commands.ts';
import { control_retained_with_timeout } from './worker_unit/control.ts';

/** Marks a generation whose driver reports a supervision record before it spawns a child. */
export const SUPERVISED_CHILD_GENERATION_PREFIX = 'supervised-';
const UNIT = /^panels-worker-[0-9a-f]{32}\.service$/;
const PROPERTIES =
  '--property=LoadState,ActiveState,SubState,Result,MainPID,ControlGroup,Slice,KillMode';
interface RetainedUnit {
  readonly control: OwnedUnitControl;
  readonly finish: () => void;
}
export class LocalChildCustody implements LocalChildCustodyInspection {
  readonly #runtime: WorkerSupervisionRuntime;
  readonly #children: OwnedChildProcesses;
  readonly #commands: BackendCommands;
  readonly #legacy_process: ReturnType<typeof legacy_custody_process_inspector>;
  readonly #units = new Map<string, RetainedUnit>();
  constructor(options: {
    runtime: WorkerSupervisionRuntime;
    child_processes: OwnedChildProcesses;
    one_shot_children: OwnedOneShotChildren;
  }) {
    this.#runtime = options.runtime;
    this.#children = options.child_processes;
    this.#commands = new BackendCommands(options.one_shot_children);
    this.#legacy_process = legacy_custody_process_inspector(
      options.one_shot_children,
      options.runtime.clock,
    );
  }
  host_boot_identity(): Promise<LocalHostBootIdentity | null> {
    return this.#runtime
      .operation(async () => {
        try {
          return (await this.#runtime.context(this.#runtime.clock() + INSPECTION_SECONDS)).host;
        } catch (error) {
          console.warn('child custody: the host boot identity is unavailable', error);
          return null;
        }
      })
      .catch((error: unknown) => {
        console.warn('child custody: the host boot identity operation failed', error);
        return null;
      });
  }
  retirement_row_proven(
    row: LocalChildCustodyRow,
    options?: StopOptions,
    ticket_worker = false,
  ): Promise<boolean> {
    if (row.state === 'retired') return Promise.resolve(true);
    return this.#inspect(async (deadline) => {
      if (row.state === 'launch_pending') {
        if (row.supervision_record !== null)
          return this.inspect_supervision(row.supervision_record, undefined, { deadline });
        // A marked driver reports its record before it spawns, so a marked row without one never
        // spawned. Callers judge only rows no launch of this process is still writing.
        if (row.generation.startsWith(SUPERVISED_CHILD_GENERATION_PREFIX)) return true;
        const current = (await this.#runtime.context(deadline)).host;
        return row.boot !== null && current.host === row.host && current.boot !== row.boot;
      }
      if (
        process.platform === 'linux' &&
        ticket_worker &&
        row.supervision_record === null &&
        row.unit === null
      ) {
        const current = (await this.#runtime.context(deadline)).host;
        return row.boot !== null && current.host === row.host && current.boot !== row.boot;
      }
      if (!Number.isSafeInteger(row.pid) || row.pid! <= 0 || !row.birth || !row.boot || !row.host)
        return false;
      return this.#absent(
        {
          pid: row.pid!,
          birth: row.birth,
          boot: row.boot,
          unit: row.unit,
          supervision_record: row.supervision_record,
        },
        row.host,
        deadline,
      );
    }, options);
  }
  inspect_supervision(
    path: string,
    identity?: LocalChildIdentity,
    options?: StopOptions,
  ): Promise<boolean> {
    return this.#inspect(
      async (deadline) =>
        (await inspect_record(path, { runtime: this.#runtime, identity, deadline })).complete,
      options,
    );
  }
  stop_supervision(
    path: string,
    identity?: LocalChildIdentity,
    options?: StopOptions,
  ): Promise<boolean> {
    return this.#inspect(
      async (deadline) =>
        (await stop_record(path, { runtime: this.#runtime, identity, deadline })).complete,
      options,
      STOP_SECONDS,
    );
  }
  stop_recorded_child(
    identity: LocalChildIdentity,
    host: string | null,
    options?: StopOptions,
    ticket_worker = false,
  ): Promise<boolean> {
    if (
      process.platform === 'linux' &&
      ticket_worker &&
      identity.supervision_record === null &&
      identity.unit === null
    )
      return this.stop_recorded_child(identity, host, options).then(async () => {
        const current = await this.host_boot_identity();
        return current !== null && current.host === host && current.boot !== identity.boot;
      });
    return this.#inspect(
      async (deadline) => {
        if (identity.supervision_record !== null) {
          if ((await records.read(identity.supervision_record)).host !== host) return false;
          return this.stop_supervision(identity.supervision_record, identity, { deadline });
        }
        const { host: current, primitives } = await this.#runtime.context(deadline);
        if (current.host !== host) return false;
        if (current.boot !== identity.boot || (await this.#absent(identity, host, deadline)))
          return true;
        if (identity.unit !== null) {
          if (!UNIT.test(identity.unit)) return false;
          if (!this.#units.has(identity.unit)) {
            const finished = Promise.withResolvers<void>();
            const control = this.#children.retain_worker_unit({
              name: identity.unit,
              finished: finished.promise,
            });
            this.#units.set(identity.unit, { control, finish: finished.resolve });
          }
          if ((await this.#unit_control(identity.unit, 'stop', [], deadline))[0] !== 0)
            return false;
          await this.#unit_control(identity.unit, 'reset-failed', [], deadline);
          return this.#unit_retired(identity.unit, deadline);
        }
        // Historical Mac custody has no process handle. An operator must stop a survivor.
        if (!current.host.startsWith('linux:')) return false;
        const process = this.#process(identity);
        const observed = (await primitives.snapshot(deadline)).get(identity.pid);
        if (!legacy_direct_identity_matches({ ...identity, host }, current, observed)) return false;
        await primitives.signal(process, 15, deadline);
        const term_deadline = Math.min(
          deadline,
          this.#runtime.clock() + ORPHAN_AGENT_TERMINATION_GRACE_SECONDS,
        );
        while (this.#runtime.clock() < term_deadline) {
          if (await this.#absent(identity, host, deadline)) return true;
          await pause(Math.min(50, Math.max(0, term_deadline - this.#runtime.clock()) * 1000));
        }
        if (this.#runtime.clock() >= deadline) return false;
        await primitives.signal(process, 9, deadline);
        return this.#absent(identity, host, deadline);
      },
      options,
      ORPHAN_AGENT_TERMINATION_GRACE_SECONDS + INSPECTION_SECONDS,
    );
  }
  #process(identity: LocalChildIdentity): WorkerProcess {
    return {
      pid: identity.pid,
      birth: identity.birth,
      parent: 0,
      group: 0,
      session: 0,
      zombie: false,
    };
  }
  async #absent(
    identity: LocalChildIdentity,
    host: string | null,
    deadline: number,
  ): Promise<boolean> {
    try {
      if (identity.supervision_record !== null) {
        if ((await records.read(identity.supervision_record)).host !== host) return false;
        return this.inspect_supervision(identity.supervision_record, identity, { deadline });
      }
      const { host: current, primitives } = await this.#runtime.context(deadline);
      if (current.host !== host) return false;
      if (current.boot !== identity.boot) return true;
      if (current.host.startsWith('darwin:') && !/^\d+:\d+$/.test(identity.birth)) {
        if (
          await this.#legacy_process(
            { process_id: identity.pid, started: identity.birth },
            deadline,
          )
        )
          return false;
      } else if (!(await primitives.is_absent(this.#process(identity), deadline))) return false;
      return identity.unit === null || this.#unit_retired(identity.unit, deadline);
    } catch (error) {
      // Missing evidence does not prove retirement or cancel later stop escalation.
      console.warn('child custody: retirement evidence is unavailable', error);
      return false;
    }
  }
  async #unit_control(
    unit: string,
    action: 'show' | 'stop' | 'reset-failed',
    extra: readonly string[],
    deadline: number,
  ): Promise<readonly [number, string]> {
    const timeout = Math.min(10, deadline - this.#runtime.clock());
    if (timeout <= 0) throw new Error('Local custody inspection deadline expired');
    const retained = this.#units.get(unit);
    if (retained) return control_retained_with_timeout(retained.control, action, extra, timeout);
    const result = await this.#commands.run(['systemctl', '--user', action, unit, ...extra], {
      timeout_seconds: timeout,
    });
    return [result.exit_code, result.standard_output];
  }
  async #unit_retired(unit: string, deadline: number): Promise<boolean> {
    try {
      if (!UNIT.test(unit)) return false;
      const [code, output] = await this.#unit_control(unit, 'show', [PROPERTIES], deadline);
      const properties = Object.fromEntries(
        output
          .split('\n')
          .filter((line) => line.includes('='))
          .map((line) => {
            const separator = line.indexOf('=');
            return [line.slice(0, separator), line.slice(separator + 1)];
          }),
      );
      let retired = properties.LoadState === 'not-found';
      if (
        !retired &&
        code === 0 &&
        'ControlGroup' in properties &&
        ['inactive', 'failed'].includes(properties.ActiveState)
      ) {
        const group = properties.ControlGroup;
        if (group) {
          const root = '/sys/fs/cgroup';
          const path = resolve(root, group.replace(/^\/+/, ''), 'cgroup.events');
          retired =
            basename(group) === unit &&
            path_is_within(root, path) &&
            (await readFile(path, 'utf8')).split('\n').includes('populated 0');
        } else
          retired =
            properties.MainPID === '0' &&
            properties.ActiveState === 'inactive' &&
            properties.SubState === 'dead' &&
            properties.Result === 'success' &&
            properties.KillMode === 'control-group' &&
            properties.Slice === 'panels-workers.slice';
      }
      if (retired) {
        this.#units.get(unit)?.finish();
        this.#units.delete(unit);
      }
      return retired;
    } catch (error) {
      console.warn(`child custody: unit ${unit} retirement could not be checked`, error);
      return false;
    }
  }
  #inspect(
    operation: (deadline: number) => Promise<boolean>,
    options?: StopOptions,
    budget = INSPECTION_SECONDS,
  ): Promise<boolean> {
    return this.#runtime
      .operation(async () => {
        const deadline = options?.deadline ?? this.#runtime.clock() + budget;
        if (this.#runtime.clock() >= deadline) return false;
        try {
          return await operation(deadline);
        } catch (error) {
          console.warn('child custody: an inspection failed', error);
          return false;
        }
      })
      .catch((error: unknown) => {
        console.warn('child custody: an inspection operation failed', error);
        return false;
      });
  }
}
