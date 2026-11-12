/** One selected real transaction candidate fails eight times, then recovers normally. */
import type { TestCompositionContext } from '../../core/start.ts';
import type { TransactionSql } from '../../core/database/contracts.ts';
import type {
  ConversationRecordStore,
  StoredConversationEvent,
  RecordCommitParticipant,
} from '../record/contracts.ts';
import { decode_scripted_event } from './scripted_protocol.ts';
import type { ScriptedBackendRegistry } from './scripted_registry.ts';

export class TerminalSaveProbe {
  private readonly context: TestCompositionContext;
  private readonly registry: ScriptedBackendRegistry;
  private slot: {
    id: string;
    original: ConversationRecordStore['commit_backend_event'];
    error: typeof console.error;
    failures: number;
    candidates: { attempt: number; events: number; views: number; lifecycle: number }[];
    retries: { failure: number; delay: number; error: string }[];
    first_failure: Promise<void>;
    failed(): void;
    observer: Promise<void> | null;
  } | null = null;

  constructor(context: TestCompositionContext, registry: ScriptedBackendRegistry) {
    this.context = context;
    this.registry = registry;
  }

  private require(id: string) {
    if (!this.slot || this.slot.id !== id) throw new Error('No matching terminal save probe');
    return this.slot;
  }

  arm(id: string) {
    if (this.slot) throw new Error('A terminal save probe is already occupied');
    const store = this.context.record.store;
    let failed!: () => void;
    const first_failure = new Promise<void>((resolve) => {
      failed = resolve;
    });
    const slot = {
      id,
      original: store.commit_backend_event,
      error: console.error,
      failures: 0,
      candidates: [] as { attempt: number; events: number; views: number; lifecycle: number }[],
      retries: [] as { failure: number; delay: number; error: string }[],
      first_failure,
      failed,
      observer: null as Promise<void> | null,
    };
    this.slot = slot;
    const commit = store.commit_backend_event.bind(store);
    store.commit_backend_event = function <T>(
      writer: (tx: TransactionSql) => { result: T; written: readonly StoredConversationEvent[] },
      participant: RecordCommitParticipant<T> | null = null,
      options: { always_announce?: boolean } = {},
    ) {
      return commit<T>(
        (tx) => {
          const candidate = writer(tx);
          if (
            candidate.written.some((row) => row.conversation_id === id && row.kind === 'turn_ended')
          ) {
            const count = (table: string) =>
              tx.get<{ count: number }>(
                `SELECT count(*) AS count FROM ${table} WHERE conversation_id=?`,
                [id],
              )!.count;
            slot.candidates.push({
              attempt: slot.failures + 1,
              events: count('conversation_events'),
              views: count('conversation_view_rows'),
              lifecycle: count('conversation_lifecycle_facts'),
            });
            if (slot.failures < 8) {
              slot.failures++;
              throw new Error('disk unavailable');
            }
          }
          return candidate;
        },
        participant,
        options,
      );
    };
    console.error = (...args: unknown[]) => {
      const message = String(args[0]);
      const prefix = `conversation ${id} save failed `;
      if (message.startsWith(prefix)) {
        const match = /\(failure (\d+)\); retrying in ([0-9.]+) s/.exec(message);
        if (match)
          slot.retries.push({
            failure: Number(match[1]),
            delay: Number(match[2]),
            error: args[1] instanceof Error ? args[1].message : String(args[1]),
          });
        if (match) slot.failed();
      }
      slot.error(...args);
    };
    return { armed: true };
  }

  async begin(id: string) {
    const slot = this.require(id);
    if (slot.observer) throw new Error('The terminal save probe has already begun');
    const child = this.registry.child(id);
    const token = (await child.inspect()).token;
    if (!token) throw new Error('A scripted turn must exist before terminal save');
    await child.emit(
      decode_scripted_event({
        kind: 'TurnEnded',
        values: {
          ending: 'failed',
          error_summary: 'the model refused',
          standard_error_tail: null,
        },
      }),
      token,
    );
    slot.observer = child.flush_events();
    void slot.observer.catch(() => {});
    await slot.first_failure;
    await child.emit(
      decode_scripted_event({
        kind: 'ToolCallStarted',
        values: {
          tool_call_id: 'late',
          title: 'Read a file',
          tool_kind: 'read',
        },
      }),
      token,
    );
    const turn = this.context.core!.runtime_turn(id);
    return this.context.database.read((sql) => ({
      failures: slot.failures,
      events: sql.all(
        'SELECT sequence,kind FROM conversation_events WHERE conversation_id=? ORDER BY sequence',
        [id],
      ),
      running: turn !== null,
      ended: turn?.ended ?? null,
    }));
  }

  async join(id: string) {
    const slot = this.require(id);
    try {
      if (!slot.observer) throw new Error('The terminal save probe has not begun');
      await slot.observer;
      await this.registry.child(id).flush_events();
      await this.context.core!.wait_until_quiescent();
      return { failures: slot.failures, candidates: slot.candidates, retries: slot.retries };
    } finally {
      this.restore();
    }
  }

  private restore() {
    if (!this.slot) return;
    this.context.record.store.commit_backend_event = this.slot.original;
    console.error = this.slot.error;
    this.slot = null;
  }

  async close() {
    const observer = this.slot?.observer;
    this.restore();
    if (observer) await Promise.allSettled([observer]);
  }
}
