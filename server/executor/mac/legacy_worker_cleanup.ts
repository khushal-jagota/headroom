/** One authorized pass retires the old aggregate record before modern custody begins. */
import { join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { is_record } from '../../core/records.ts';
import { STOP_SECONDS, TERM_SECONDS } from '../../worker_supervision/config.ts';
import { failure_message } from '../../worker_supervision/inspection.ts';
import type { LegacyCleanupDependencies } from './cleanup_contracts.ts';
import { failure_detail } from './failure_detail.ts';
import type { AgentProcessIdentity } from './platform.ts';
import { atomic_record, read_record } from './runtime/records.ts';

export const LEGACY_CLEANUP_RECEIPT = 'legacy-worker-cleanup.json';
export function legacy_targets(raw: unknown): AgentProcessIdentity[] {
  if (!Array.isArray(raw)) throw new Error('Legacy process evidence is unreadable');
  return raw.map((value: unknown) => {
    if (
      !is_record(value) ||
      !Number.isSafeInteger(value.process_id) ||
      (value.process_id as number) <= 0 ||
      typeof value.started !== 'string'
    )
      throw new Error('Legacy process identity is unreadable');
    return { process_id: value.process_id as number, started: value.started };
  });
}
export function migrate_once(
  run: string,
  dependencies: LegacyCleanupDependencies,
  signal?: AbortSignal,
): Promise<void> {
  return dependencies.runtime.operation(async () => {
    signal?.throwIfAborted();
    const { runtime, files, platform, inspect_legacy, warn } = dependencies;
    const legacy = join(run, 'agent-processes.json');
    const receipt = join(run, LEGACY_CLEANUP_RECEIPT);
    let previous: Record<string, unknown> | null;
    try {
      previous = await read_record(files, receipt);
    } catch (error) {
      warn(`Legacy cleanup receipt is unreadable: ${failure_detail(error)}`);
      return;
    }
    if (previous !== null) {
      if (previous.version === 1 && previous.attempted === true)
        await files.remove_runtime_record(legacy);
      else warn('Legacy cleanup receipt is unreadable');
      return;
    }
    const attempt: {
      version: number;
      attempted: boolean;
      targets: AgentProcessIdentity[];
      error: string | null;
    } = {
      version: 1,
      attempted: true,
      targets: [],
      error: null,
    };
    try {
      const record = await read_record(files, legacy);
      attempt.targets = legacy_targets(record === null ? [] : record.processes);
    } catch (error) {
      attempt.error = failure_message(error);
      warn(`Legacy cleanup compatibility pass: ${attempt.error}`);
    }
    // This durable attempt precedes inventory and every possible signal.
    await atomic_record(files, receipt, attempt);
    const deadline = runtime.clock() + STOP_SECONDS;
    const timeout = AbortSignal.timeout(
      Math.max(1, Math.ceil((deadline - runtime.clock()) * 1000)),
    );
    const cancellation = signal ? AbortSignal.any([signal, timeout]) : timeout;
    try {
      if (attempt.error === null) {
        let selected: AgentProcessIdentity[] = [];
        if (attempt.targets.length) {
          cancellation.throwIfAborted();
          const table = await platform.snapshot(cancellation);
          const found = new Map(
            attempt.targets
              .filter((target) =>
                table.some(
                  (row) => row.process_id === target.process_id && row.started === target.started,
                ),
              )
              .map((target) => [target.process_id, target]),
          );
          let changed = true;
          while (changed) {
            changed = false;
            for (const row of table)
              if (!found.has(row.process_id) && found.has(row.parent_process_id)) {
                found.set(row.process_id, { process_id: row.process_id, started: row.started });
                changed = true;
              }
          }
          selected = [...found.values()];
        }
        const known = new Map(attempt.targets.map((target) => [target.process_id, target]));
        for (const target of selected) known.set(target.process_id, target);
        attempt.targets = [...known.values()];
        await atomic_record(files, receipt, attempt);
        for (const signum of ['SIGTERM', 'SIGKILL'] as const) {
          for (const target of selected) {
            cancellation.throwIfAborted();
            if (runtime.clock() >= deadline)
              throw new DOMException('Legacy cleanup deadline expired', 'TimeoutError');
            if (await inspect_legacy(target, deadline)) {
              try {
                await platform.signal(target, signum, cancellation);
              } catch (error) {
                if ((error as NodeJS.ErrnoException).code !== 'EPERM') throw error;
              }
            }
          }
          if (signum === 'SIGTERM') {
            const grace = Math.min(deadline, runtime.clock() + TERM_SECONDS);
            while (runtime.clock() < grace) {
              cancellation.throwIfAborted();
              let remains = false;
              for (const target of selected)
                if (await inspect_legacy(target, deadline)) {
                  remains = true;
                  break;
                }
              if (!remains) break;
              await pause(Math.min(50, Math.max(0, grace - runtime.clock()) * 1000), undefined, {
                signal: cancellation,
              });
            }
          }
        }
      }
    } catch (error) {
      attempt.error = failure_message(error);
      warn(`Legacy cleanup compatibility pass: ${attempt.error}`);
      await atomic_record(files, receipt, attempt);
    } finally {
      await files.remove_runtime_record(legacy);
    }
  });
}
