import { stringify_python_json } from '../core/http/numeric_json.ts';
/** Journal inspection and bounded cleanup share the same locked source evidence. */
import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import type { LocalChildIdentity } from '../conversation/local_child_contracts.ts';
import type {
  CleanupResult,
  CleanupUncertainty,
  InspectionContext,
  ProcessObservation,
  ReconciliationRecord,
  ReconciliationResult,
  WorkerProcessRecord,
} from './contracts.ts';
import { INSPECTION_SECONDS, STOP_SECONDS, TERM_SECONDS } from './config.ts';
import { discover, validate_pending_shape } from './logic/ownership.ts';
import { capture_observation } from './observation.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';
import * as records from './records.ts';

export const failure_message = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);
export const CLEANUP_SCOPE =
  'Linux cleanup requires empty delegated cgroup custody for all inherited descendants. Legacy or unsupported Linux custody retains a cleanup fence. Processes launched through external brokers are outside descendant custody. Mac cleanup covers recorded process incarnations and observed session members only, with an immediate matching microsecond birth check and an accepted check-to-signal race.';
export function cleanup_result(complete: boolean, reason: string): CleanupResult {
  return { complete, reason, scope: CLEANUP_SCOPE };
}
export interface InspectionOptions {
  readonly runtime: WorkerSupervisionRuntime;
  readonly identity?: LocalChildIdentity;
  readonly deadline?: number;
  readonly context?: InspectionContext;
  readonly revoke_pending?: boolean;
}
export async function observe_unlocked(
  path: string,
  options: InspectionOptions & {
    deadline: number;
    observation?: ProcessObservation;
    fresh?: boolean;
  },
): Promise<WorkerProcessRecord> {
  const { runtime, deadline } = options;
  const record = await records.read(path);
  const previous = stringify_python_json(record, { sort_keys: true, ensure_ascii: true });
  validate_pending_shape(record);
  const context = options.context ?? (await runtime.context(deadline));
  if (context.host.host !== record.host)
    throw new Error('Worker process record belongs to another machine');
  if (context.host.boot !== record.boot)
    Object.assign(record, {
      state: 'complete',
      boot_ended: true,
      reason: 'The recorded boot ended',
    });
  else if (record.state !== 'complete') {
    const observation =
      options.observation ??
      (options.fresh
        ? await runtime.fresh.capture(context, deadline)
        : await capture_observation(context, deadline));
    const found = discover(record, observation);
    const known = new Set(found.map((process) => `${process.pid}:${process.birth}`));
    const root = record.root;
    let members: typeof found = [];
    if (root != null && record.isolated && root.session === root.pid) {
      const leader = observation.stable.get(root.pid);
      if (!leader || leader.birth === root.birth) {
        if (context.primitives.owns_isolated_session_members)
          for (const process of observation.sessions.get(root.session) ?? []) {
            if (!process.zombie && !known.has(`${process.pid}:${process.birth}`)) {
              found.push(process);
              known.add(`${process.pid}:${process.birth}`);
            }
          }
        members = (observation.latest_sessions.get(root.session) ?? []).filter(
          (process) => !process.zombie && !known.has(`${process.pid}:${process.birth}`),
        );
      }
    }
    record.processes = found;
    record.unattributed_session_members = members;
    delete record.observation_error;
  }
  if (stringify_python_json(record, { sort_keys: true, ensure_ascii: true }) !== previous)
    await records.write(path, record);
  return record;
}
function identity_matches(
  record: WorkerProcessRecord,
  identity: LocalChildIdentity | undefined,
): boolean {
  return (
    identity === undefined ||
    (record.root != null &&
      record.root.pid === identity.pid &&
      record.root.birth === identity.birth &&
      record.boot === identity.boot)
  );
}
async function revoke(path: string, record: WorkerProcessRecord): Promise<CleanupResult> {
  validate_pending_shape(record);
  if (record.owner == null || !record.host || !record.boot)
    return cleanup_result(false, 'Pending Worker launch ownership is unknown');
  Object.assign(record, {
    state: 'complete',
    launch_revoked: true,
    reason: 'Launch authorization revoked before backend execution',
  });
  await records.write(path, record);
  return cleanup_result(true, record.reason!);
}
export async function inspect_unlocked(
  path: string,
  options: InspectionOptions & { deadline: number; context: InspectionContext; fresh?: boolean },
): Promise<CleanupResult> {
  const { runtime, deadline, context } = options;
  let record = await observe_unlocked(path, options);
  if (!identity_matches(record, options.identity))
    return cleanup_result(false, 'Worker custody does not match its process record');
  if (record.state === 'complete')
    return cleanup_result(true, record.reason ?? 'Recorded processes ended');
  if (record.root == null) {
    if (
      !options.revoke_pending &&
      !record.launch_failed &&
      record.owner != null &&
      !(await context.primitives.is_absent(record.owner, deadline))
    )
      return cleanup_result(false, 'Worker launch is still pending with its live owner');
    return revoke(path, record);
  }
  for (const process of record.processes)
    if (!(await context.primitives.is_absent(process, deadline)))
      return cleanup_result(false, 'Recorded Worker processes remain');
  // Root absence alone cannot certify a late tool. Inspect fresh relationships again.
  record = await observe_unlocked(path, options);
  for (const process of record.processes)
    if (!(await context.primitives.is_absent(process, deadline)))
      return cleanup_result(false, 'Recorded Worker processes remain');
  if (record.unattributed_session_members?.length)
    return cleanup_result(false, 'Unattributed isolated-session processes remain');
  if (context.primitives.requires_containment) {
    if (!record.cgroup_joined)
      return cleanup_result(
        false,
        record.cgroup_error ?? 'Legacy Worker record lacks complete cgroup custody',
      );
    if (!runtime.cgroups || !record.cgroup)
      return cleanup_result(false, 'Worker cgroup custody inspector is unavailable');
    if (!(await runtime.cgroups.empty(record.cgroup)))
      return cleanup_result(false, 'Worker cgroup processes remain');
  }
  Object.assign(record, { state: 'complete', reason: 'All owned Worker processes ended' });
  await records.write(path, record);
  if (record.cgroup_joined && record.cgroup && runtime.cgroups) {
    // The committed empty receipt already proves process retirement.
    await runtime.cgroups.remove(record.cgroup).catch((error: NodeJS.ErrnoException) => {
      if (typeof error.code !== 'string') throw error;
    });
  }
  return cleanup_result(true, record.reason!);
}
export function inspect_record(path: string, options: InspectionOptions): Promise<CleanupResult> {
  return options.runtime.operation(async () => {
    const deadline = options.deadline ?? options.runtime.clock() + INSPECTION_SECONDS;
    try {
      return await records.locked(
        path,
        deadline,
        async () =>
          inspect_unlocked(path, {
            ...options,
            deadline,
            context: options.context ?? (await options.runtime.context(deadline)),
          }),
        { clock: options.runtime.clock },
      );
    } catch (error) {
      return cleanup_result(
        false,
        `Worker cleanup evidence unavailable: ${failure_message(error)}`,
      );
    }
  });
}
export function stop_record(path: string, options: InspectionOptions): Promise<CleanupResult> {
  return options.runtime.operation(async () => {
    const { runtime } = options;
    const deadline = options.deadline ?? runtime.clock() + STOP_SECONDS;
    try {
      return await records.locked(
        path,
        deadline,
        async () => {
          const context = options.context ?? (await runtime.context(deadline));
          const settings = { ...options, deadline, context, fresh: true };
          let record = await observe_unlocked(path, settings);
          if (!identity_matches(record, options.identity))
            return cleanup_result(false, 'Worker custody does not match its process record');
          if (record.state === 'complete')
            return cleanup_result(true, record.reason ?? 'Recorded processes ended');
          if (record.root == null) return revoke(path, record);
          const term_deadline = Math.min(deadline, runtime.clock() + TERM_SECONDS);
          for (const [signal, phase_deadline] of [
            [15, term_deadline],
            [9, deadline],
          ] as const) {
            record = await observe_unlocked(path, settings);
            if (signal === 9 && record.cgroup) {
              if (!runtime.cgroups)
                throw new Error('Worker cgroup custody inspector is unavailable');
              await runtime.cgroups.kill(record.cgroup);
            }
            for (const process of record.processes)
              if (!(await context.primitives.is_absent(process, deadline)))
                await context.primitives.signal(process, signal, deadline);
            while (runtime.clock() < phase_deadline) {
              try {
                const result = await inspect_unlocked(path, settings);
                if (result.complete) return result;
              } catch {
                // A transient evidence failure is not a cleanup receipt. Keep
                // polling, including escalation after the TERM phase expires.
              }
              await pause(Math.min(50, Math.max(0, phase_deadline - runtime.clock()) * 1000));
            }
          }
          record = await records.read(path);
          let reason = 'Worker stop deadline expired';
          if (context.primitives.requires_containment && !record.cgroup_joined)
            reason +=
              '; ' + (record.cgroup_error ?? 'Legacy Worker record lacks complete cgroup custody');
          if (record.unattributed_session_members?.length)
            reason += '; unattributed isolated-session processes remain';
          Object.assign(record, { state: 'not_proven', reason });
          await records.write(path, record);
          return cleanup_result(false, reason);
        },
        { clock: runtime.clock },
      );
    } catch (error) {
      return cleanup_result(false, `Worker cleanup not proven: ${failure_message(error)}`);
    }
  });
}
async function paths(root: string): Promise<string[]> {
  try {
    return (await readdir(root))
      .filter((name) => name.endsWith('.json'))
      .sort()
      .map((name) => join(root, name));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
}
export function cleanup_uncertainties(
  root: string,
  options: {
    runtime: WorkerSupervisionRuntime;
    revoke_pending?: boolean;
    ticket_id?: string;
    healthy_sources?: ReadonlySet<string>;
  },
): Promise<readonly CleanupUncertainty[]> {
  return options.runtime.operation(async () => {
    const uncertain: CleanupUncertainty[] = [];
    let context: InspectionContext | undefined, context_error: string | undefined;
    for (const path of await paths(root)) {
      let record: WorkerProcessRecord;
      try {
        record = await records.read(path);
      } catch (error) {
        uncertain.push({
          ticket_id: null,
          conversation_id: null,
          record_path: path,
          reason: failure_message(error),
        });
        continue;
      }
      if (options.ticket_id !== undefined && record.ticket_id !== options.ticket_id) continue;
      if (
        record.source_incarnation &&
        record.source_child_id &&
        options.healthy_sources?.has(
          JSON.stringify([record.source_incarnation, record.source_child_id]),
        )
      )
        continue;
      let result: CleanupResult;
      try {
        validate_pending_shape(record);
        if (record.state === 'complete') continue;
        if (!context && !context_error)
          try {
            context = await options.runtime.context(options.runtime.clock() + INSPECTION_SECONDS);
          } catch (error) {
            context_error = `Worker host evidence unavailable: ${failure_message(error)}`;
          }
        result = context_error
          ? cleanup_result(false, context_error)
          : await inspect_record(path, { ...options, context });
      } catch (error) {
        result = cleanup_result(false, failure_message(error));
      }
      if (!result.complete)
        uncertain.push({
          ticket_id: record.ticket_id,
          conversation_id: record.conversation_id ?? null,
          record_path: path,
          reason: result.reason,
        });
    }
    return uncertain;
  });
}
export function reconcile(
  root: string,
  options: { runtime: WorkerSupervisionRuntime; ticket_id?: string },
): Promise<ReconciliationResult> {
  return options.runtime.operation(async () => {
    const results: ReconciliationRecord[] = [];
    let context: InspectionContext | undefined, context_error: string | undefined;
    for (const path of await paths(root)) {
      let record: WorkerProcessRecord;
      try {
        record = await records.read(path);
      } catch (error) {
        results.push({
          ticket_id: null,
          conversation_id: null,
          record_path: path,
          result: cleanup_result(false, `Worker record is unreadable: ${failure_message(error)}`),
        });
        continue;
      }
      if (options.ticket_id !== undefined && record.ticket_id !== options.ticket_id) continue;
      let result: CleanupResult;
      try {
        validate_pending_shape(record);
        if (record.state === 'complete')
          result = cleanup_result(true, record.reason ?? 'Recorded cleanup complete');
        else {
          const deadline = options.runtime.clock() + STOP_SECONDS;
          if (!context && !context_error)
            try {
              context = await options.runtime.context(deadline);
            } catch (error) {
              context_error = `Worker host evidence unavailable: ${failure_message(error)}`;
            }
          if (context_error) result = cleanup_result(false, context_error);
          else if (context!.host.host !== record.host)
            result = cleanup_result(false, 'Prior Worker custody belongs to another machine');
          else if (
            context!.host.boot === record.boot &&
            record.owner != null &&
            !(await context!.primitives.is_absent(record.owner, deadline))
          )
            result = await inspect_record(path, { runtime: options.runtime, deadline, context });
          else result = await stop_record(path, { runtime: options.runtime, deadline, context });
        }
      } catch (error) {
        result = cleanup_result(
          false,
          `Worker cleanup evidence unavailable: ${failure_message(error)}`,
        );
      }
      results.push({
        record_path: path,
        ticket_id: record.ticket_id,
        conversation_id: record.conversation_id ?? null,
        result,
      });
    }
    const remaining = results.filter((item) => !item.result.complete).length;
    return {
      ...cleanup_result(
        remaining === 0,
        results.length === 0
          ? 'No recorded Worker launches'
          : remaining
            ? `${remaining} Worker launch records remain unproven`
            : 'All recorded Worker launches reconciled',
      ),
      records: results,
    };
  });
}
