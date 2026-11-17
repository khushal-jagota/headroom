/** Actual production record composition plus the shared scripted child, without a server. */
import { dirname, join } from 'node:path';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { createDatabase } from '../../core/database/index.ts';
import { loadConfig } from '../../core/config.ts';
import { createAreasRuntime } from '../../areas.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { BackendStateStore } from '../backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../../tickets/execution_admission.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { delivery_fate_json } from '../http.ts';
import { text_message_content } from '../message_content.ts';
import { ContextCompacted, ToolCallStarted } from '../backends/contracts.ts';
import { ScriptedBackendRegistry } from './scripted_registry.ts';
import { reset_ticket_conversation } from '../../runtime/conversation_start.ts';
import { read_ticket } from '../../tickets/data/index.ts';
import { compose_worker_step_prompt } from '../../runtime/logic/worker_step_prompt.ts';
import {
  MEMORY_NOTICE_MESSAGE_PREFIX,
  conversation_holds_an_unanswered_memory_loss,
  ticket_needs_memory_notice,
} from '../../runtime/worker_memory.ts';

interface Spec {
  database: string;
  case: string;
}
export async function run(spec: Spec): Promise<unknown> {
  let now = 1000;
  let sequence = 0;
  const root = dirname(spec.database);
  const database = createDatabase(spec.database);
  const areas = createAreasRuntime();
  const config = loadConfig(join(root, 'absent-config.yaml'), {
    PLAN_DB_PATH: spec.database,
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'synthetic-credential-key'),
    PLAN_WORKSPACE_ROOT: root,
    PLAN_TEST_MODE: '1',
  });
  const registry = new ScriptedBackendRegistry();
  const clock = {
    now: () => new Date(now * 1000),
    nowUnix: () => now,
    isoformat: () => new Date(now * 1000).toISOString(),
  };
  await areas.prepareRecord(database, config, clock, registry.custody.inspection, new Map());
  areas.initialize(database, config, clock);
  const { store } = areas.getRuntime();
  const core = new SqliteProcessConversationSystem({
    store,
    delivery_model_refusal: (backend, model, ticket_id) =>
      new BackendStateStore(database).delivery_model_refusal(backend, model, ticket_id),
    execution_refusal: (id) =>
      database.read((sql) =>
        conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
      ),
    backend_registry: registry.registry,
    message_files: new ConversationMessageFiles(spec.database),
    unix_time_now: () => now,
    monotonic_now: () => now,
  });
  areas.startRecordReplay(core.workspace_admission);
  const identity = spec.case === 'replaced' ? 'old' : 'c';
  const checkpoints: Record<string, unknown>[] = [];
  const tables = [
    'conversations',
    'conversation_events',
    'conversation_lifecycle_facts',
    'conversation_view_state',
    'conversation_view_rows',
    'conversation_view_turns',
    'conversation_view_groups',
    'conversation_view_lookups',
  ];
  const send = async (text: string, notice = false, steer = false, wake = false) => {
    sequence++;
    return delivery_fate_json(
      await core.send(identity, text_message_content(text), {
        sender_label: notice ? 'Panels' : 'owner',
        sender_message_id:
          (notice
            ? MEMORY_NOTICE_MESSAGE_PREFIX
            : wake || (spec.case === 'memory_only' && sequence === 1)
              ? 'worker_step_'
              : 'owner-') + sequence,
        mode: steer ? 'steer' : 'queue',
      }),
    );
  };
  const finish = async () => {
    await registry.child(identity).finish();
    await core.wait_until_quiescent();
  };
  const compact = async () => {
    await registry.child(identity).emit(new ContextCompacted());
    await registry.child(identity).flush_events();
    await core.wait_until_quiescent();
  };
  const sweep = async () => {
    await core.sweep_idle_children();
    await core.wait_until_quiescent();
  };
  const backlog = (value = 1) =>
    database.write((tx) =>
      tx.run('UPDATE tickets SET is_backlogged=? WHERE id=?', [value, 't_worker']),
    );
  const capture = async (name: string, fates: unknown[] = []) => {
    const rows = database.read((sql) =>
      Object.fromEntries(
        tables.map((table) => [
          table,
          sql.all(
            `SELECT * FROM ${table} ORDER BY ${table === 'conversation_lifecycle_facts' ? 'conversation_id,turn_number,kind,operation,identity' : table === 'conversation_view_lookups' ? 'conversation_id,lookup_kind,source_sequence,lookup_id' : '1,2'}`,
          ),
        ]),
      ),
    );
    checkpoints.push({
      name,
      fates,
      rows,
      native: await registry.inspect(identity),
      open_memory_only: store.open_turn_holds_only_memory_notices(identity),
      eligibility: store.automated_maintenance_suppression_reason(identity),
      debt: database.read((sql) => conversation_holds_an_unanswered_memory_loss(sql, identity)),
      notice_candidate: database.read((sql) => ticket_needs_memory_notice(sql, 't_worker')),
    });
  };
  const freeze_selection = async (change: () => unknown | Promise<unknown>) => {
    const select = store.conversations_due_for_automatic_compaction.bind(store);
    const selected = select({ due_at_or_before: now - 50 * 60, activity_after: now - 60 * 60 });
    await change();
    store.conversations_due_for_automatic_compaction = () => {
      store.conversations_due_for_automatic_compaction = select;
      return selected;
    };
    return selected;
  };
  try {
    if (spec.case === 'memory_only') {
      database.write((tx) =>
        tx.run("UPDATE tickets SET worker_step_claim='out' WHERE id='t_worker'"),
      );
      await send('real work');
      await finish();
      now += 50 * 60;
      await sweep();
      await compact();
      await finish();
      await capture('compact-debt');
      backlog();
      await capture('backlogged-debt');
      backlog(0);
      const stale = await send('Your context was compacted', true);
      await capture('idle-notice-is-stale', [stale]);
      await send('work that compacts', false, false, true);
      await compact();
      await finish();
      const fate = await send('Your context was compacted', true);
      await registry
        .child(identity)
        .emit(
          new ToolCallStarted({ tool_call_id: 't1', title: 'panels worker', tool_kind: 'execute' }),
        );
      await registry.child(identity).flush_events();
      await core.wait_until_quiescent();
      await capture('active-memory-notice', [fate]);
      await finish();
      now += 51 * 60;
      await sweep();
      await capture('memory-does-not-recompact');
      // Send outside the compaction window: Python still compacts at send time there.
      now += 10 * 60;
      const owner = await send('more work');
      await finish();
      now += 50 * 60;
      await sweep();
      await capture('real-work-restores-compaction', [owner]);
    } else if (['held_batch', 'steer_accepted', 'steer_uncertain'].includes(spec.case)) {
      database.write((tx) =>
        tx.run("UPDATE tickets SET worker_step_claim='out' WHERE id='t_worker'"),
      );
      await send('work that compacts', false, false, true);
      await compact();
      await finish();
      const first = await send('Your context was compacted', true);
      await capture('notice-started', [first]);
      let second, owner;
      if (spec.case === 'held_batch') {
        second = await send('Your context was compacted', true);
        owner = await send('owner');
        await finish();
      } else {
        await registry.configure(identity, 'steer', {
          result: {
            fate: spec.case === 'steer_uncertain' ? 'uncertain' : 'accepted',
            composed_content_delivered: true,
          },
        });
        second = await send('owner', false, true);
        owner = second;
      }
      await capture('real-work-joined', [second, owner]);
      await finish();
      now += 50 * 60;
      await sweep();
      await capture('joined-work-is-due');
      if (spec.case === 'held_batch') {
        const queue_wake = async (revision: number) => {
          const sender_message_id = `worker_step_message_t_worker:${revision}`;
          database.write((tx) =>
            tx.run(
              "INSERT INTO worker_step_dispatch_intents VALUES ('t_worker',?,?, 'dispatch_started')",
              [revision, sender_message_id],
            ),
          );
          const ticket = database.read((sql) => read_ticket(sql, 't_worker'));
          const text = compose_worker_step_prompt(ticket, {
            worker_type_definition: ticket.workflow,
          }).model_text;
          const fate = await core.send(identity, text_message_content(text), {
            sender_label: 'Panels',
            sender_message_id,
          });
          return { sender_message_id, text, fate: delivery_fate_json(fate) };
        };
        let stale = await send('Your context was compacted', true);
        let wake = await queue_wake(0);
        await queue_wake(1);
        const first_child = registry.child(identity);
        await registry.configure(identity, 'write_prompt', {
          barrier: 'memory-rebind',
          error: { name: 'NeedsRebind', message: 'fresh child' },
        });
        await compact();
        const finishing = finish();
        await first_child.wait_for_barrier('memory-rebind');
        database.write((tx) =>
          tx.run(
            'UPDATE conversations SET automatically_compacted_through_sequence=0 WHERE conversation_id=?',
            [identity],
          ),
        );
        await first_child.release('memory-rebind');
        await finishing;
        await capture('wake-rebind-recomputed', [stale, wake.fate]);
        checkpoints.at(-1)!.wake = { sender_message_id: wake.sender_message_id, text: wake.text };
        checkpoints.at(-1)!.awaiting = [
          ...(await core.sender_message_ids_awaiting_outcome(identity)),
        ].sort();
        stale = await send('Your context was compacted', true);
        wake = await queue_wake(2);
        await registry.configure(identity, 'write_prompt', {
          error: { name: 'Error', message: 'acknowledgement lost after possible emission' },
        });
        await compact();
        await finish();
        const replay = await core.send(identity, text_message_content(wake.text), {
          sender_label: 'Panels',
          sender_message_id: wake.sender_message_id,
        });
        await capture('wake-note-uncertain', [stale, wake.fate, delivery_fate_json(replay)]);
        checkpoints.at(-1)!.wake = { sender_message_id: wake.sender_message_id, text: wake.text };
        checkpoints.at(-1)!.awaiting = [
          ...(await core.sender_message_ids_awaiting_outcome(identity)),
        ].sort();
      }
    } else {
      await send('real work');
      if (spec.case === 'queued_backlog') {
        const notice = await send('Your context was compacted', true),
          owner = await send('behind it');
        backlog();
        await finish();
        await capture('queued-notice-suppressed-owner-survives', [notice, owner]);
      } else {
        await finish();
        now += (spec.case === 'replaced' ? 51 : 50) * 60;
        let selected: readonly string[] = [];
        if (spec.case === 'before_selection') backlog();
        else if (spec.case === 'after_selection')
          selected = await freeze_selection(() => backlog());
        else
          selected = await freeze_selection(() =>
            reset_ticket_conversation(core, database, 't_worker', { now: 2 }),
          );
        await sweep();
        now += 5 * 60;
        await sweep();
        await capture('maintenance-suppressed');
        checkpoints.at(-1)!.selected_before_change = selected;
        let fate;
        if (spec.case === 'replaced') {
          now += 10 * 60;
          fate = await send('Your context was compacted', true);
        } else fate = await send('still there?');
        await core.wait_until_quiescent();
        await capture('later-send', [fate]);
      }
    }
    return { case: spec.case, checkpoints };
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    await registry.close();
    await areas.closeRecord();
    database.close();
  }
}
