/** Retain agent identities across executor replacement, including reparented tools. */
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { FifoLock } from '../../conversation/runtime/state.ts';
import {
  observe_shutdown,
  is_shutdown_business_cancellation,
} from '../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../lifecycle/contracts.ts';
import type { OwnedOneShotChildren, StopOptions } from '../../lifecycle/contracts.ts';
import {
  MAC_AGENT_PROCESS_POLL_MS,
  MAC_AGENT_PROCESS_KILL_WAIT_SECONDS,
} from '../configuration.ts';
import { AGENT_CHILD_NICE, ORPHAN_AGENT_TERMINATION_GRACE_SECONDS } from './configuration.ts';
import { atomic_record } from './runtime/records.ts';
import type { RuntimeRecordIO } from './runtime/records.ts';
import { mac_agent_process_platform } from './platform.ts';
import type {
  AgentProcessIdentity,
  AgentProcessSnapshot,
  AgentProcessPlatform,
} from './platform.ts';

export interface AgentProcessesOptions {
  readonly run: string;
  readonly parent_process_id: number;
  readonly files: RuntimeRecordIO;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly warn: (message: string) => void;
  readonly platform?: AgentProcessPlatform;
}
const identity_key = (identity: AgentProcessIdentity) =>
  `${identity.process_id}:${identity.started}`;
export class AgentProcesses {
  private readonly options: AgentProcessesOptions;
  private readonly platform: AgentProcessPlatform;
  private readonly path: string;
  private readonly lock = new FifoLock();
  private readonly cancellation = new AbortController();
  private readonly operations = new Set<Promise<void>>();
  private readonly lowered = new Set<string>();
  private closed = false;
  constructor(options: AgentProcessesOptions) {
    this.options = options;
    this.path = join(options.run, 'agent-processes.json');
    this.platform = options.platform ?? mac_agent_process_platform(options.one_shot_children);
  }
  private schedule(
    work: (signal: AbortSignal) => Promise<void>,
    signal?: AbortSignal,
  ): Promise<void> {
    if (this.closed)
      return Promise.reject(new ServerLifecycleError('Mac agent process tracking is stopped.'));
    const cancellation = signal
      ? AbortSignal.any([signal, this.cancellation.signal])
      : this.cancellation.signal;
    const task = this.lock.run(async () => {
      cancellation.throwIfAborted();
      await work(cancellation);
    });
    this.operations.add(task);
    void task.then(
      () => this.operations.delete(task),
      () => this.operations.delete(task),
    );
    return task;
  }
  private async read(): Promise<AgentProcessIdentity[]> {
    try {
      const text = await this.options.files.read_runtime_record(this.path);
      const record: unknown = text === null ? null : JSON.parse(text);
      const entries =
        record && typeof record === 'object' && 'processes' in record ? record.processes : null;
      if (!Array.isArray(entries)) return [];
      return entries
        .filter(
          (value): value is AgentProcessIdentity =>
            value !== null &&
            typeof value === 'object' &&
            Number.isSafeInteger(value.process_id) &&
            value.process_id > 0 &&
            typeof value.started === 'string' &&
            value.started.length > 0,
        )
        .map(({ process_id, started }) => ({ process_id, started }));
    } catch (error) {
      this.cancellation.signal.throwIfAborted();
      this.options.warn(
        `Agent process record unavailable: ${error instanceof Error ? error.name : 'Error'}`,
      );
      return [];
    }
  }
  private write(processes: readonly AgentProcessIdentity[]): Promise<void> {
    return atomic_record(this.options.files, this.path, { processes });
  }
  private descendants(
    table: readonly AgentProcessSnapshot[],
    roots: ReadonlySet<number>,
  ): AgentProcessIdentity[] {
    const children = new Map<number, AgentProcessSnapshot[]>();
    for (const row of table) {
      const list = children.get(row.parent_process_id) ?? [];
      list.push(row);
      children.set(row.parent_process_id, list);
    }
    const seen = new Set(roots),
      waiting = [...roots],
      result: AgentProcessIdentity[] = [];
    while (waiting.length)
      for (const row of children.get(waiting.shift()!) ?? []) {
        if (seen.has(row.process_id)) continue;
        seen.add(row.process_id);
        waiting.push(row.process_id);
        result.push({ process_id: row.process_id, started: row.started });
      }
    return result;
  }
  record_children(): Promise<void> {
    return this.schedule(async (signal) => {
      const table = await this.platform.snapshot(signal);
      signal.throwIfAborted();
      const live = new Map(table.map((row) => [row.process_id, row.started]));
      const recorded = (await this.read()).filter(
        (row) => live.get(row.process_id) === row.started,
      );
      const known = new Set(recorded.map(identity_key));
      for (const row of this.descendants(table, new Set([this.options.parent_process_id]))) {
        signal.throwIfAborted();
        const key = identity_key(row);
        if (!this.lowered.has(key)) {
          try {
            if (!(await this.platform.lower_priority(row, AGENT_CHILD_NICE, signal))) continue;
          } catch (error) {
            signal.throwIfAborted();
            this.options.warn(
              `Agent priority update failed: ${error instanceof Error ? error.name : 'Error'}`,
            );
          }
          this.lowered.add(key);
        }
        if (!known.has(key)) {
          recorded.push(row);
          known.add(key);
        }
      }
      for (const key of this.lowered) if (!known.has(key)) this.lowered.delete(key);
      signal.throwIfAborted();
      await this.write(recorded);
    });
  }
  end_orphans(cancellation?: AbortSignal): Promise<void> {
    return this.schedule(async (signal) => {
      const recorded = await this.read();
      if (!recorded.length) return;
      const table = await this.platform.snapshot(signal),
        live = new Map(table.map((row) => [row.process_id, row.started]));
      const roots = recorded.filter((row) => live.get(row.process_id) === row.started);
      let remaining = [
        ...new Map(
          [...roots, ...this.descendants(table, new Set(roots.map((row) => row.process_id)))].map(
            (row) => [identity_key(row), row],
          ),
        ).values(),
      ];
      // Keep discovered descendants recoverable if startup is cancelled after SIGTERM.
      signal.throwIfAborted();
      await this.write(remaining);
      for (const signum of ['SIGTERM', 'SIGKILL'] as const) {
        for (const row of remaining) {
          signal.throwIfAborted();
          try {
            await this.platform.signal(row, signum, signal);
          } catch (error) {
            signal.throwIfAborted();
            if ((error as NodeJS.ErrnoException).code !== 'EPERM') throw error;
            this.options.warn(`Cannot signal agent process ${row.process_id}.`);
          }
        }
        const deadline =
          performance.now() +
          (signum === 'SIGTERM'
            ? ORPHAN_AGENT_TERMINATION_GRACE_SECONDS
            : MAC_AGENT_PROCESS_KILL_WAIT_SECONDS) *
            1000;
        while (remaining.length && performance.now() < deadline) {
          await sleep(MAC_AGENT_PROCESS_POLL_MS, undefined, { signal }).catch((error: unknown) => {
            if (error instanceof Error && error.cause === signal.reason) throw signal.reason;
            throw error;
          });
          const current = new Map(
            (await this.platform.snapshot(signal)).map((row) => [row.process_id, row.started]),
          );
          remaining = remaining.filter((row) => current.get(row.process_id) === row.started);
        }
        if (!remaining.length) break;
      }
      signal.throwIfAborted();
      await this.write(remaining);
      if (remaining.length) throw new Error('Recorded Mac agent processes remain alive.');
    }, cancellation);
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.cancellation.abort();
    return observe_shutdown(
      this.settled(),
      options,
      'Mac agent process tracking remains unfinished.',
    );
  }
  async settled(): Promise<void> {
    const results = await Promise.allSettled([...this.operations]);
    for (const result of results)
      if (
        result.status === 'rejected' &&
        !(this.closed && is_shutdown_business_cancellation(result.reason))
      )
        throw result.reason;
  }
}
