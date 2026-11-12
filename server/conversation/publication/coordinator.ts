/** Publish current runtime and durable facts within ordinary source transactions. */
import { randomUUID } from 'node:crypto';
import type { Database, TransactionSql } from '../../core/database/contracts.ts';
import type { CurrentAuthority } from '../../../web/src/lib/navigation/contracts.ts';
import {
  SignalUnavailable,
  SignalSnapshot,
  SignalBatch,
  ConversationSignalPublicationV1,
} from '../contracts.ts';
import type {
  ConversationAttention,
  SignalAttachment,
  SignalAvailability,
  SignalInvalidation,
  SignalPublicationSource,
  SignalParticipant,
  SignalInstaller,
  SignalDeletion,
} from '../contracts.ts';
import type { SaveAttempt } from '../runtime/contracts.ts';
import { save_attempt } from '../runtime/save_attempt.ts';
import { read_durable_facts } from './reader.ts';
import * as persistence from './persistence.ts';

interface PendingTurn {
  readonly pending_permission_ask_ids: ReadonlySet<string>;
  readonly pending_user_input_questions: ReadonlyMap<string, unknown>;
}
const identity = () => randomUUID().replaceAll('-', '');

function sync<T>(work: () => T): T {
  const result = work();
  if (
    result !== null &&
    (typeof result === 'object' || typeof result === 'function') &&
    typeof (result as { then?: unknown }).then === 'function'
  ) {
    if (result instanceof Promise) void result.catch(() => {});
    throw new TypeError('Signal publication callbacks must be synchronous');
  }
  return result;
}

export class SignalPublicationCoordinator implements SignalPublicationSource {
  private readonly database: Database;
  private readonly require_ownership: () => void;
  private readonly delivery_session_id = identity();
  private scope = '';
  private epoch = '';
  private initialized = false;
  private phase: 'bootstrap' | 'installation' | 'runtime' = 'bootstrap';
  private generation = 0;
  private state: SignalAvailability['state'] = 'initializing';
  private reason: string | null = 'initializing';
  private turn: ((id: string) => PendingTurn | null) | null = null;
  private attachment: SignalAttachment | null = null;
  private participant: SignalParticipant | null = null;
  private unavailable: ((value: SignalInvalidation) => void) | null = null;
  private attempt: { id: string; mutated: boolean } | null = null;
  private readonly watchers = new Set<() => void>();

  constructor(options: { database: Database; require_runtime_ownership: () => void }) {
    this.database = options.database;
    this.require_ownership = options.require_runtime_ownership;
  }

  get runtime_epoch(): string {
    return this.epoch;
  }

  bind_runtime(reader: (id: string) => PendingTurn | null): void {
    if (this.phase === 'runtime') throw new Error('cannot replace live Signal runtime bindings');
    this.turn = reader;
  }

  private require_authority(): void {
    try {
      this.require_ownership();
    } catch (error) {
      this.invalidate('authority_lost');
      throw error;
    }
  }

  current_read(): void {
    this.require_authority();
    if (this.state !== 'current')
      throw new SignalUnavailable(this.reason ?? 'Signal is not active');
  }

  signal_availability(): SignalAvailability {
    try {
      this.require_authority();
    } catch {
      /* Availability reports lost ownership instead of granting a permit. */
    }
    return { generation: this.generation, state: this.state, reason: this.reason };
  }

  private attention(tx: TransactionSql, id: string): ConversationAttention | null {
    const durable = read_durable_facts(tx, id);
    if (!durable) return null;
    if (!this.turn) throw new Error('Signal runtime is not bound');
    const turn = this.turn(id);
    return {
      signal: {
        working: turn !== null || durable.has_live_background_task,
        message_for_owner: durable.message_for_owner,
      },
      awaiting_answer:
        turn !== null &&
        (turn.pending_permission_ask_ids.size > 0 || turn.pending_user_input_questions.size > 0),
      last_turn_failed: durable.last_turn_failed,
    };
  }

  /** Call only after the record owner reconciles the runtime. No domain repair happens here. */
  initialize(): void {
    this.require_authority();
    if (!this.turn) throw new Error('bind reconciled runtime before Signal initialization');
    if (this.initialized) throw new Error('Signal producer is already initialized');
    const epoch = identity();
    const scope = this.database.write(
      (tx) => {
        const prior = tx.get<{ scope: string }>('SELECT scope FROM signal_publication_meta');
        const scope = prior?.scope ?? identity();
        tx.run('DELETE FROM signal_publication_meta');
        tx.run('INSERT INTO signal_publication_meta VALUES(1,?,?,0)', [scope, epoch]);
        tx.run('DELETE FROM signal_publication_values');
        tx.run('DELETE FROM signal_publication_dirty');
        tx.run('DELETE FROM signal_publication_associations');
        for (const row of tx.all<{ conversation_id: string }>(
          'SELECT conversation_id FROM conversations ORDER BY conversation_id',
        )) {
          const attention = this.attention(tx, row.conversation_id);
          if (!attention) throw new Error('complete Signal initialization omitted a conversation');
          const value = new ConversationSignalPublicationV1({
            conversation_id: row.conversation_id,
            runtime_epoch: epoch,
            conversation_revision: 0,
            attention,
          });
          tx.run('INSERT INTO signal_publication_values VALUES(?,0,?)', [
            row.conversation_id,
            persistence.encode(value),
          ]);
        }
        return scope;
      },
      { changeSignal: 'silent' },
    );
    this.scope = scope;
    this.epoch = epoch;
    this.initialized = true;
    this.phase = 'installation';
  }

  readonly save_attempt: SaveAttempt = (id, work, mutations) => {
    const previous = this.attempt;
    this.attempt = { id, mutated: mutations.length > 0 };
    try {
      return save_attempt(id, work, mutations);
    } finally {
      this.attempt = previous;
    }
  };

  readonly finalize_signal = (tx: TransactionSql): void => {
    if (!this.initialized) return;
    if (this.attempt?.mutated) persistence.mark_changed(tx, this.attempt.id);
    const relevant = tx.get('SELECT 1 FROM signal_publication_dirty LIMIT 1');
    const associated = tx.get('SELECT 1 FROM signal_publication_associations LIMIT 1');
    if (!relevant && !associated) return;
    const base = persistence.cursor(tx);
    if (base.runtime_epoch !== this.epoch) throw new SignalUnavailable('obsolete producer epoch');
    const replacements: ConversationSignalPublicationV1[] = [];
    const deletions: SignalDeletion[] = [];
    for (const row of tx.all<{ conversation_id: string }>(
      'SELECT conversation_id FROM signal_publication_dirty ORDER BY conversation_id',
    )) {
      const id = row.conversation_id;
      const previous = tx.get<{ revision: number; value: string | null }>(
        'SELECT revision,value FROM signal_publication_values WHERE conversation_id=?',
        [id],
      );
      const attention = this.attention(tx, id);
      let body: string | null;
      let revision: number;
      if (attention === null) {
        if (!previous || previous.value === null) continue;
        revision = previous.revision + 1;
        deletions.push({
          conversation_id: id,
          runtime_epoch: this.epoch,
          conversation_revision: revision,
        });
        body = null;
      } else {
        if (
          previous?.value !== undefined &&
          previous.value !== null &&
          persistence.encode(
            (JSON.parse(previous.value) as ConversationSignalPublicationV1).attention,
          ) === persistence.encode(attention)
        )
          continue;
        revision = previous ? previous.revision + 1 : 0;
        const value = new ConversationSignalPublicationV1({
          conversation_id: id,
          runtime_epoch: this.epoch,
          conversation_revision: revision,
          attention,
        });
        replacements.push(value);
        body = persistence.encode(value);
      }
      tx.run(
        `INSERT INTO signal_publication_values VALUES(?,?,?)
        ON CONFLICT(conversation_id) DO UPDATE SET revision=excluded.revision,value=excluded.value`,
        [id, revision, body],
      );
    }
    const associations = persistence.associations(tx);
    const cursor = { ...base, sequence: base.sequence + 1 };
    const batch = new SignalBatch({
      base_cursor: base,
      cursor,
      replacements,
      deletions,
      associations,
    });
    tx.run('UPDATE signal_publication_meta SET sequence=?', [cursor.sequence]);
    tx.run('DELETE FROM signal_publication_dirty');
    tx.run('DELETE FROM signal_publication_associations');
    if (this.participant) sync(() => this.participant!(tx, batch));
  };

  attach_signal_publication(options: {
    install: SignalInstaller;
    participate: SignalParticipant;
    unavailable: (value: SignalInvalidation) => void;
  }): SignalAttachment {
    this.require_authority();
    if (!this.initialized) throw new Error('Signal producer is not initialized');
    const snapshot = this.database.write((tx) => {
      const snapshot = new SignalSnapshot({
        installation_id: identity(),
        cursor: persistence.cursor(tx),
        values: persistence.values(tx),
      });
      sync(() => options.install(tx, snapshot));
      return snapshot;
    });
    let closed = false;
    const attachment: SignalAttachment = {
      capture_permit: () => {
        this.current_read();
        if (closed || this.attachment !== attachment)
          throw new SignalUnavailable('Signal attachment is not active');
        return {
          scope: this.scope,
          runtime_epoch: this.epoch,
          installation_id: snapshot.installation_id,
          generation: this.generation,
        };
      },
      validate: (permit) => {
        if (persistence.encode(permit) !== persistence.encode(attachment.capture_permit()))
          throw new SignalUnavailable('Signal generation changed');
      },
      close: () => {
        if (closed) return;
        closed = true;
        if (this.attachment === attachment) {
          this.invalidate('detached');
          this.attachment = null;
          this.participant = null;
          this.unavailable = null;
        }
      },
    };
    // Installation can fail. Retain the old permits and subscriptions until its
    // transaction succeeds, then replace the authority before notifying them.
    const previous_unavailable = this.unavailable;
    const previous_watchers = [...this.watchers];
    this.watchers.clear();
    this.participant = options.participate;
    this.unavailable = options.unavailable;
    this.generation += 1;
    this.attachment = attachment;
    this.phase = 'runtime';
    this.state = 'current';
    this.reason = null;
    this.notify_invalidation('detached', previous_unavailable, previous_watchers);
    return attachment;
  }

  invalidate(reason: SignalInvalidation['reason']): void {
    this.generation += 1;
    this.state = 'unavailable';
    this.reason = reason;
    const watchers = [...this.watchers];
    this.watchers.clear();
    this.notify_invalidation(reason, this.unavailable, watchers);
  }

  private notify_invalidation(
    reason: SignalInvalidation['reason'],
    unavailable: ((value: SignalInvalidation) => void) | null,
    watchers: readonly (() => void)[],
  ): void {
    if (unavailable) {
      try {
        sync(() => unavailable({ generation: this.generation, reason }));
      } catch {
        /* Generation remains revoked when a consumer fails. */
      }
    }
    for (const watcher of watchers) {
      try {
        sync(watcher);
      } catch {
        /* One consumer cannot prevent revocation of the others. */
      }
    }
  }

  capture(): CurrentAuthority {
    this.current_read();
    if (!this.attachment) throw new SignalUnavailable('Signal attachment is not active');
    const permit = this.attachment.capture_permit();
    return {
      delivery_session_id: this.delivery_session_id,
      availability: { state: 'current', generation: String(permit.generation), reason: null },
      permit: { ...permit, generation: String(permit.generation) },
    };
  }

  validate(value: CurrentAuthority): void {
    if (persistence.encode(value) !== persistence.encode(this.capture()))
      throw new SignalUnavailable('Signal generation changed');
  }

  watch(value: CurrentAuthority, invalidate: () => void): () => void {
    this.validate(value);
    const notify = () => invalidate();
    this.watchers.add(notify);
    return () => {
      this.watchers.delete(notify);
    };
  }
}
