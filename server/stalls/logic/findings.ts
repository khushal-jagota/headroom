import * as configuration from '../configuration.ts';
import { finding_key, PAUSED_WHILE_MAC_AWAY } from '../contracts.ts';
import type { StallFinding, TrackedWait } from '../contracts.ts';
import type { StallPresenceRecord } from '../../executor/contracts.ts';

export const MAC_LEASE_FAILING_WAIT_KEY = 'mac';
const MAC_LEASE_FAILING_FINDING_KEY = 'mac_lease_failing:mac';
export interface ItemProgress {
  oldest_sequence: number;
  first_seen_at_ms: number;
}
export interface PresenceObservation {
  items: ReadonlyMap<string, ItemProgress>;
  contact_since_ms: number | null;
  present_stretches: readonly (readonly [number, number])[];
  last_run_lease_ms: number | null;
  open_findings: ReadonlyMap<string, number>;
}
export interface OverdueWait {
  wait: TrackedWait;
  stalled_since_ms: number;
}
export function empty_observation(): PresenceObservation {
  return {
    items: new Map(),
    contact_since_ms: null,
    present_stretches: [],
    last_run_lease_ms: null,
    open_findings: new Map(),
  };
}
export function items_wait_key(conversation_id: string, oldest_sequence: number): string {
  return `${conversation_id}:${oldest_sequence}`;
}
export function observation_from_open_findings(
  findings: Iterable<StallFinding>,
): PresenceObservation {
  const items = new Map<string, ItemProgress>();
  const open_findings = new Map<string, number>();
  let contact_since_ms: number | null = null;
  for (const finding of findings) {
    if (finding.cleared_at_ms !== null) continue;
    open_findings.set(finding.finding_key, finding.stalled_since_ms);
    if (finding.kind === 'mac_items_not_moving' && finding.conversation_id) {
      const sequence = finding.finding_key.split(':').at(-1)!;
      if (/^\d+$/.test(sequence))
        items.set(finding.conversation_id, {
          oldest_sequence: Number(sequence),
          first_seen_at_ms: finding.stalled_since_ms,
        });
    } else if (finding.kind === 'mac_lease_failing') contact_since_ms = finding.stalled_since_ms;
  }
  return { items, contact_since_ms, present_stretches: [], last_run_lease_ms: null, open_findings };
}
export function presence_waits(
  record: StallPresenceRecord | null,
  now_ms: number,
  previous: PresenceObservation,
): [readonly TrackedWait[], PresenceObservation] {
  const items = observe_items(record, previous.items);
  const waits: TrackedWait[] = [...items.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([conversation_id, progress]) => ({
      kind: 'mac_items_not_moving',
      wait_key: items_wait_key(conversation_id, progress.oldest_sequence),
      conversation_id,
      detail: `The Mac has not delivered item ${progress.oldest_sequence}`,
      started_at_ms: progress.first_seen_at_ms,
    }));
  const contact_since_ms = contact_since(record, now_ms, previous);
  const stretches = observe_present_time(record, previous.present_stretches);
  let last_run_lease_ms = previous.last_run_lease_ms;
  const last = stretches.at(-1);
  if (last && last[1] > last[0]) last_run_lease_ms = last[1];
  if (
    record &&
    contact_since_ms !== null &&
    (contact_unanswered(record) || previous.open_findings.has(MAC_LEASE_FAILING_FINDING_KEY))
  ) {
    let detail = 'The Mac keeps making contact, but its leases are not accepted';
    if (record.refusal) detail += ` (${record.refusal.reason})`;
    waits.push({
      kind: 'mac_lease_failing',
      wait_key: MAC_LEASE_FAILING_WAIT_KEY,
      conversation_id: null,
      detail,
      started_at_ms:
        last_run_lease_ms === null
          ? contact_since_ms
          : Math.max(last_run_lease_ms, contact_since_ms),
    });
  }
  return [
    waits,
    { ...previous, items, contact_since_ms, present_stretches: stretches, last_run_lease_ms },
  ];
}
function contact_unanswered(record: StallPresenceRecord): boolean {
  if (record.last_contact_at_ms === null) return false;
  if (record.last_lease_at_ms === null) return true;
  return (
    record.last_contact_at_ms - record.last_lease_at_ms >
    configuration.MAC_LEASE_FAILING_UNANSWERED_CONTACT_SECONDS * 1000
  );
}
function observe_items(
  record: StallPresenceRecord | null,
  previous: ReadonlyMap<string, ItemProgress>,
): ReadonlyMap<string, ItemProgress> {
  if (!record || !record.lease || record.last_lease_at_ms === null) return new Map(previous);
  const observed = new Map<string, ItemProgress>();
  for (const waiting of record.lease.waiting_conversations) {
    const kept = previous.get(waiting.conversation_id);
    observed.set(
      waiting.conversation_id,
      kept && kept.oldest_sequence === waiting.oldest_sequence
        ? kept
        : { oldest_sequence: waiting.oldest_sequence, first_seen_at_ms: record.last_lease_at_ms },
    );
  }
  return observed;
}
function contact_since(
  record: StallPresenceRecord | null,
  now_ms: number,
  previous: PresenceObservation,
): number | null {
  if (!record || record.last_contact_at_ms === null) return null;
  if (
    now_ms - record.last_contact_at_ms <=
    configuration.MAC_LEASE_FAILING_RECENT_CONTACT_SECONDS * 1000
  )
    return previous.contact_since_ms ?? record.last_contact_at_ms;
  if (previous.open_findings.has(MAC_LEASE_FAILING_FINDING_KEY)) return previous.contact_since_ms;
  return null;
}
function observe_present_time(
  record: StallPresenceRecord | null,
  previous: readonly (readonly [number, number])[],
): readonly (readonly [number, number])[] {
  if (!record || record.last_lease_at_ms === null) return previous;
  const lease = record.last_lease_at_ms;
  const run_start = record.continuous_since_ms ?? lease;
  let kept = previous;
  const last_stretch = previous.at(-1);
  if (last_stretch) {
    const [first, last] = last_stretch;
    if (lease <= last) return previous;
    if (run_start - last <= configuration.MAC_PRESENT_GAP_SECONDS * 1000)
      return [...previous.slice(0, -1), [first, lease]];
    if (last - first < configuration.MAC_PRESENT_STRETCH_MIN_SECONDS * 1000)
      kept = previous.slice(0, -1);
  }
  return [...kept, [Math.min(run_start, lease), lease]];
}
export function present_ms(
  stretches: Iterable<readonly [number, number]>,
  since_ms: number,
): number {
  let result = 0;
  for (const [first, last] of stretches) {
    if (last - first >= configuration.MAC_PRESENT_STRETCH_MIN_SECONDS * 1000)
      result += Math.max(0, last - Math.max(first, since_ms));
  }
  return result;
}
export function forget_present_time_before(
  observation: PresenceObservation,
  waits: Iterable<TrackedWait>,
): PresenceObservation {
  const stretches = observation.present_stretches;
  const last = stretches.at(-1);
  if (!last) return observation;
  const starts = [...waits]
    .filter((wait) => PAUSED_WHILE_MAC_AWAY.has(wait.kind))
    .map((wait) => wait.started_at_ms);
  const earliest = starts.length ? Math.min(...starts) : last[1];
  return {
    ...observation,
    present_stretches: [...stretches.slice(0, -1).filter((stretch) => stretch[1] > earliest), last],
  };
}
export function overdue_waits(
  waits: Iterable<TrackedWait>,
  record: StallPresenceRecord | null,
  now_ms: number,
  observation: PresenceObservation,
): readonly OverdueWait[] {
  const earliest = new Map<string, TrackedWait>();
  for (const wait of waits) {
    const key = finding_key(wait);
    const kept = earliest.get(key);
    if (!kept || wait.started_at_ms < kept.started_at_ms) earliest.set(key, wait);
  }
  const tool_running = new Set(
    record?.lease?.working_turns
      .filter((turn) => turn.tool_running)
      .map((turn) => turn.child.conversation_id) ?? [],
  );
  const retiring = new Set(
    [...earliest.values()]
      .filter((wait) => wait.kind === 'vps_replacement_retiring')
      .map((wait) => wait.conversation_id),
  );
  const overdue: OverdueWait[] = [];
  for (const wait of earliest.values()) {
    if (
      wait.kind === 'mac_turn_silent' &&
      wait.conversation_id &&
      tool_running.has(wait.conversation_id)
    )
      continue;
    if (wait.kind === 'mac_replacement_pending' && retiring.has(wait.conversation_id)) continue;
    const counted_ms =
      observation.open_findings.get(finding_key(wait)) === wait.started_at_ms
        ? null
        : PAUSED_WHILE_MAC_AWAY.has(wait.kind)
          ? present_ms(observation.present_stretches, wait.started_at_ms)
          : now_ms - wait.started_at_ms;
    if (counted_ms === null || counted_ms >= configuration.stall_limit_seconds(wait.kind) * 1000)
      overdue.push({ wait, stalled_since_ms: wait.started_at_ms });
  }
  return overdue.sort((a, b) =>
    finding_key(a.wait) < finding_key(b.wait)
      ? -1
      : finding_key(a.wait) > finding_key(b.wait)
        ? 1
        : 0,
  );
}
