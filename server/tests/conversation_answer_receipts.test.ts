/** An answer sent with an operation ID is saved once, and a repeat gets the saved fate back. */
import { expect, test } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { BackendPermissionAsk, PermissionAskRaised } from '../conversation/backends/contracts.ts';
import { PermissionAskOption } from '../conversation/events.ts';
import { UserInputAnswer } from '../conversation/events.ts';

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
