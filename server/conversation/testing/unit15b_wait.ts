/** Observe the real core's save retry through the stalls registry. */
import { createDatabase } from '../../core/database/index.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { ProcessWaitRegistry } from '../../stalls/registry.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { ConversationStartRequest } from '../contracts.ts';
import { text_message_content } from '../message_content.ts';
import { ScriptedChild } from './scripted_child.ts';
import { BackendStateStore } from '../backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../../tickets/execution_admission.ts';

export async function run_wait(database_path: string): Promise<unknown> {
  const database = createDatabase(database_path);
  let armed = false;
  let attempts = 0;
  const registry = new ProcessWaitRegistry(() => 100000);
  const store = new ConversationStore(database, {
    integer_now: () => 100,
    transaction_participant: () => {
      if (armed && ++attempts <= 2) throw new Error('unit15b save fault');
    },
  });
  const children: ScriptedChild[] = [];
  const factory = ({
    event_sink,
  }: Parameters<import('../backends/contracts.ts').BackendChildFactory>[0]) => {
    const child = new ScriptedChild(event_sink);
    children.push(child);
    return child;
  };
  const core = new SqliteProcessConversationSystem({
    store,
    delivery_model_refusal: (backend, model, ticket_id) =>
      new BackendStateStore(database).delivery_model_refusal(backend, model, ticket_id),
    execution_refusal: (id) =>
      database.read((sql) =>
        conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
      ),
    message_files: new ConversationMessageFiles(database_path),
    backend_child_factories: { codex: factory, claude: factory, hermes: factory },
    unix_time_now: () => 100,
    wait_registry: registry,
  });
  const checkpoints: unknown[] = [];
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
  const capture = (name: string) =>
    checkpoints.push({
      name,
      waits: registry.snapshot(),
      rows: database.read((sql) =>
        Object.fromEntries(
          tables.map((table) => [
            table,
            sql.all(
              `SELECT * FROM ${table} ORDER BY ${table === 'conversation_lifecycle_facts' ? 'conversation_id,turn_number,kind,operation,identity' : table === 'conversation_view_lookups' ? 'conversation_id,lookup_kind,source_sequence,lookup_id' : '1,2'}`,
            ),
          ]),
        ),
      ),
    });
  try {
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: 'wait-proof',
        model: 'a-model',
        workspace_folder: '/tmp',
      }),
    );
    await core.send('wait-proof', text_message_content('work'), {
      sender_label: 'Owner',
      sender_message_id: 'wait-work',
    });
    armed = true;
    const finish = children[0]!.finish();
    for (const failure of [1, 2]) {
      const deadline = Date.now() + 5000;
      while (registry.snapshot()[0]?.detail !== `Stuck at write ${failure}`) {
        if (Date.now() > deadline) throw new Error('Retry wait not visible');
        await new Promise((resolve) => setTimeout(resolve, 2));
      }
      capture(`failure-${failure}`);
    }
    await finish;
    await core.wait_until_quiescent();
    capture('saved');
    return { checkpoints, attempts };
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    for (const child of children) await child.terminate();
    await store.finish_writes();
    database.close();
  }
}
