/** The committed lease cache, incarnation fence, contact flush and memory-only stalls. */
import type { Database, ReadSql } from '../core/database/contracts.ts';
import { isDeepStrictEqual } from 'node:util';
import { is_sqlite_error } from '../core/database/index.ts';
import { PlannerError } from '../core/contracts.ts';
import { is_os_error } from '../core/system_errors.ts';
import { ExecutorValidationError } from '../executor/protocol_errors.ts';
import { UpdateConflict } from '../environments/updates/attempts.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { StallSource, TrackedWait } from '../stalls/contracts.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import {
  MAC_NEEDS_UPDATE,
  ORIGINAL_MAC_MACHINE_ID,
  ProtocolRefused,
  refusal_reason_for_versions,
  supports_executor_versions,
  validate_identity,
} from '../execution_identity.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import type {
  AcceptedLease,
  ExecutorHealthRead,
  ExecutorRefusal,
  IncarnationReplaced,
  MacPresenceOptions,
  PresenceRecord,
  UpdateCheck,
  WireValue,
} from './contracts.ts';
import { now_ms } from './clock.ts';
import {
  CONTACT_FLUSH_INTERVAL_SECONDS,
  LEASE_CONTINUITY_SECONDS,
  MAC_AWAY_AFTER_SECONDS,
  MAC_UPDATE_SCHEMA_VERSION,
} from './configuration.ts';
import { decode_value } from './protocol.ts';
import { high_sequence } from './data/mailbox.ts';
import {
  accept_lease_in_transaction,
  not_current,
  read_presence,
  record_contact,
  record_refusal,
  same_identity,
} from './vps_presence_store.ts';
import {
  judge_presence,
  lease_changes_presence_signal,
  next_presence_change_ms,
} from './logic/presence.ts';
import type { SqliteCommandMailbox } from './vps_mailbox.ts';

export function no_update_check(): WireValue<'MacUpdateCheck'> {
  return { schema_version: MAC_UPDATE_SCHEMA_VERSION, target: null, prefetch: null };
}
function empty_record(): PresenceRecord {
  return {
    executor_incarnation: null,
    identity: null,
    last_contact_at_ms: null,
    last_lease_at_ms: null,
    continuous_since_ms: null,
    going_to_sleep_at_ms: null,
    lease: null,
    refusal: null,
  };
}

export class MacPresenceService {
  readonly machine_id: string;
  update_check: UpdateCheck = no_update_check;
  incarnation_replaced: IncarnationReplaced | null = null;
  returned: (() => void) | null = null;
  private loaded_record: PresenceRecord | null = null;
  private record_loaded = false;
  private flushed_contact_ms: number | null = null;
  private contact_flush: ReturnType<typeof setTimeout> | undefined;
  private presence_timer: ReturnType<typeof setTimeout> | undefined;
  private readonly drain_waiters = new Set<{
    conversation_id: string;
    resolve(): void;
    reject(error: unknown): void;
  }>();
  private drains_owed = false;
  private working_turns = new Map<
    string,
    { conversation_id: string; turn_number: number; first_seen_ms: number; tool_running: boolean }
  >();
  private readonly activity_ms = new Map<string, number>();
  private readonly operations = new Set<Promise<unknown>>();
  private readonly failures = new Set<unknown>();
  private readonly clock: () => number;
  private closed = false;
  private readonly resource_join: Promise<void>;
  private resolve_join!: () => void;
  private reject_join!: (error: unknown) => void;

  private readonly database: Database;
  readonly mailbox: SqliteCommandMailbox;
  private readonly options: MacPresenceOptions;
  constructor(database: Database, mailbox: SqliteCommandMailbox, options: MacPresenceOptions) {
    this.database = database;
    this.mailbox = mailbox;
    this.options = options;
    this.machine_id = options.machine_id ?? ORIGINAL_MAC_MACHINE_ID;
    this.clock = options.clock ?? now_ms;
    this.resource_join = new Promise((resolve, reject) => {
      this.resolve_join = resolve;
      this.reject_join = reject;
    });
    void this.resource_join.catch(() => {});
  }
  private admit(): void {
    if (this.closed) throw new ServerLifecycleError('Executor presence is closed.');
  }
  private get_record(): PresenceRecord | null {
    if (!this.record_loaded && !this.closed) {
      this.loaded_record = this.database.read((sql) => read_presence(sql, this.machine_id));
      this.record_loaded = true;
      this.flushed_contact_ms = this.loaded_record?.last_contact_at_ms ?? null;
    }
    return this.loaded_record;
  }
  private set_record(record: PresenceRecord): void {
    this.loaded_record = record;
    this.record_loaded = true;
  }
  private own<T>(work: () => T | Promise<T>): Promise<T> {
    let resolve!: (value: T | PromiseLike<T>) => void;
    let reject!: (error: unknown) => void;
    const result = new Promise<T>((done, failed) => {
      resolve = done;
      reject = failed;
    });
    this.operations.add(result);
    void result.then(
      () => this.operations.delete(result),
      () => this.operations.delete(result),
    );
    try {
      resolve(work());
    } catch (error) {
      reject(error);
    }
    return result;
  }
  server_identity(): ExecutorIdentity {
    return {
      protocol_version: PROTOCOL_VERSION,
      application_revision: this.options.application_revision ?? 'unknown',
      spool_format_version: SPOOL_FORMAT_VERSION,
      executor_incarnation: '',
      connection_generation: 0,
    };
  }
  accept_lease(
    request: WireValue<'LeaseRequest'>,
  ): Promise<WireValue<'LeaseReply'> | WireValue<'LeaseRefused'>> {
    this.admit();
    return this.own(() => this.lease(request));
  }
  private async lease(
    request: WireValue<'LeaseRequest'>,
  ): Promise<WireValue<'LeaseReply'> | WireValue<'LeaseRefused'>> {
    const identity = request.identity;
    const arrived = this.clock();
    try {
      validate_identity(identity, { check_versions: false });
    } catch (error) {
      if (!(error instanceof ProtocolRefused)) throw error;
      this.contact();
      throw new PlannerError('validation', error.message);
    }
    if (!supports_executor_versions(identity.protocol_version, identity.spool_format_version)) {
      const refusal: ExecutorRefusal = {
        reason: refusal_reason_for_versions(
          identity.protocol_version,
          identity.spool_format_version,
        ),
        peer_identity: { ...identity, connection_generation: identity.connection_generation ?? 0 },
        server_identity: this.server_identity(),
        refused_at_ms: arrived,
      };
      await this.keep_refusal(refusal);
      const error = new PlannerError('executor_version_refused', refusal.reason, {
        protocol_version: PROTOCOL_VERSION,
        spool_format_version: SPOOL_FORMAT_VERSION,
      });
      return {
        error: decode_value(error.toPayload().error, 'JsonObject'),
        refusal,
        update: await this.check_update(),
      };
    }
    let written;
    try {
      written = this.write_lease(request);
    } catch (error) {
      if (!(error instanceof PlannerError) || error.code !== 'executor_incarnation_not_current')
        throw error;
      this.contact();
      return {
        error: decode_value(error.toPayload().error, 'JsonObject'),
        refusal: null,
        update: null,
      };
    }
    // Publication remains owned if stop seals after the commit or during its announcement.
    await Promise.resolve();
    const { accepted, settled, now, announce } = written;
    const returned =
      accepted.new_incarnation || judge_presence(accepted.previous, now).presence !== 'present';
    const kept = this.get_record();
    if (
      kept === null ||
      kept.last_lease_at_ms === null ||
      kept.last_lease_at_ms <= now ||
      accepted.new_incarnation
    ) {
      this.set_record(accepted.current);
      this.flushed_contact_ms = accepted.current.last_contact_at_ms;
      this.observe_lease(accepted.current.lease ?? request, accepted, now);
      this.schedule_presence_change();
    }
    if (announce) this.options.changes.emit();
    await this.mailbox.publish_settlements(settled);
    this.wake_drains();
    if (accepted.replaced_incarnation !== null) this.drains_owed = true;
    if (!this.closed && this.drains_owed && this.incarnation_replaced !== null) {
      try {
        await this.incarnation_replaced(
          accepted.replaced_incarnation ?? '',
          identity.executor_incarnation,
        );
        this.drains_owed = false;
      } catch (error) {
        if (!is_os_error(error) && !is_sqlite_error(error)) throw error;
        console.warn("The replaced Mac process's drains did not start:", error);
      }
    }
    if (!this.closed && returned && this.returned !== null) this.returned();
    return {
      accepted_at_vps_ms: now,
      away_after_seconds: MAC_AWAY_AFTER_SECONDS,
      update: await this.check_update(),
      mailbox_high_sequence: this.database.read((sql) => high_sequence(sql, this.machine_id)),
    };
  }
  private write_lease(request: WireValue<'LeaseRequest'>) {
    return this.database.write(
      (tx) => {
        const now = this.clock();
        request = this.validated_lease(tx, request);
        const accepted = accept_lease_in_transaction(tx, request, now, this.machine_id);
        const settled =
          accepted.replaced_incarnation === null
            ? []
            : this.mailbox.replace_incarnation_in_transaction(tx, {
                replaced: accepted.replaced_incarnation,
                current: request.identity.executor_incarnation,
                now_ms: now,
              });
        const previous_lease = accepted.previous?.lease;
        const cleanup_changed =
          previous_lease == null ||
          !isDeepStrictEqual(previous_lease.cleanup_uncertainties, request.cleanup_uncertainties) ||
          previous_lease.legacy_cleanup_notice !== request.legacy_cleanup_notice;
        const announce =
          cleanup_changed ||
          lease_changes_presence_signal(accepted.previous, accepted.current, {
            new_incarnation: accepted.new_incarnation,
            now_ms: now,
          });
        return { accepted, settled, now, announce };
      },
      { changeSignal: 'silent' },
    );
  }
  private validated_lease(
    sql: ReadSql,
    request: WireValue<'LeaseRequest'>,
  ): WireValue<'LeaseRequest'> {
    const owner = (conversation_id: string): string | null => {
      const row =
        sql.get<{ machine_id: string }>('SELECT machine_id FROM mac_runs WHERE conversation_id=?', [
          conversation_id,
        ]) ??
        sql.get<{ machine_id: string }>(
          'SELECT i.machine_id FROM executor_child_assignments a ' +
            'JOIN executor_incarnations i USING(executor_incarnation) ' +
            'WHERE a.conversation_id=? LIMIT 1',
          [conversation_id],
        );
      if (row !== undefined && row.machine_id !== this.machine_id)
        throw new PlannerError('agent_forbidden', "lease references another machine's work");
      return row?.machine_id ?? null;
    };
    const waiting_conversations = request.waiting_conversations.filter(
      (waiting) => owner(waiting.conversation_id) !== null,
    );
    const working_turns = request.working_turns.filter((working) => {
      if (working.turn.conversation_id !== working.child.conversation_id)
        throw new PlannerError('validation', 'lease turn belongs to another conversation');
      owner(working.child.conversation_id);
      const incarnation = sql.get<{ machine_id: string }>(
        'SELECT machine_id FROM executor_incarnations WHERE executor_incarnation=?',
        [working.child.executor_incarnation],
      );
      if (incarnation !== undefined && incarnation.machine_id !== this.machine_id)
        throw new PlannerError('agent_forbidden', 'lease child belongs to another machine');
      return (
        sql.get(
          'SELECT 1 FROM mac_runs r JOIN executor_child_assignments a ' +
            'ON a.executor_incarnation=r.executor_incarnation AND a.child_id=r.child_id ' +
            'AND a.conversation_id=r.conversation_id WHERE r.machine_id=? ' +
            'AND r.executor_incarnation=? AND r.child_id=? AND r.conversation_id=? ' +
            "AND r.turn_number=? AND r.stop_state='open' AND a.closed_at_ms IS NULL",
          [
            this.machine_id,
            working.child.executor_incarnation,
            working.child.child_id,
            working.child.conversation_id,
            working.turn.turn_number,
          ],
        ) !== undefined
      );
    });
    for (const uncertainty of request.cleanup_uncertainties ?? []) {
      owner(uncertainty.conversation_id);
      const ticket = sql.get<{ machine_id: string }>('SELECT machine_id FROM tickets WHERE id=?', [
        uncertainty.ticket_id,
      ]);
      if (ticket !== undefined && ticket.machine_id !== this.machine_id)
        throw new PlannerError('agent_forbidden', 'cleanup evidence belongs to another machine');
    }
    return { ...request, waiting_conversations, working_turns };
  }
  private async check_update(): Promise<WireValue<'MacUpdateCheck'>> {
    if (this.closed) return no_update_check();
    try {
      return await this.update_check();
    } catch (error) {
      if (
        !(error instanceof UpdateConflict) &&
        !(error instanceof ExecutorValidationError) &&
        !(error instanceof SyntaxError) &&
        !is_os_error(error)
      )
        throw error;
      console.warn("The Mac's update target is unavailable:", error);
      return no_update_check();
    }
  }
  private schedule_presence_change(): void {
    clearTimeout(this.presence_timer);
    this.presence_timer = undefined;
    if (this.closed) return;
    const at = next_presence_change_ms(this.get_record());
    if (at !== null)
      this.presence_timer = setTimeout(
        () => {
          this.presence_timer = undefined;
          this.options.changes.emit();
        },
        Math.max(at - this.clock(), 0),
      );
  }
  refuse_old_websocket(): Promise<void> {
    this.admit();
    return this.own(() =>
      this.keep_refusal({
        reason: MAC_NEEDS_UPDATE,
        peer_identity: null,
        server_identity: this.server_identity(),
        refused_at_ms: this.clock(),
      }),
    );
  }
  private async keep_refusal(refusal: ExecutorRefusal): Promise<void> {
    const stored = this.get_record()?.refusal ?? null;
    if (
      stored !== null &&
      stored.reason === refusal.reason &&
      same_identity(stored.peer_identity, refusal.peer_identity)
    ) {
      this.contact();
      return;
    }
    const announce = this.database.write((tx) => record_refusal(tx, refusal, this.machine_id), {
      changeSignal: 'silent',
    });
    await Promise.resolve();
    const base = this.get_record() ?? empty_record();
    const contact = Math.max(base.last_contact_at_ms ?? 0, refusal.refused_at_ms);
    this.set_record({ ...base, refusal, last_contact_at_ms: contact });
    this.flushed_contact_ms = contact;
    this.schedule_presence_change();
    if (announce) this.options.changes.emit();
  }
  record_contact(): void {
    this.contact();
  }
  private contact(): void {
    const base = this.get_record() ?? empty_record();
    this.set_record({
      ...base,
      last_contact_at_ms: Math.max(base.last_contact_at_ms ?? 0, this.clock()),
    });
    if (this.closed || this.contact_flush !== undefined) return;
    this.contact_flush = setTimeout(() => {
      this.contact_flush = undefined;
      void this.own(() => {
        try {
          this.flush_contact();
        } catch (error) {
          console.warn("The Mac's contact time was not saved:", error);
        }
      });
    }, CONTACT_FLUSH_INTERVAL_SECONDS * 1000);
  }
  private flush_contact(): void {
    const contact = this.loaded_record?.last_contact_at_ms ?? null;
    if (
      contact === null ||
      (this.flushed_contact_ms !== null && contact <= this.flushed_contact_ms)
    )
      return;
    this.database.write((tx) => record_contact(tx, contact, this.machine_id), {
      changeSignal: 'silent',
    });
    this.flushed_contact_ms = contact;
  }
  require_current({
    incarnation_header,
    protocol_header,
  }: {
    incarnation_header: string | null;
    protocol_header: string | null;
  }): ExecutorIdentity {
    this.contact();
    if (protocol_header !== String(PROTOCOL_VERSION))
      throw new PlannerError(
        'executor_version_refused',
        'This Mac executor speaks another protocol.',
        { protocol_version: PROTOCOL_VERSION },
      );
    const record = this.get_record();
    if (
      record === null ||
      record.identity === null ||
      record.executor_incarnation === null ||
      incarnation_header !== record.executor_incarnation
    )
      throw not_current(incarnation_header || '');
    return record.identity;
  }
  record(): PresenceRecord | null {
    return this.get_record();
  }
  judgement() {
    return judge_presence(this.get_record(), this.clock());
  }
  is_present(): boolean {
    return this.judgement().presence === 'present';
  }
  current_identity(): ExecutorIdentity | null {
    const record = this.get_record();
    return record?.executor_incarnation == null ? null : record.identity;
  }
  health(): ExecutorHealthRead {
    const record = this.get_record();
    const judgement = judge_presence(record, this.clock());
    return {
      presence: judgement.presence,
      since_ms: judgement.since_ms,
      identity: record?.executor_incarnation == null ? null : record.identity,
      last_contact_at_ms: record?.last_contact_at_ms ?? null,
      refusal: record?.refusal ?? null,
      queue_depth: record?.lease?.queue_depth ?? null,
      oldest_item_age_ms: record?.lease?.oldest_item_age_ms ?? null,
      admission_stopped: record?.lease?.admission_stopped ?? false,
      cleanup_uncertainties: record?.lease?.cleanup_uncertainties ?? [],
      legacy_cleanup_notice: record?.lease?.legacy_cleanup_notice ?? null,
    };
  }
  private drained(conversation_id: string): boolean {
    const record = this.get_record();
    if (record?.lease == null || record.last_lease_at_ms === null) return false;
    return (
      judge_presence(record, this.clock()).presence === 'present' &&
      this.clock() - record.last_lease_at_ms <= LEASE_CONTINUITY_SECONDS * 1000 &&
      record.lease.identity.executor_incarnation === record.executor_incarnation &&
      !record.lease.waiting_conversations_truncated &&
      !(record.lease.cleanup_uncertainties ?? []).some(
        (uncertainty) => uncertainty.conversation_id === conversation_id,
      ) &&
      record.lease.waiting_conversations.every(
        (waiting) => waiting.conversation_id !== conversation_id,
      )
    );
  }
  wait_until_drained(
    conversation_id: string,
    options: { signal?: AbortSignal } = {},
  ): Promise<void> {
    this.admit();
    return this.own(async () => {
      if (this.drained(conversation_id)) return;
      let entry!: { conversation_id: string; resolve(): void; reject(error: unknown): void };
      const waiting = new Promise<void>((resolve, reject) => {
        entry = { conversation_id, resolve, reject };
      });
      this.drain_waiters.add(entry);
      const abort = () =>
        entry.reject(
          options.signal?.reason ?? new DOMException('Presence drain aborted.', 'AbortError'),
        );
      try {
        options.signal?.addEventListener('abort', abort, { once: true });
        if (options.signal?.aborted) abort();
        await waiting;
      } finally {
        options.signal?.removeEventListener('abort', abort);
        this.drain_waiters.delete(entry);
      }
    });
  }
  private wake_drains(): void {
    for (const entry of this.drain_waiters)
      if (this.drained(entry.conversation_id)) entry.resolve();
  }
  note_conversation_activity(conversation_id: string): void {
    if ([...this.working_turns.values()].some((turn) => turn.conversation_id === conversation_id))
      this.activity_ms.set(conversation_id, this.clock());
  }
  private observe_lease(
    request: WireValue<'LeaseRequest'>,
    accepted: AcceptedLease,
    now: number,
  ): void {
    const previous = accepted.new_incarnation ? new Map() : this.working_turns;
    const turns = new Map<
      string,
      { conversation_id: string; turn_number: number; first_seen_ms: number; tool_running: boolean }
    >();
    for (const working of request.working_turns) {
      const conversation_id = working.turn.conversation_id;
      const turn_number = working.turn.turn_number;
      const key = `${conversation_id}:${turn_number}`;
      turns.set(key, {
        conversation_id,
        turn_number,
        first_seen_ms: previous.get(key)?.first_seen_ms ?? now,
        tool_running: working.tool_running,
      });
      if (working.tool_running) this.activity_ms.set(conversation_id, now);
    }
    this.working_turns = turns;
    const working_conversations = new Set([...turns.values()].map((turn) => turn.conversation_id));
    for (const conversation_id of this.activity_ms.keys())
      if (!working_conversations.has(conversation_id)) this.activity_ms.delete(conversation_id);
  }
  tracked_waits(): readonly TrackedWait[] {
    return [...this.working_turns.values()]
      .filter((turn) => !turn.tool_running)
      .map((turn) => ({
        kind: 'mac_turn_silent',
        wait_key: `${turn.conversation_id}:${turn.turn_number}`,
        conversation_id: turn.conversation_id,
        machine_id: null,
        detail: `Turn ${turn.turn_number} is working on the Mac but sends nothing`,
        started_at_ms: Math.max(
          turn.first_seen_ms,
          this.activity_ms.get(turn.conversation_id) ?? 0,
        ),
      }));
  }
  stall_source(): StallSource {
    return { tracked_waits: () => this.tracked_waits() };
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.closed) {
      this.closed = true;
      clearTimeout(this.contact_flush);
      clearTimeout(this.presence_timer);
      this.contact_flush = undefined;
      this.presence_timer = undefined;
      for (const waiter of this.drain_waiters)
        waiter.reject(new DOMException('Executor presence is closed.', 'AbortError'));
      void this.drain().then(this.resolve_join, this.reject_join);
    }
    return observe_shutdown(
      this.resource_join,
      options,
      'Executor presence work remains unfinished at the shutdown deadline.',
    );
  }
  private async drain(): Promise<void> {
    const seen = new Set<Promise<unknown>>();
    let pending = [...this.operations];
    while (pending.length) {
      for (const operation of pending) seen.add(operation);
      for (const result of await Promise.allSettled(pending))
        if (result.status === 'rejected' && !is_shutdown_business_cancellation(result.reason))
          this.failures.add(result.reason);
      pending = [...this.operations].filter((operation) => !seen.has(operation));
    }
    try {
      this.flush_contact();
    } catch (error) {
      this.failures.add(error);
    }
    if (this.failures.size)
      throw new AggregateError([...this.failures], 'Executor presence shutdown failed.');
  }
  settled(): Promise<void> {
    return this.resource_join;
  }
}
