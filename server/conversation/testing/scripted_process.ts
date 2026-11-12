/** One deterministic scripted agent. Neither adapter owns its behavior. */
import { createInterface } from 'node:readline';
import type {
  ScriptedBehavior,
  ScriptedCall,
  ScriptedEvent,
  ScriptedFrame,
  ScriptedFailure,
  ScriptedRequest,
} from './scripted_protocol.ts';
import type { TurnToken } from '../backends/contracts.ts';

let token: TurnToken | null = null;
const calls: ScriptedCall[] = [];
const delivered_text: string[] = [];
const answers: Record<string, unknown> = {};
const behaviors = new Map<string, ScriptedBehavior[]>();
const barriers = new Map<string, { reached: boolean; release: () => void; wait: Promise<void> }>();
const barrier_observers = new Map<string, (() => void)[]>();
function send(frame: ScriptedFrame): void {
  process.stdout.write(JSON.stringify(frame) + '\n');
}
function emit(event: ScriptedEvent, target: TurnToken | null = token): void {
  send({ event, turn: target });
}
function barrier(name: string) {
  let value = barriers.get(name);
  if (!value) {
    let release = () => {};
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    value = { reached: false, release, wait };
    barriers.set(name, value);
  }
  return value;
}
async function run(request: ScriptedRequest): Promise<unknown> {
  const args = request.arguments;
  if (request.command === 'configure') {
    const command = args.command as string;
    const queued = behaviors.get(command) ?? [];
    queued.push(args.behavior as ScriptedBehavior);
    behaviors.set(command, queued);
    return null;
  }
  if (request.command === 'wait_for_barrier') {
    const name = args.name as string;
    if (!barrier(name).reached) {
      await new Promise<void>((resolve) => {
        const observers = barrier_observers.get(name) ?? [];
        observers.push(resolve);
        barrier_observers.set(name, observers);
      });
    }
    return null;
  }
  if (request.command === 'release') {
    barrier(args.name as string).release();
    return null;
  }
  if (request.command === 'inspect') {
    return {
      token,
      calls,
      delivered_text,
      answers,
      barriers: [...barriers].filter(([, value]) => value.reached).map(([name]) => name),
    };
  }
  if (request.command === 'emit') {
    emit(
      args.event as ScriptedEvent,
      args.turn === undefined ? token : (args.turn as TurnToken | null),
    );
    return null;
  }
  if (request.command === 'questions') {
    if (token === null) {
      throw new Error('a scripted turn must exist before questions');
    }
    emit({
      kind: 'UserInputRequested',
      values: {
        request: {
          request_id: 'input-1',
          questions: [
            {
              question_id: 'scope',
              header: 'Scope',
              question: 'Which surfaces should change?',
              options: [
                { label: 'Composer', description: 'The live composer' },
                { label: 'Transcript', description: 'Durable history' },
              ],
              multi_select: true,
              allow_other: true,
            },
            {
              question_id: 'proof',
              header: 'Proof',
              question: 'What proof should be required?',
              options: [{ label: 'Browser test', description: 'Exercise the full flow' }],
              multi_select: false,
              allow_other: true,
            },
          ],
        },
      },
    });
    return null;
  }
  if (request.command === 'finish') {
    if (token === null) {
      throw new Error('a scripted turn must exist before finish');
    }
    emit({
      kind: 'TurnEnded',
      values: {
        ending: args.ending ?? 'completed',
        error_summary: args.error_summary ?? null,
        standard_error_tail: null,
      },
    });
    token = null;
    return null;
  }
  calls.push({ command: request.command, arguments: args });
  const behavior = behaviors.get(request.command)?.shift();
  if (request.command === 'write_prompt') {
    const possible_emission =
      behavior?.error?.name === 'PromptWriteFailed' ||
      behavior?.error?.name === 'RemoteCommandUncertain';
    if (!behavior?.error || possible_emission) {
      token = args.turn_token as TurnToken;
      const pieces = args.content as { piece: string; text?: string }[];
      delivered_text.push(
        pieces
          .filter((piece) => piece.piece === 'text')
          .map((piece) => piece.text)
          .join('\n'),
      );
    }
  }
  for (const item of behavior?.events ?? []) {
    emit(item.event, item.turn === undefined ? token : item.turn);
  }
  if (behavior?.barrier) {
    const current = barrier(behavior.barrier);
    current.reached = true;
    for (const notify of barrier_observers.get(behavior.barrier) ?? []) {
      notify();
    }
    barrier_observers.delete(behavior.barrier);
    await current.wait;
  }
  if (behavior?.error) {
    const error = new Error(behavior.error.message) as Error & { failed_child_recovery?: boolean };
    error.name = behavior.error.name;
    error.failed_child_recovery = behavior.error.failed_child_recovery;
    throw error;
  }
  switch (request.command) {
    case 'start':
      return null;
    case 'write_prompt':
      return behavior?.result ?? { composed_content_delivered: true };
    case 'steer':
      return behavior?.result ?? { fate: 'accepted', composed_content_delivered: true };
    case 'answer_user_input':
      answers[args.request_id as string] = args.answers;
      return behavior?.result ?? 'applied';
    case 'answer_permission_ask':
      return behavior?.result ?? 'applied';
    case 'cancel_running_turn':
    case 'stop':
    case 'detach':
      token = null;
      return null;
    case 'withdraw_delivery':
      return behavior?.result ?? true;
    default:
      throw new Error(`unknown scripted command ${request.command}`);
  }
}

const input = createInterface({ input: process.stdin });
input.on('line', (line) => {
  let request: ScriptedRequest;
  try {
    request = JSON.parse(line) as ScriptedRequest;
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
    input.close();
    return;
  }
  // A blocked receipt must not block its independent release control.
  void run(request).then(
    (result) => send({ id: request.id, result }),
    (error: Error & ScriptedFailure) =>
      send({
        id: request.id,
        error: {
          name: error.name,
          message: error.message,
          failed_child_recovery: error.failed_child_recovery,
        },
      }),
  );
});
input.on('close', () => {
  for (const current of barriers.values()) {
    current.release();
  }
});
