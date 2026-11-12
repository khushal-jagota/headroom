import { describe, expect, it } from 'vitest';
import {
  ConversationStartRequest,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
} from '../conversation/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import {
  InMemoryConversationSystem,
  TurnCannotEndWhilePermissionAskIsPending,
  TurnCannotEndWhileUserInputIsPending,
} from '../conversation/in_memory_conversation_system.ts';
import { text_message_content } from '../conversation/message_content.ts';

async function started(): Promise<InMemoryConversationSystem> {
  const system = new InMemoryConversationSystem();
  const contract: ConversationSystem = system;
  await contract.start_conversation(
    new ConversationStartRequest({
      conversation_id: 'example',
      model: 'model',
      reasoning_effort: 'high',
    }),
  );
  return system;
}
const words = text_message_content;

describe('in-process boundaries absent from the four comparison endpoints', () => {
  // HTTP cannot carry an admission callback or retain an operation across an await.
  it('retains active admission through await and closes only after a successful claim', async () => {
    const system = await started();
    let release!: () => void;
    const pending = system.workspace_admission.operation(async (admitted) => {
      expect(admitted).toBe(true);
      await new Promise<void>((resolve) => {
        release = resolve;
      });
    });
    expect(system.workspace_activity()).toBe('busy');
    let claims = 0;
    expect(
      await system.run_with_workspace_admission(
        () => {
          claims += 1;
        },
        { require_idle: true },
      ),
    ).toBe(false);
    release();
    await pending;
    expect(system.workspace_activity()).toBe('idle');
    const lease = system.workspace_admission.operation();
    expect(lease.admitted).toBe(true);
    expect(system.workspace_activity()).toBe('busy');
    lease.release();
    lease.release();
    expect(system.workspace_activity()).toBe('idle');
    await expect(
      system.run_with_workspace_admission(
        () => {
          throw new Error('claim failed');
        },
        { require_idle: true },
      ),
    ).rejects.toThrow('claim failed');
    expect(system.workspace_activity()).toBe('idle');
    expect(
      await system.run_with_workspace_admission(
        () => {
          claims += 1;
        },
        { require_idle: true },
      ),
    ).toBe(true);
    expect(claims).toBe(1);
    expect(system.workspace_activity()).toBe('unknown');
    await expect(
      system.send('example', words('message'), { sender_label: 'owner' }),
    ).rejects.toThrow('Workspace admission is closed for an owner update.');
    system.workspace_admission.reopen();
    expect(system.workspace_activity()).toBe('idle');
  });

  // Test-only ask controls do not belong in the four-endpoint comparison surface.
  it('keeps asks on the live turn and applies only complete valid question answers', async () => {
    const system = await started();
    await system.send('example', words('message'), { sender_label: 'owner' });
    const ask = system.raise_permission_ask('example');
    expect(await system.has_pending_permission_ask('example')).toBe(true);
    expect(() => system.complete_running_turn('example')).toThrow(
      TurnCannotEndWhilePermissionAskIsPending,
    );
    expect(system.answer_permission_ask('example', ask, 'allow')).toBe('applied');
    expect(system.answer_permission_ask('example', ask, 'allow')).toBe('refused');
    const request = system.raise_user_input('example', [
      {
        question_id: 'question',
        header: 'Choice',
        question: 'Which?',
        options: [{ label: 'A', description: '' }],
        multi_select: false,
        allow_other: false,
      },
    ]);
    expect(() => system.complete_running_turn('example')).toThrow(
      TurnCannotEndWhileUserInputIsPending,
    );
    expect(
      system.answer_user_input('example', request, [{ question_id: 'question', answers: ['B'] }]),
    ).toBe('refused');
    expect(await system.has_pending_user_input('example')).toBe(true);
    expect(
      system.answer_user_input('example', request, [{ question_id: 'question', answers: ['A'] }]),
    ).toBe('applied');
    expect(system.backend_user_input_answers('example', request)).toEqual([
      { question_id: 'question', answers: ['A'] },
    ]);
    const late = system.raise_permission_ask('example');
    await system.kill('example');
    expect(system.answer_permission_ask('example', late, 'allow')).toBe('refused');
    expect(await system.has_pending_permission_ask('example')).toBe(false);
  });

  // The host has no failure-injection route; draining must record failed held messages.
  it('records held write failures without applying their deferred model changes', async () => {
    const system = await started();
    await system.send('example', words('first'), { sender_label: 'owner' });
    await system.send('example', words('next'), {
      sender_label: 'owner',
      model_change: 'other',
      sender_message_id: 'next',
    });
    system.arm_backend_write_failure('example');
    system.complete_running_turn('example');
    expect(await system.is_running('example')).toBe(false);
    expect(await system.held_prompts('example')).toEqual([]);
    expect(system.backend_model('example')).toBe('model');
    expect(system.observations('example').at(-1)).toMatchObject({
      kind: 'prompt_delivery_refused',
      refusal_reason: 'write_to_backend_failed',
      sender_message_id: 'next',
    });
    expect(
      await system.send('example', words('next'), {
        sender_label: 'owner',
        model_change: 'other',
        sender_message_id: 'next',
      }),
    ).toEqual(new PromptDeliveryRefused({ refusal_reason: 'write_to_backend_failed' }));
    const unstarted = new InMemoryConversationSystem();
    await unstarted.start_conversation(
      new ConversationStartRequest({ conversation_id: 'spawn', model: 'model' }),
    );
    unstarted.arm_backend_start_failure('spawn');
    expect(await unstarted.send('spawn', words('message'), { sender_label: 'owner' })).toEqual(
      new PromptDeliveryRefused({ refusal_reason: 'backend_did_not_start' }),
    );
  });

  // Promotion/discard are contract operations without HTTP routes in this instrument.
  it('keeps deferred changes held until promotion and preserves discarded sender identity', async () => {
    const system = await started();
    await system.send('example', words('first'), { sender_label: 'owner' });
    await system.send('example', words('change'), {
      sender_label: 'owner',
      reasoning_effort_is_set: true,
      reasoning_effort_change: null,
      sender_message_id: 'change',
    });
    const [held] = await system.held_prompts('example');
    expect(await system.promote_held_prompt('example', held.held_prompt_id, 'steer')).toEqual(
      new PromptDeliveryQueued({ queue_position: 1 }),
    );
    expect(system.backend_reasoning_effort('example')).toBe('high');
    await system.promote_held_prompt('example', held.held_prompt_id, 'send_now');
    expect(system.backend_reasoning_effort('example')).toBe(null);
    expect(system.backend_cancellations('example')).toBe(1);
    await system.send('example', words('discard'), {
      sender_label: 'owner',
      sender_message_id: 'discard',
    });
    const [discarded] = await system.held_prompts('example');
    expect(await system.discard_held_prompt('example', discarded.held_prompt_id)).toBe(true);
    expect(
      await system.send('example', words('discard'), {
        sender_label: 'owner',
        sender_message_id: 'discard',
      }),
    ).toEqual(new PromptDeliverySuppressed({ reason: 'discarded' }));
  });
});
