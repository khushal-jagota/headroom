/** The process boundary must preserve receipt order, typed failures, and process loss. */
import { performance } from 'node:perf_hooks';
import { expect, test } from 'vitest';
import { ScriptedChild } from './scripted_child.ts';
import {
  AgentMessageCompleted,
  BackendPromptRejected,
  NeedsRebind,
  PromptWriteFailed,
  PromptWriteRefused,
  TurnEnded,
  UserInputRequested,
} from '../backends/contracts.ts';
import type { BackendEventValue } from '../backends/contracts.ts';
import { ConversationRoleMaterials, ResolvedConversationStart } from '../contracts.ts';
import {
  MessageFile,
  MessageText,
  message_content_json_entries,
  text_message_content,
} from '../message_content.ts';
import { UserInputAnswer } from '../events.ts';

const start = new ResolvedConversationStart({
  conversation_id: 'scripted-test',
  backend_key: 'codex',
  model: 'scripted-model',
  reasoning_effort: null,
  role_materials: new ConversationRoleMaterials({
    role_text: 'Scripted role',
    identity_environment_variables: [['PLAN_ACTOR', 'worker']],
  }),
  workspace_folder: '/synthetic/workspace',
  access: 'full',
});
const turn = { conversation_id: start.conversation_id, turn_number: 1 };
const options = {
  sender_label: 'Owner',
  sender_content: text_message_content('sender'),
  mode: 'queue' as const,
  model_change: null,
  reasoning_effort_change: null,
};

test('early news cannot block receipt parsing and complete inputs cross the shared process', async () => {
  const began = performance.now();
  const events: BackendEventValue[] = [];
  let release_report = () => {};
  const held_report = new Promise<void>((resolve) => {
    release_report = resolve;
  });
  const child = new ScriptedChild({
    report: async (_turn, event) => {
      events.push(event);
      if (event instanceof TurnEnded) {
        await held_report;
      }
    },
  });
  try {
    await child.start(start, { vendor_session_cursor: 'resumed-session' });
    await child.configure('write_prompt', {
      barrier: 'receipt',
      events: [{ event: { kind: 'TurnEnded', values: { ending: 'completed' } } }],
    });
    const content = [
      new MessageText({ text: 'first' }),
      new MessageFile({
        stored_file_id: 'file-id',
        media_type: 'text/plain',
        file_name: 'proof.txt',
        byte_count: 5,
      }),
      new MessageText({ text: 'last' }),
    ];
    const pending = child.write_prompt(turn, content, options);
    await child.wait_for_barrier('receipt');
    expect(events[0]).toBeInstanceOf(TurnEnded);
    await child.release('receipt');
    // The report is still held. Waiting for it while parsing would deadlock this receipt.
    expect(await pending).toMatchObject({ composed_content_delivered: true });
    release_report();
    await child.flush_events();
    const snapshot = await child.inspect();
    expect(snapshot.calls[0]).toEqual({
      command: 'start',
      arguments: { resolved_start: start, vendor_session_cursor: 'resumed-session' },
    });
    expect(snapshot.calls[1].arguments.content).toEqual(
      message_content_json_entries(content).content,
    );
    expect(snapshot.delivered_text).toEqual(['first\nlast']);
    await child.emit(new AgentMessageCompleted({ content: text_message_content('agent prose') }));
    await child.request_questions();
    const question = events.find((event) => event instanceof UserInputRequested);
    expect(question?.request.questions.map((item) => item.question_id)).toEqual(['scope', 'proof']);
    await child.answer_user_input('input-1', [
      new UserInputAnswer({ question_id: 'scope', answers: ['Composer'] }),
    ]);
    expect((await child.inspect()).answers).toEqual({
      'input-1': [{ question_id: 'scope', answers: ['Composer'] }],
    });
    expect(events.find((event) => event instanceof AgentMessageCompleted)?.content).toEqual(
      text_message_content('agent prose'),
    );
    await child.finish();
    expect((await child.inspect()).token).toBeNull();
  } finally {
    release_report();
    await child.terminate();
    console.info(
      `shared scripted process connected proof: ${Math.round(performance.now() - began)} ms`,
    );
  }
}, 10_000);

test('transport preserves definite refusal, possible emission, and rebind identities without a retry', async () => {
  const child = new ScriptedChild({ report: async () => {} });
  try {
    await child.start(start, { vendor_session_cursor: null });
    for (const ErrorClass of [
      BackendPromptRejected,
      PromptWriteRefused,
      PromptWriteFailed,
      NeedsRebind,
    ]) {
      await child.configure('write_prompt', {
        error: {
          name: ErrorClass.name,
          message: 'exact native detail',
          failed_child_recovery: true,
        },
      });
      const result = child.write_prompt(turn, text_message_content('sender'), options);
      await expect(result).rejects.toBeInstanceOf(ErrorClass);
      await expect(result).rejects.toThrow('exact native detail');
      if (ErrorClass === BackendPromptRejected || ErrorClass === PromptWriteRefused) {
        expect((await child.inspect()).token).toBeNull();
      }
      if (ErrorClass === NeedsRebind) {
        await expect(result).rejects.toMatchObject({ failed_child_recovery: true });
      }
    }
    const snapshot = await child.inspect();
    expect(snapshot.calls.filter((call) => call.command === 'write_prompt')).toHaveLength(4);
    expect(snapshot.delivered_text).toEqual(['sender']);
    await child.stop();
  } finally {
    await child.terminate();
  }
}, 10_000);

test('process loss rejects an outstanding receipt instead of leaving the core exchange pending', async () => {
  const child = new ScriptedChild({ report: async () => {} });
  try {
    await child.configure('write_prompt', { barrier: 'never-replied' });
    const pending = child.write_prompt(turn, text_message_content('sender'), options);
    const rejection = expect(pending).rejects.toThrow('scripted child exited');
    await child.wait_for_barrier('never-replied');
    await child.terminate();
    await rejection;
  } finally {
    await child.terminate();
  }
}, 10_000);
