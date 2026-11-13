/** One paired consumer exchange. No database, network, or provider process. */
import { readFileSync } from 'node:fs';
import {
  ResolvedConversationStart,
  ConversationRoleMaterials,
  PromptDeliveryMode,
} from '../../conversation/contracts.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import { UserInputAnswer } from '../../conversation/events.ts';
import { as_object, decode_value } from '../protocol.ts';
import { MacRemoteChild, LateSteer } from '../vps_remote_child.ts';
import type { RemoteChildCommands } from '../vps_remote_child.ts';
import type { WireTypes, JsonObject, CommandDisposition } from '../wire.generated.ts';
const spec = JSON.parse(readFileSync(0, 'utf8'));
const decoded_start = decode_value(spec.start, 'ResolvedConversationStart');
const start = new ResolvedConversationStart({
  ...decoded_start,
  role_materials:
    decoded_start.role_materials === null
      ? null
      : new ConversationRoleMaterials(decoded_start.role_materials),
});
const snapshot = decode_value(spec.snapshot, 'CanonicalSkillSnapshot');
const child = decode_value(spec.child, 'ChildReference'),
  turn = { conversation_id: child.conversation_id, turn_number: 3 };
const sent: JsonObject[] = [],
  results: unknown[] = [],
  withdrawals: string[][] = [];
const ids = new Map<string, string>();
let present = true,
  late = false,
  disposition: CommandDisposition = 'succeeded',
  result: JsonObject = { kind: 'steer_accepted' };
let hold = false,
  release!: (value: boolean) => void,
  entered!: () => void;
let gate = Promise.resolve();
function capture(command: WireTypes['ChildCommand']): void {
  if (!ids.has(command.command_id))
    ids.set(
      command.command_id,
      command.command_id.startsWith('answer-') ? command.command_id : `minted-${ids.size}`,
    );
  sent.push({ ...as_object(command, 'ChildCommand'), command_id: ids.get(command.command_id)! });
}
const commands: RemoteChildCommands = {
  async put(command) {
    capture(command);
    if (hold) {
      entered();
      return new Promise<boolean>((resolve) => {
        release = resolve;
      });
    }
    return true;
  },
  mac_is_present: () => present,
  async deliver_steer(command) {
    capture(command);
    return late
      ? new LateSteer(Promise.resolve(true))
      : { command_id: command.command_id, child, disposition, result };
  },
  async stop(command) {
    capture(command);
    return { command_id: command.command_id, child, disposition, result };
  },
  async save_answer(command) {
    capture(command);
    return true;
  },
  async cancel_initial_delivery(_child, command_ids) {
    withdrawals.push(command_ids.map((id) => ids.get(id)!));
    return false;
  },
  retire_child(reference) {
    results.push({ detached: reference });
  },
};
async function observe(work: () => Promise<unknown>): Promise<void> {
  try {
    const value = await work();
    if (value === undefined) results.push(null);
    else if (value !== null && typeof value === 'object') {
      const record = value as Record<string, unknown>;
      results.push({
        type: value.constructor.name,
        ...record,
        ...(record.not_delivered instanceof Promise
          ? { not_delivered: await record.not_delivered }
          : {}),
      });
    } else results.push(value);
  } catch (error) {
    const caught = error as Error;
    results.push({
      error: caught.constructor.name,
      message: caught.constructor.name === 'RemoteCommandUncertain' ? '<command>' : caught.message,
    });
  }
}
const old_now = Date.now;
Date.now = () => 1700000000000;
try {
  const proxy = new MacRemoteChild(child, commands, () => snapshot);
  await observe(() => proxy.start(start, { vendor_session_cursor: 'session' }));
  const prompt = () =>
    proxy.write_prompt(turn, text_message_content('composed'), {
      sender_label: 'owner',
      sender_content: text_message_content('sender'),
      mode: PromptDeliveryMode.queue,
      model_change: null,
      reasoning_effort_change: null,
    });
  await observe(prompt);
  const adopted = new MacRemoteChild(child, commands, () => snapshot, {
    started_on: start,
    initial_history_complete: false,
  });
  await observe(() =>
    adopted.write_prompt(turn, text_message_content('other'), {
      sender_label: 'owner',
      sender_content: text_message_content('other'),
      mode: PromptDeliveryMode.queue,
      model_change: 'different',
      reasoning_effort_change: null,
    }),
  );
  const steer = () => proxy.steer(turn, text_message_content('steer'), { sender_label: 'owner' });
  present = false;
  await observe(steer);
  present = true;
  late = true;
  await observe(steer);
  late = false;
  disposition = 'uncertain';
  result = { composed_content_delivered: false };
  await observe(steer);
  disposition = 'succeeded';
  result = { kind: 'steer_refused', refusal_reason: 'backend_rejected_steer' };
  await observe(steer);
  result = { kind: 'steer_accepted', composed_content_delivered: {} };
  await observe(steer);
  result = {};
  await observe(steer);
  disposition = 'refused';
  result = { error_type: 'NeedsRebind', error_message: 'fresh child' };
  await observe(steer);
  await observe(() => proxy.answer_permission_ask('ask', 'allow'));
  await observe(() =>
    proxy.answer_permission_ask('ask', 'allow', {
      answer_id: 'answer-permission',
      turn_token: turn,
    }),
  );
  await observe(() =>
    proxy.answer_user_input(
      'request',
      [new UserInputAnswer({ question_id: 'q', answers: ['yes'] })],
      { answer_id: 'answer-input', turn_token: turn },
    ),
  );
  disposition = 'uncertain';
  await observe(() => proxy.stop());
  hold = true;
  gate = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const writing = prompt();
  await gate;
  const withdrawing = proxy.withdraw_delivery();
  release(true);
  await observe(() => writing);
  await observe(() => withdrawing);
  await observe(prompt);
  await observe(() => adopted.withdraw_delivery());
  await observe(() => proxy.detach());
} finally {
  Date.now = old_now;
}
process.stdout.write(JSON.stringify({ sent, results, withdrawals }));
