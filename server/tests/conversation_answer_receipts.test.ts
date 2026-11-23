/** An answer sent with an operation ID is saved once, and a repeat gets the saved fate back. */
import { expect, test } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { BackendPermissionAsk, PermissionAskRaised } from '../conversation/backends/contracts.ts';
import { PermissionAskOption } from '../conversation/events.ts';
import { UserInputAnswer } from '../conversation/events.ts';
import { createApp } from '../app.ts';
import { loadConfig } from '../core/config.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import { createConversationRoutes } from '../conversation/routes.ts';
import type { ConversationViewRuntime } from '../conversation/views/routes.ts';
import { create } from './ticket_fixture.ts';

// Distinct gap: the answer routes were never idempotent, and no test crossed a lost response.
// Reuse the scripted-child core fixture; no server or browser is needed.
async function with_asks() {
  const f = setup();
  await f.start();
  await f.send();
  const child = await f.child;
  const raise = async (ask_id: string) => {
    await child.emit(
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id,
          title: 'Permission',
          detail: null,
          options: [
            new PermissionAskOption({
              option_id: 'allow',
              label: 'Allow',
              option_kind: 'allow_once',
            }),
          ],
        }),
      }),
    );
    await child.flush_events();
    await f.core.wait_until_quiescent();
  };
  const answer_rows = () =>
    f.store
      .read_events_after('core', 0)
      .filter((row) => row.kind === 'permission_answered' || row.kind === 'user_input_answered');
  return { f, child, raise, answer_rows };
}

test('a repeat with the same operation ID returns the saved fate, and refusals save nothing', async () => {
  const { f, raise, answer_rows } = await with_asks();
  await raise('first');
  const [ask] = f.core.runtime_turn('core')!.pending_permission_ask_ids;
  expect(await f.core.answer_permission_ask('core', ask, 'allow', 'op-1')).toBe('applied');
  // The response was lost, so the phone sends again. The ask is no longer waiting.
  expect(await f.core.answer_permission_ask('core', ask, 'allow', 'op-1')).toBe('applied');
  expect(await f.core.answer_permission_ask('core', ask, 'allow')).toBe('refused');
  expect(answer_rows()).toHaveLength(1);
  expect(f.core.answer_receipt('core', 'op-1')).toBe('applied');
  await expect(f.core.answer_permission_ask('core', ask, 'deny', 'op-1')).rejects.toThrow(
    'operation_id already names a different answer',
  );
  // An answer to an ask that is not waiting is refused, and it leaves no receipt.
  expect(await f.core.answer_permission_ask('core', 'absent', 'allow', 'op-2')).toBe('refused');
  expect(f.core.answer_receipt('core', 'op-2')).toBeNull();
  await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
});

test('a repeat that arrives while the first answer is being written gets the same fate', async () => {
  const { f, child, raise, answer_rows } = await with_asks();
  await raise('first');
  const [ask] = f.core.runtime_turn('core')!.pending_permission_ask_ids;
  await child.configure('answer_permission_ask', { barrier: 'answer' });
  const first = f.core.answer_permission_ask('core', ask, 'allow', 'op-1');
  await child.wait_for_barrier('answer');
  const repeat = f.core.answer_permission_ask('core', ask, 'allow', 'op-1');
  await child.release('answer');
  expect(await Promise.all([first, repeat])).toEqual(['applied', 'applied']);
  expect(answer_rows()).toHaveLength(1);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'answer_permission_ask'),
  ).toHaveLength(1);
  await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
});

test('a worker question repeat matches the saved answers whatever their order', async () => {
  const { f, child, answer_rows } = await with_asks();
  await child.request_questions();
  await f.core.wait_until_quiescent();
  const [request] = f.core.runtime_turn('core')!.pending_user_input_questions.keys();
  const scope = new UserInputAnswer({ question_id: 'scope', answers: ['Composer'] });
  const proof = new UserInputAnswer({ question_id: 'proof', answers: ['Browser test'] });
  expect(await f.core.answer_user_input('core', request, [scope, proof], 'op-1')).toBe('applied');
  expect(await f.core.answer_user_input('core', request, [proof, scope], 'op-1')).toBe('applied');
  expect(answer_rows()).toHaveLength(1);
  await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
});

test('a repeat with a saved receipt gets its fate after the Ticket moved to a new conversation', async () => {
  const { f, raise } = await with_asks();
  await raise('first');
  const [ask] = f.core.runtime_turn('core')!.pending_permission_ask_ids;
  // The answer routes read only the database from the record runtime.
  const record = { database: f.database, store: f.store } as unknown as ConversationViewRuntime;
  const app = await createApp(
    loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: f.database_path }),
    {
      database: f.database,
      areas: [
        {
          prefix: '/api/conversation',
          routes: createConversationRoutes({
            core: f.core,
            record,
            writes: new ConversationHttpWriteScope(),
          }),
        },
      ],
    },
  );
  const answer = (operation_id: string, option_id = 'allow') =>
    app.inject({
      method: 'POST',
      url: '/api/conversation/conversations/core/permission-answers',
      payload: { ask_id: ask, option_id, operation_id },
    });
  try {
    expect((await answer('op-1')).json()).toEqual({ fate: 'applied' });
    // A Worker restart gives the Ticket a new conversation; the phone retries against the old one.
    const ticket = create(f.database);
    f.database.write((tx) => {
      tx.run(
        `INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,created_at)
        VALUES('restarted','codex','model','/workspace','full',0)`,
      );
      tx.run("INSERT INTO ticket_conversations VALUES('core',?)", [ticket.id]);
      tx.run("UPDATE tickets SET conversation_id='restarted' WHERE id=?", [ticket.id]);
    });
    const repeat = await answer('op-1');
    expect([repeat.statusCode, repeat.json()]).toEqual([200, { fate: 'applied' }]);
    expect((await answer('op-1', 'deny')).statusCode).toBe(422);
    expect((await answer('op-2')).statusCode).toBe(409);
  } finally {
    await app.close();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});
