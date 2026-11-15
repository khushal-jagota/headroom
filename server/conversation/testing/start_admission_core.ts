/** Core call-site proof: the existing scripted child supplies native steer receipts. */
import { createDatabase } from '../../core/database/index.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { delivery_fate_json } from '../http.ts';
import { text_message_content } from '../message_content.ts';
import { ScriptedBackendRegistry } from './scripted_registry.ts';

export async function run(path: string, outcome: 'accepted' | 'uncertain'): Promise<unknown> {
  let now = 0;
  let available = false;
  const saving = Promise.withResolvers<void>();
  const database = createDatabase(path);
  const store = new ConversationStore(database, { integer_now: () => 100 });
  database.write((tx) => store.reconcile_in_transaction(tx, new Map()));
  const registry = new ScriptedBackendRegistry();
  const core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: registry.factories,
    message_files: new ConversationMessageFiles(path),
    execution_refusal: () => null,
    delivery_model_refusal: () => null,
    unix_time_now: () => 100,
    monotonic_now: () => now,
  });
  const checkpoints: unknown[] = [];
  const grants: boolean[] = [];
  const fates: unknown[] = [];
  const capture = async (name: string) => {
    checkpoints.push({
      name,
      snapshots: [...core.start_admission.snapshots()].sort((a, b) =>
        a.request.identity.localeCompare(b.request.identity),
      ),
      rows: database.read((sql) =>
        sql.all('SELECT * FROM automatic_start_bindings ORDER BY identity'),
      ),
      receipts: database
        .read((sql) =>
          sql.all<{ sequence: number; kind: string; payload: string }>(
            "SELECT sequence,kind,payload FROM conversation_events WHERE conversation_id='c' AND kind IN ('prompt','prompt_delivery_uncertain','prompt_delivery_refused') ORDER BY sequence",
          ),
        )
        .map((row) => ({ ...row, payload: JSON.parse(row.payload) })),
      delivered: (await registry.child('c').inspect()).delivered_text,
    });
  };
  const send = (identity: string, mode: 'queue' | 'steer') =>
    core.send('c', text_message_content(identity), {
      sender_label: 'loop',
      sender_message_id: identity,
      mode,
    });
  const original = store.append_events.bind(store);
  store.append_events = (...args) => {
    if (
      args[1].some(
        (payload) => 'sender_message_id' in payload && payload.sender_message_id === 'followup',
      ) &&
      !available
    ) {
      saving.resolve();
      throw new Error('receipt save held');
    }
    return original(...args);
  };
  try {
    grants.push(core.start_admission.try_start(await core.automatic_start_request('initial', 'c')));
    fates.push(delivery_fate_json(await send('initial', 'queue')));
    await capture('initial');
    now = 1;
    grants.push(
      core.start_admission.try_start(await core.automatic_start_request('followup', 'c')),
    );
    const child = registry.child('c');
    await child.configure('steer', {
      barrier: 'steer-receipt',
      result: { fate: outcome, composed_content_delivered: true },
    });
    const steering = send('followup', 'steer');
    await child.wait_for_barrier('steer-receipt');
    await capture('wire-pending');
    await child.release('steer-receipt');
    await saving.promise;
    await capture('save-pending');
    available = true;
    fates.push(delivery_fate_json(await steering));
    await capture('settled');
    return { grants, fates, checkpoints };
  } finally {
    available = true;
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    await registry.close();
    database.close();
  }
}
