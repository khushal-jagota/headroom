import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { read_stall_presence } from '../executor/vps_presence_store.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { STALL_CHECK_INTERVAL_SECONDS } from './configuration.ts';
import { finding_key, TRACKED_WAIT_KINDS } from './contracts.ts';
import type { StallFinding, StallSource, WaitRegistry, TrackedWait } from './contracts.ts';
import {
  forget_present_time_before,
  observation_from_open_findings,
  overdue_waits,
  presence_waits,
} from './logic/findings.ts';
import type { OverdueWait, PresenceObservation } from './logic/findings.ts';

export function read_open_findings(sql: ReadSql): readonly StallFinding[] {
  return sql.all<StallFinding>(
    `
    SELECT finding_key,kind,conversation_id,ticket_id,ticket_title,detail,
      stalled_since_ms,last_seen_at_ms,cleared_at_ms
    FROM stall_findings
    WHERE cleared_at_ms IS NULL AND kind IN (SELECT value FROM json_each(?))
    ORDER BY stalled_since_ms,finding_key
  `,
    [JSON.stringify(TRACKED_WAIT_KINDS)],
  );
}
export class StallChecker {
  private readonly database: Database;
  private readonly registry: WaitRegistry;
  private readonly sources: () => readonly StallSource[];
  private readonly clock: () => number;
  private observations = new Map<string, PresenceObservation>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  constructor(
    database: Database,
    registry: WaitRegistry,
    sources: () => readonly StallSource[],
    clock: () => number,
  ) {
    this.database = database;
    this.registry = registry;
    this.sources = sources;
    this.clock = clock;
  }
  start(): void {
    if (this.running) throw new Error('stall checker already started');
    this.running = true;
    const run = () => {
      if (!this.running) return;
      try {
        this.check_once();
      } catch (error) {
        console.error('stall check failed', error);
      }
      if (this.running) this.timer = setTimeout(run, STALL_CHECK_INTERVAL_SECONDS * 1000);
    };
    run();
  }
  async close(): Promise<void> {
    this.running = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
  check_once(): readonly StallFinding[] {
    const now_ms = this.clock();
    const registry_waits = this.registry.snapshot();
    const sources = this.sources();
    const { records, waits, open_findings, destinations } = this.database.read((sql) => {
      const records = new Map(
        sql
          .all<{ machine_id: string }>('SELECT machine_id FROM mac_executor_presence')
          .map((row) => [row.machine_id, read_stall_presence(sql, row.machine_id)]),
      );
      if (!records.has(ORIGINAL_MAC_MACHINE_ID)) records.set(ORIGINAL_MAC_MACHINE_ID, null);
      const waits = [
        ...registry_waits,
        ...sources.flatMap((source) => [...source.tracked_waits(sql, now_ms)]),
      ];
      const open_findings = read_open_findings(sql);
      const destinations = new Map<string, string>();
      const ids = new Set(
        [...waits, ...open_findings]
          .map((item) => item.conversation_id)
          .filter((id): id is string => !!id),
      );
      for (const id of ids) {
        const row =
          sql.get<{ machine_id: string }>(
            'SELECT machine_id FROM tickets WHERE conversation_id=?',
            [id],
          ) ??
          sql.get<{ machine_id: string }>(
            'SELECT i.machine_id FROM executor_child_assignments a JOIN executor_incarnations i USING(executor_incarnation) WHERE a.conversation_id=? LIMIT 1',
            [id],
          );
        if (row !== undefined) destinations.set(id, row.machine_id);
      }
      return { records, waits, open_findings, destinations };
    });
    const groups = new Map<string, TrackedWait[]>([...records.keys()].map((id) => [id, []]));
    for (const wait of waits) {
      const machine =
        wait.machine_id || destinations.get(wait.conversation_id ?? '') || ORIGINAL_MAC_MACHINE_ID;
      if (!groups.has(machine)) groups.set(machine, []);
      groups.get(machine)!.push(wait);
    }
    const rebuilt = new Map<string, StallFinding[]>();
    for (let finding of open_findings) {
      let machine = destinations.get(finding.conversation_id ?? '') ?? ORIGINAL_MAC_MACHINE_ID;
      const marker = finding.finding_key.indexOf(':machine=');
      if (marker !== -1) {
        const rest = finding.finding_key.slice(marker + ':machine='.length);
        const separator = rest.indexOf(':');
        if (separator === -1) throw new Error('Invalid machine-specific stall finding key');
        machine = rest.slice(0, separator);
        finding = {
          ...finding,
          finding_key: `${finding.finding_key.slice(0, marker)}:${rest.slice(separator + 1)}`,
        };
      }
      if (!rebuilt.has(machine)) rebuilt.set(machine, []);
      rebuilt.get(machine)!.push(finding);
    }
    const overdue: OverdueWait[] = [];
    const observations = new Map<string, PresenceObservation>();
    for (const [machine, machine_waits] of groups) {
      const previous =
        this.observations.get(machine) ??
        observation_from_open_findings(rebuilt.get(machine) ?? []);
      const record = records.get(machine) ?? null;
      const [lease_waits, observation] = presence_waits(record, now_ms, previous);
      const all_waits = [...machine_waits, ...lease_waits];
      const found = overdue_waits(all_waits, record, now_ms, observation);
      observations.set(machine, {
        ...forget_present_time_before(observation, all_waits),
        open_findings: new Map(
          found.map((item) => [finding_key(item.wait), item.stalled_since_ms]),
        ),
      });
      for (const item of found)
        overdue.push(
          machine !== ORIGINAL_MAC_MACHINE_ID && item.wait.conversation_id === null
            ? {
                ...item,
                wait: { ...item.wait, wait_key: `machine=${machine}:${item.wait.wait_key}` },
              }
            : item,
        );
    }
    const findings = this.database.write((tx) => write_findings(tx, overdue, now_ms), {
      changeSignal: 'silent',
    });
    this.observations = observations;
    return findings;
  }
}
function write_findings(
  sql: TransactionSql,
  overdue: readonly OverdueWait[],
  now_ms: number,
): readonly StallFinding[] {
  const conversation_ids = [
    ...new Set(
      overdue
        .map((item) => item.wait.conversation_id)
        .filter((id): id is string => id !== null && id !== ''),
    ),
  ].sort();
  const tickets = new Map(
    sql
      .all<{ conversation_id: string; id: string; title: string }>(
        `
    SELECT conversation_id,id,title FROM tickets
    WHERE conversation_id IN (SELECT value FROM json_each(?))
  `,
        [JSON.stringify(conversation_ids)],
      )
      .map((row) => [row.conversation_id, row]),
  );
  const findings: StallFinding[] = overdue.map((item) => {
    const ticket = tickets.get(item.wait.conversation_id ?? '');
    return {
      finding_key: finding_key(item.wait),
      kind: item.wait.kind,
      conversation_id: item.wait.conversation_id,
      ticket_id: ticket?.id ?? null,
      ticket_title: ticket?.title ?? null,
      detail: item.wait.detail,
      stalled_since_ms: item.stalled_since_ms,
      last_seen_at_ms: now_ms,
      cleared_at_ms: null,
    };
  });
  for (const finding of findings)
    sql.run(
      `
    INSERT INTO stall_findings(finding_key,kind,conversation_id,ticket_id,ticket_title,
      detail,stalled_since_ms,last_seen_at_ms,cleared_at_ms)
    VALUES (?,?,?,?,?,?,?,?,NULL)
    ON CONFLICT(finding_key) DO UPDATE SET kind=excluded.kind,
      conversation_id=excluded.conversation_id,ticket_id=excluded.ticket_id,
      ticket_title=excluded.ticket_title,detail=excluded.detail,
      stalled_since_ms=excluded.stalled_since_ms,last_seen_at_ms=excluded.last_seen_at_ms,
      cleared_at_ms=NULL
  `,
      [
        finding.finding_key,
        finding.kind,
        finding.conversation_id,
        finding.ticket_id,
        finding.ticket_title,
        finding.detail,
        finding.stalled_since_ms,
        finding.last_seen_at_ms,
      ],
    );
  sql.run(
    `
    UPDATE stall_findings SET cleared_at_ms=? WHERE cleared_at_ms IS NULL
      AND finding_key NOT IN (SELECT value FROM json_each(?))
  `,
    [now_ms, JSON.stringify(findings.map((finding) => finding.finding_key))],
  );
  sql.run(
    `
    INSERT INTO stall_checker_heartbeat(singleton,checked_at_ms) VALUES (1,?)
    ON CONFLICT(singleton) DO UPDATE SET checked_at_ms=excluded.checked_at_ms
  `,
    [now_ms],
  );
  return findings;
}
