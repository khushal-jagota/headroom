import { numeric_kind, numeric_value } from '../../core/http/numeric_json.ts';
/** Pure identity validation and discovery from a paired process observation. */
import type { WorkerProcess, WorkerProcessRecord, ProcessObservation } from '../contracts.ts';

export function process_from_json(value: unknown): WorkerProcess {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid recorded process');
  const raw = value as Record<string, unknown>;
  for (const key of ['pid', 'parent', 'group', 'session'])
    if (numeric_kind(raw[key]) !== 'integer' || !Number.isSafeInteger(numeric_value(raw[key])))
      throw new Error('Invalid recorded process identity');
  if (
    numeric_value(raw.pid)! <= 0 ||
    typeof raw.birth !== 'string' ||
    !raw.birth ||
    (raw.zombie !== undefined && typeof raw.zombie !== 'boolean')
  )
    throw new Error('Invalid recorded process identity');
  return {
    pid: numeric_value(raw.pid)!,
    birth: raw.birth,
    parent: numeric_value(raw.parent)!,
    group: numeric_value(raw.group)!,
    session: numeric_value(raw.session)!,
    zombie: (raw.zombie as boolean | undefined) ?? false,
  };
}
export function same_process(first: WorkerProcess, second: WorkerProcess | undefined): boolean {
  return (
    second !== undefined &&
    first.pid === second.pid &&
    first.birth === second.birth &&
    first.parent === second.parent &&
    first.group === second.group &&
    first.session === second.session &&
    first.zombie === second.zombie
  );
}
export function alive(process: WorkerProcess, table: ReadonlyMap<number, WorkerProcess>): boolean {
  const current = table.get(process.pid);
  return current !== undefined && current.birth === process.birth && !current.zombie;
}
export function validate_pending_shape(record: WorkerProcessRecord): void {
  if (record.launchd !== undefined) {
    if (
      !/^gui\/[0-9]+$/.test(record.launchd.domain) ||
      !/^com\.panels\.worker\.[0-9a-f]+\.[0-9a-f]{32}$/.test(record.launchd.label) ||
      !record.host ||
      !record.boot ||
      record.processes.length ||
      record.root != null ||
      record.owner != null
    )
      throw new Error('Invalid Worker launchd custody');
    return;
  }
  if (record.root != null) return;
  if (record.processes.length)
    throw new Error('Worker record has process custody without an exact root');
  if (!record.host || !record.boot || record.owner == null)
    throw new Error('Pending Worker launch ownership is unknown');
  process_from_json(record.owner);
  if (record.state === 'complete') {
    if (!record.launch_revoked && !record.boot_ended)
      throw new Error('Rootless completion lacks a pre-execution revocation receipt');
    return;
  }
  if (
    record.state === 'pending' ||
    ((record.state === 'not_proven' || record.state === 'bootstrap_failed') &&
      record.launch_phase === 'pending')
  )
    return;
  throw new Error('Worker record lacks valid pre-execution launch evidence');
}
export function discover(
  record: WorkerProcessRecord,
  observation: ProcessObservation,
): WorkerProcess[] {
  const known = new Map(
    record.processes.map((process) => [`${process.pid}:${process.birth}`, process]),
  );
  const roots = new Set(
    record.processes
      .filter((process) => alive(process, observation.stable))
      .map((process) => process.pid),
  );
  const pending = [...roots];
  const visited = new Set<number>();
  while (pending.length) {
    const pid = pending.pop()!;
    const candidates = [...(observation.children.get(pid) ?? [])];
    const session = observation.stable.get(pid)!.session;
    if (record.isolated && !visited.has(session)) {
      visited.add(session);
      candidates.push(...(observation.sessions.get(session) ?? []));
    }
    for (const child of candidates)
      if (!roots.has(child.pid)) {
        roots.add(child.pid);
        known.set(`${child.pid}:${child.birth}`, child);
        pending.push(child.pid);
      }
  }
  return [...known.values()].sort(
    (a, b) => a.pid - b.pid || (a.birth < b.birth ? -1 : a.birth > b.birth ? 1 : 0),
  );
}
