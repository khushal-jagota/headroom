/** Bounded observations for source promises that ordinary settling reads cannot prove. */
import type { TestCompositionContext } from '../../core/start.ts';
import { OWNER } from '../../core/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import { text_message_content } from '../message_content.ts';
import { delivery_fate_json } from '../http.ts';
import type { ScriptedBackendRegistry } from './scripted_registry.ts';
import type { ScriptedChild } from './scripted_child.ts';

/** Exactly one owned steer and its ending observer; never a general task registry. */
export class PendingSteerProbe {
  private readonly context: TestCompositionContext;
  private readonly registry: ScriptedBackendRegistry;
  private pending: {
    id: string;
    barrier: string;
    child: ScriptedChild;
    steer: Promise<Record<string, unknown>>;
    ending: Promise<void> | null;
    ending_done: boolean;
  } | null = null;

  constructor(context: TestCompositionContext, registry: ScriptedBackendRegistry) {
    this.context = context;
    this.registry = registry;
  }

  require(id: string) {
    if (!this.pending || this.pending.id !== id) throw new Error('No matching pending steer probe');
    return this.pending;
  }

  async begin(id: string, body: { text: string; recipient: Principal; barrier: string }) {
    if (this.pending) throw new Error('A pending steer probe is already occupied');
    const core = this.context.core!;
    const child = this.registry.child(id);
    const steer = core
      .send(id, text_message_content(body.text), {
        sender_label: 'owner',
        sender: OWNER,
        recipient: body.recipient,
        mode: 'steer',
      })
      .then(delivery_fate_json);
    // Own the rejection immediately; release/join still returns the original failure.
    void steer.catch(() => {});
    this.pending = { id, barrier: body.barrier, child, steer, ending: null, ending_done: false };
    try {
      await child.wait_for_barrier(body.barrier);
      return { begun: true };
    } catch (error) {
      this.pending = null;
      throw error;
    }
  }

  ending(id: string, observer: Promise<void>): void {
    const slot = this.require(id);
    if (slot.ending) throw new Error('The pending steer already owns an ending observer');
    slot.ending = observer;
    void observer.then(
      () => {
        slot.ending_done = true;
      },
      () => {
        slot.ending_done = true;
      },
    );
  }

  observe(id: string) {
    const slot = this.require(id);
    const turn = this.context.core!.runtime_turn(id);
    return this.context.database.read((sql) => ({
      events: sql
        .all<Record<string, unknown>>(
          'SELECT sequence,kind,payload FROM conversation_events WHERE conversation_id=? ORDER BY sequence',
          [id],
        )
        .map((row) => ({ ...row, payload: JSON.parse(row.payload as string) })),
      owner_read_through_sequence: sql.get<{ owner_read_through_sequence: number }>(
        'SELECT owner_read_through_sequence FROM conversations WHERE conversation_id=?',
        [id],
      )!.owner_read_through_sequence,
      running: turn !== null,
      ended: turn?.ended ?? null,
      ending_done: slot.ending_done,
    }));
  }

  async release(id: string) {
    const slot = this.require(id);
    try {
      await slot.child.release(slot.barrier);
      const fate = await slot.steer;
      if (slot.ending) await slot.ending;
      await this.registry.child(id).flush_events();
      await this.context.core!.wait_until_quiescent();
      const signal = this.context.database.read((sql) => {
        const row = sql.get<{ value: string }>(
          'SELECT value FROM signal_publication_values WHERE conversation_id=?',
          [id],
        );
        return row ? JSON.parse(row.value).attention.signal : null;
      });
      return { fate, ending_done: slot.ending_done, signal };
    } finally {
      this.pending = null;
    }
  }

  async close(): Promise<void> {
    const slot = this.pending;
    if (!slot) return;
    try {
      await slot.child.release(slot.barrier).catch(() => {});
      await Promise.allSettled([slot.steer, ...(slot.ending ? [slot.ending] : [])]);
    } finally {
      this.pending = null;
    }
  }
}

export async function live_status_before_adoption(
  context: TestCompositionContext,
  conversation_id: string,
  include_runtime_epoch = false,
): Promise<Record<string, unknown>> {
  const core = context.core;
  if (!core) throw new Error('Scripted probes require the real conversation core');
  const store = context.record.store;
  const before_keys = [...core.conversations.keys()].sort();
  const original = store.read_conversation;
  let reads = 0;
  store.read_conversation = function (...args) {
    reads++;
    return original.apply(this, args);
  };
  try {
    const values: Record<string, Record<string, boolean>> = {};
    for (const [name, id] of [
      ['retained', conversation_id],
      ['missing', `${conversation_id}-missing`],
    ] as const) {
      values[name] = {
        is_running: await core.is_running(id),
        has_pending_permission_ask: await core.has_pending_permission_ask(id),
        has_pending_user_input: await core.has_pending_user_input(id),
      };
    }
    return {
      values,
      read_count: reads,
      before_keys,
      after_keys: [...core.conversations.keys()].sort(),
      ...(include_runtime_epoch
        ? {
            runtime_epoch_rows: context.database.read((sql) =>
              sql.all<{ singleton: number; epoch: string }>(
                'SELECT singleton,epoch FROM conversation_runtime_epoch ORDER BY singleton',
              ),
            ),
          }
        : {}),
    };
  } finally {
    store.read_conversation = original;
  }
}
