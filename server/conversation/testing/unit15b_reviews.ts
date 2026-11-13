/** Real-core companion for the fresh Python review-start comparison. */
import { configure_conversation_start } from '../../runtime/conversation_start.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { createDatabase } from '../../core/database/index.ts';
import { load_worker_runtime_definitions } from '../../worker_types/configuration.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { ConversationStartRequest, PromptDispatchDecision } from '../contracts.ts';
import { text_message_content } from '../message_content.ts';
import { ScriptedBackendRegistry } from './scripted_registry.ts';
import {
  admit_notice_batch,
  all_sender_messages_are_manager_notices,
  mark_notices_leaving_queue,
  claim_next_batch,
  create_ticket_failure_notice,
} from '../../manager_wakes/data.ts';
import { accept_proposal, edit_ticket, file_current_proposal } from '../../tickets/data/index.ts';
import { deliver_batch } from '../../manager_wakes/runtime.ts';
import { batches_waiting_for_outcome } from '../../manager_wakes/data.ts';
import type { WakeBatch } from '../../manager_wakes/contracts.ts';
import { dirname } from 'node:path';
import { OWNER, CHIEF } from '../../core/contracts.ts';
import { delivery_fate_json } from '../http.ts';
import { model_is_enabled } from '../backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../../tickets/execution_admission.ts';

interface Spec {
  path: string;
  case: string;
  ticket: string;
  sender: string;
  message: string;
  manager: string;
}
export async function run(spec: Spec) {
  const database = createDatabase(spec.path);
  database.read(load_worker_runtime_definitions);
  configure_conversation_start(database, {
    db_path: spec.path,
    workspace_root: dirname(spec.path),
  });
  let registry = new ScriptedBackendRegistry();
  const store = new ConversationStore(database, {
    integer_now: () => 100,
    all_are_notices: all_sender_messages_are_manager_notices,
    mark_notices_leaving_queue,
    failure_notice: create_ticket_failure_notice,
    admit_notice(tx, conversation_id, sender_message_id, now) {
      const [is_manager_notice, suppression_reason] = admit_notice_batch(tx, {
        conversation_id,
        sender_message_id,
        now,
      });
      return new PromptDispatchDecision({
        sender_message_id,
        is_manager_notice,
        suppression_reason,
      });
    },
  });
  let core = new SqliteProcessConversationSystem({
    store,
    model_is_enabled: (backend, model) =>
      database.read((sql) => model_is_enabled(sql, backend, model)),
    execution_refusal: (id) =>
      database.read((sql) =>
        conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
      ),
    backend_child_factories: registry.factories,
    message_files: new ConversationMessageFiles(spec.path),
    unix_time_now: () => 100,
    monotonic_now: () => 100,
  });
  const fates = [];
  const record = (id: string) =>
    database.read((sql) => {
      const rows = (table: string, order: string) =>
        sql
          .all<Record<string, unknown>>(
            `SELECT * FROM ${table} WHERE conversation_id=? ORDER BY ${order}`,
            [id],
          )
          .map((row) => {
            for (const name of ['payload', 'identity_environment_variables', 'composer_catalog'])
              if (typeof row[name] === 'string') row[name] = JSON.parse(row[name]);
            return row;
          });
      return {
        conversation: rows('conversations', 'conversation_id')[0] ?? null,
        events: rows('conversation_events', 'sequence'),
        lifecycle: rows(
          'conversation_lifecycle_facts',
          'kind,turn_number,operation,provider_identity,status,identity',
        ),
        views: {
          state: rows('conversation_view_state', 'conversation_id'),
          rows: rows('conversation_view_rows', 'sequence'),
          turns: rows('conversation_view_turns', 'source_sequence,turn_key'),
          groups: rows('conversation_view_groups', 'source_sequence,group_key'),
          lookups: rows('conversation_view_lookups', 'lookup_kind,source_sequence,lookup_id'),
        },
      };
    });
  const resolve = () =>
    database.write((tx) =>
      accept_proposal(tx, spec.ticket, 'what_changes', {
        principal: CHIEF,
        next_holder: CHIEF,
        next_ceiling: 'needs_implementation',
        now: 4,
      }),
    );
  const start = (conversation_id: string) =>
    core.start_conversation(
      new ConversationStartRequest({
        conversation_id,
        model: 'original',
        reasoning_effort: 'low',
        workspace_folder: '/tmp',
      }),
    );
  try {
    await start('c');
    const transfer = spec.case.startsWith('transfer');
    let old_batch: WakeBatch | null = null;
    if (transfer) {
      await start('current');
      database.write((tx) => {
        tx.run(
          "INSERT INTO agents(agent_key,conversation_id) VALUES ('chief_of_staff','c') ON CONFLICT(agent_key) DO UPDATE SET conversation_id='c'",
        );
        tx.run("UPDATE agents SET conversation_id='current' WHERE agent_key=?", [
          'manager_' + spec.manager,
        ]);
      });
      old_batch = database.read(batches_waiting_for_outcome)[0];
    }
    const deliver = (batch: WakeBatch) =>
      deliver_batch(batch, {
        database,
        conversation_system: core,
        process_token: 'test',
        now: () => 10,
        assert_database_owned: () => {},
        workspace_context: { db_path: spec.path, workspace_root: dirname(spec.path) },
      });
    if (spec.case === 'rebind') {
      await core.send('c', text_message_content('Start'), { sender_label: 'Owner' });
      await registry.child('c').finish();
      await core.wait_until_quiescent();
      await registry.configure('c', 'write_prompt', {
        error: { name: 'NeedsRebind', message: 'replace' },
      });
    }
    await registry.configure('c', 'start', { barrier: 'prepare' }, 'next');
    const send = () =>
      core.send('c', text_message_content(spec.message), {
        sender_label: 'Chief',
        sender_message_id: spec.sender,
        sender: CHIEF,
        recipient: { kind: 'manager', id: spec.manager },
        ...(spec.case === 'rebind'
          ? { model_change: 'new-model', reasoning_effort_change: 'high' }
          : {}),
      });
    const task = transfer ? deliver(old_batch!) : send();
    // Observe construction, then let the scripted barrier prove start reached its pause.
    let child;
    for (;;) {
      try {
        child = registry.child('c');
        if (spec.case !== 'rebind' || (await registry.inspect('c')).length >= 2) break;
      } catch {
        /* construction has not happened */
      }
      await new Promise<void>((resolve) => setImmediate(resolve));
    }
    await child.wait_for_barrier('prepare');
    const duplicate = spec.case === 'prepare' ? send() : null;
    let current_batch = null;
    if (spec.case.startsWith('transfer')) {
      if (spec.case === 'transfer-resolved') resolve();
      database.write((tx) =>
        edit_ticket(tx, spec.ticket, {
          edit: { manager_id: spec.manager, ceiling_holder: { kind: 'manager', id: spec.manager } },
          principal: OWNER,
          title_max_chars: 200,
          now: 5,
        }),
      );
      if (spec.case === 'transfer-resolved')
        database.write((tx) =>
          file_current_proposal(tx, spec.ticket, {
            body: 'New review',
            principal: { kind: 'ticket', id: spec.ticket },
            now: 6,
          }),
        );
      current_batch = database.write((tx) =>
        claim_next_batch(tx, { process_token: 'test', now: 10 }),
      );
      if (!current_batch) throw new Error('No current review');
    } else resolve();
    await child.release('prepare');
    const result = await task;
    fates.push(
      transfer
        ? { delivered: result }
        : delivery_fate_json(result as Awaited<ReturnType<typeof send>>),
    );
    if (duplicate) {
      fates.push(delivery_fate_json(await duplicate));
      fates.push(delivery_fate_json(await send()));
    }
    if (current_batch) fates.push({ delivered: await deliver(current_batch) });
    await core.wait_until_quiescent();
    const observations = Object.fromEntries(
      await Promise.all(['c', 'current'].map(async (id) => [id, await registry.inspect(id)])),
    );
    if (spec.case === 'prepare') {
      await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
      await registry.close();
      registry = new ScriptedBackendRegistry();
      core = new SqliteProcessConversationSystem({
        store,
        model_is_enabled: (backend, model) =>
          database.read((sql) => model_is_enabled(sql, backend, model)),
        execution_refusal: (id) =>
          database.read((sql) =>
            conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
          ),
        backend_child_factories: registry.factories,
        message_files: new ConversationMessageFiles(spec.path),
        unix_time_now: () => 100,
        monotonic_now: () => 100,
      });
      fates.push(delivery_fate_json(await send()));
      if ((await registry.inspect('c')).length !== 0) throw new Error('Replay created a child');
    }

    return {
      fates,
      records: { c: record('c'), current: record('current') },
      sources: database.read((sql) =>
        Object.fromEntries(
          ['manager_wakes', 'manager_wake_batches', 'manager_wake_batch_members'].map((table) => [
            table,
            sql.all(`SELECT * FROM ${table} ORDER BY 1`),
          ]),
        ),
      ),
      backend: observations,
      running: await core.is_running('c'),
    };
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    await registry.close();
    database.close();
  }
}
