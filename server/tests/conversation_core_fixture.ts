/** Shared real-record and scripted-process fixture for the conversation core ports. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, vi } from 'vitest';
import { fixture_with_path } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { ConversationRoleMaterials, ConversationStartRequest } from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { ScriptedChild } from '../conversation/testing/scripted_child.ts';
import type { ScriptedBehavior } from '../conversation/testing/scripted_protocol.ts';
import type { ConversationStoreOptions } from '../conversation/record/contracts.ts';
import type { ConversationCoreOptions } from '../conversation/runtime/contracts.ts';

const children: ScriptedChild[] = [];
const directories: string[] = [];
afterEach(async () => {
  await Promise.all(children.splice(0).map((child) => child.terminate()));
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
  vi.restoreAllMocks();
});
export function setup(
  options: {
    behavior?: ScriptedBehavior;
    record?: ConversationStoreOptions;
    core?: Partial<ConversationCoreOptions>;
  } = {},
) {
  const { database, path: database_path } = fixture_with_path();
  const directory = mkdtempSync(join(tmpdir(), 'conversation-core-'));
  directories.push(directory);
  database.write((tx) =>
    tx.run('INSERT INTO conversation_runtime_epoch VALUES (1,?)', ['test-runtime']),
  );
  const store = new ConversationStore(database, { integer_now: () => 100, ...options.record });
  const created = new Deferred<ScriptedChild>();
  const local_children: ScriptedChild[] = [];
  const factory: ConversationCoreOptions['backend_child_factories']['codex'] = ({ event_sink }) => {
    const child = new ScriptedChild(event_sink);
    children.push(child);
    local_children.push(child);
    const configured = options.behavior
      ? child.configure('write_prompt', options.behavior)
      : Promise.resolve();
    const original = child.start.bind(child);
    child.start = async (...args) => {
      await configured;
      return original(...args);
    };
    created.resolve(child);
    return child;
  };
  const core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: { codex: factory, claude: factory, hermes: factory },
    message_files: new ConversationMessageFiles(join(directory, 'conversation.sqlite')),
    ...options.core,
  });
  const start = () =>
    core.start_conversation(
      new ConversationStartRequest({
        conversation_id: 'core',
        model: 'model',
        workspace_folder: '/workspace',
        role_materials: new ConversationRoleMaterials({ role_text: 'Role' }),
      }),
    );
  const send = (text = 'one', identity = 'sender') =>
    core.send('core', text_message_content(text), {
      sender_label: 'Owner',
      sender_message_id: identity,
    });
  return {
    core,
    store,
    database,
    database_path,
    children: local_children,
    child: created.promise,
    start,
    send,
  };
}
