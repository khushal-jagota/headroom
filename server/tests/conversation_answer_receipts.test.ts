/** An answer sent with an operation ID is saved once, and a repeat gets the saved fate back. */
import { expect, test, vi } from 'vitest';
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

test('an answer applied as its turn ends keeps its receipt and returns applied', async () => {
  const { f, child, raise, answer_rows } = await with_asks();
  await raise('first');
  const [ask] = f.core.runtime_turn('core')!.pending_permission_ask_ids;
  await child.configure('answer_permission_ask', { barrier: 'answer' });
  const first = f.core.answer_permission_ask('core', ask, 'allow', 'op-1');
  await child.wait_for_barrier('answer');
  // The turn ends after the backend took the answer and before the answer is recorded.
  await child.finish();
  await vi.waitFor(() => expect(f.core.runtime_turn('core')).toBeNull());
  await child.release('answer');
  expect(await first).toBe('applied');
  expect(f.core.answer_receipt('core', 'op-1')).toBe('applied');
  expect(await f.core.answer_permission_ask('core', ask, 'allow', 'op-1')).toBe('applied');
  expect(answer_rows()).toHaveLength(1);
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

// Gap: text-only receipts never prove uploaded bytes, per-question association, or a changed-file retry.
// Reuse the real record/core and in-process HTTP fixture. No new host or provider connection.
test('answer uploads retain readable files and their identity across retries and stored replay', async () => {
  const { f, child, answer_rows } = await with_asks();
  await child.request_questions();
  await f.core.wait_until_quiescent();
  const [request_id] = f.core.runtime_turn('core')!.pending_user_input_questions.keys();
  const app = await createApp(
    loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: f.database_path }),
    {
      database: f.database,
      areas: [
        {
          prefix: '/api/conversation',
          routes: createConversationRoutes({
            core: f.core,
            record: { database: f.database, store: f.store } as unknown as ConversationViewRuntime,
            writes: new ConversationHttpWriteScope(),
          }),
        },
      ],
    },
  );
  const send = (bytes = 'attachment evidence', reverse = false) => {
    const proof = {
      answers: [],
      attachments: [
        {
          piece: 'file',
          file_name: 'proof.txt',
          media_type: 'text/plain',
          data: Buffer.from(bytes).toString('base64'),
        },
      ],
    };
    const scope = { answers: ['Composer'] };
    return app.inject({
      method: 'POST',
      url: '/api/conversation/conversations/core/user-input-answers',
      payload: {
        request_id,
        operation_id: 'upload-answer',
        answers: reverse ? { proof, scope } : { scope, proof },
      },
    });
  };
  try {
    // Missing streamed files keep their client error and do not consume the pending ask.
    const missing = await app.inject({
      method: 'POST',
      url: '/api/conversation/conversations/core/user-input-answers',
      payload: {
        request_id,
        operation_id: 'missing-upload',
        answers: {
          scope: { answers: ['Composer'] },
          proof: {
            answers: [],
            attachments: [{ piece: 'attachment_upload', upload_id: 'missing' }],
          },
        },
      },
    });
    expect(missing.statusCode).toBe(404);
    expect((await send()).json()).toEqual({ fate: 'applied' });
    expect((await send('attachment evidence', true)).json()).toEqual({ fate: 'applied' });
    const rows = answer_rows();
    expect(rows).toHaveLength(1);
    const payload = rows[0]
      .payload as import('../conversation/events.ts').UserInputAnsweredEventPayload;
    const attachment = payload.answers.find((answer) => answer.question_id === 'proof')!
      .attachments![0];
    expect(
      Buffer.from(
        await f.core.options.message_files.read('core', attachment.stored_file_id),
      ).toString(),
    ).toBe('attachment evidence');
    const { row_body } = await import('../conversation/views/logic/bodies.ts');
    const source = f.store
      .read_events_after('core', 0)
      .find((row) => row.kind === 'user_input_requested')!.payload;
    const projected = row_body(source, {
      created_at: 100,
      settlement: payload,
    }) as import('../conversation/views/contracts.ts').UserInputRowBody;
    expect(
      projected.answers!.find((answer) => answer.question_id === 'proof')!.attachments,
    ).toEqual([
      {
        piece: 'file',
        stored_file_id: attachment.stored_file_id,
        media_type: 'text/plain',
        file_name: 'proof.txt',
        byte_count: 19,
      },
    ]);
    const received = (await child.inspect()).answers['input-1'];
    expect(
      received.find((answer) => answer.question_id === 'proof')!.attachments![0].stored_file_id,
    ).toBe(attachment.stored_file_id);
    expect((await send('changed bytes')).statusCode).toBe(422);
    expect(
      (await child.inspect()).calls.filter((call) => call.command === 'answer_user_input'),
    ).toHaveLength(1);
  } finally {
    await app.close();
    await f.core.shutdown({ deadline: performance.now() / 1000 + 5 });
  }
});

// Gap: attachment-only free text and exact-choice questions need different admission rules.
test('an attachment-only free-text answer is valid, while a closed-choice answer cannot carry files', async () => {
  const { ordered_user_input_answers } = await import('../conversation/logic/user_input.ts');
  const { UserInputQuestion, UserInputOption } = await import('../conversation/events.ts');
  const { MessageFile } = await import('../conversation/message_content.ts');
  const question = new UserInputQuestion({
    question_id: 'proof',
    header: 'Proof',
    question: 'Which proof?',
    options: [new UserInputOption({ label: 'Browser', description: 'Browser proof' })],
    multi_select: false,
    allow_other: true,
  });
  const attachment = new MessageFile({
    stored_file_id: 'f_proof',
    file_name: 'proof.txt',
    media_type: 'text/plain',
    byte_count: 3,
  });
  const answer = new UserInputAnswer({
    question_id: 'proof',
    answers: [],
    attachments: [attachment],
  });
  expect(ordered_user_input_answers([question], [answer])).toEqual([answer]);
  const closed = new UserInputQuestion({ ...question, allow_other: false });
  expect(
    ordered_user_input_answers(
      [closed],
      [new UserInputAnswer({ ...answer, answers: ['Browser'] })],
    ),
  ).toBeNull();
});
