/** Hosted Python/VPS peer. One real Mac invocation, with scripted provider bindings. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { MacService } from '../host/service.ts';
import { production_bindings } from '../host/entry.ts';
import type { MacHostBindings } from '../host/contracts.ts';
import { create_mac_adapter_host } from '../adapters.ts';
import { MacCatalog } from '../catalog/index.ts';
import { ExecutorPaths } from '../configuration.ts';
import { row_envelope } from '../storage/envelopes.ts';
import { as_object } from '../../protocol.ts';
import type { BackendEventSink, BackendChild } from '../../../conversation/backends/contracts.ts';
import {
  BackendPromptAccepted,
  BackendSteerAccepted,
  BackendPermissionAsk,
  BackendUserInputRequest,
  ToolCallStarted,
  PermissionAskRaised,
  UserInputRequested,
  TurnEnded,
} from '../../../conversation/backends/contracts.ts';
import {
  PermissionAskOption,
  UserInputQuestion,
  UserInputOption,
} from '../../../conversation/events.ts';
import type { TurnToken } from '../../../conversation/backends/contracts.ts';

const input = JSON.parse(await readFile(process.argv[2], 'utf8'));
// These values come from the parent's bound fixture, before MacService can open a root or HTTP session.
const origin = new URL(input.origin);
assert.equal(origin.protocol, 'https:');
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.origin, input.fixture_origin);
assert.equal(Number(origin.port), input.allocated_port);
assert.ok(input.allocated_port > 1023 && input.allocated_port !== 8767);
assert.equal(origin.pathname, '/');
assert.equal(origin.username + origin.password + origin.search + origin.hash, '');

const options = () => ({ deadline: performance.now() / 1000 + 20 });
const configuration = {
  paths: new ExecutorPaths(input.root),
  revision: input.revision,
  executor_id: 'test-executor',
  panels_server_url: origin.origin,
  runtime: { child: true, ready_path: null, handoff_path: null },
  http: { tls: { ca: await readFile(input.ca_path) } },
};
const concrete = production_bindings(configuration, input.environment);
let expected_input: { id: string; base64: string } | null = null;
let early_event = input.early_event === true;
const calls = {
  starts: 0,
  prompts: 0,
  stops: 0,
  cancels: 0,
  answers: [] as unknown[],
  user_answers: [] as unknown[],
};
let sink: BackendEventSink | undefined, turn: TurnToken | undefined;
const bindings: MacHostBindings = {
  ...concrete,
  processes: () => null,
  adapters(owners) {
    const host = create_mac_adapter_host({
      ...owners,
      environment: input.environment,
      panels_server_url: origin.origin,
      executable_path: () => null,
    });
    return {
      ...host,
      factory: () => (values) => {
        sink = values.event_sink;
        const unsupported = async (): Promise<never> => {
          throw new Error('Unscripted provider operation');
        };
        return {
          stop_outside_lock: false,
          withdraw_delivery: unsupported,
          detach: unsupported,
          async start() {
            calls.starts++;
          },
          async write_prompt(current) {
            if (expected_input)
              assert.equal(
                Buffer.from(
                  await values.message_files.read(current.conversation_id, expected_input.id),
                ).toString('base64'),
                expected_input.base64,
              );
            calls.prompts++;
            turn = current;
            if (early_event)
              await sink!.report(
                current,
                new ToolCallStarted({
                  tool_call_id: `early-${calls.prompts}`,
                  title: 'Before prompt returns',
                  tool_kind: 'other',
                  detail: null,
                }),
              );
            return new BackendPromptAccepted({ composed_content_delivered: true });
          },
          async steer() {
            return new BackendSteerAccepted({ composed_content_delivered: true });
          },
          async cancel_running_turn() {
            calls.cancels++;
          },
          async stop() {
            calls.stops++;
          },
          async answer_permission_ask(id, option) {
            calls.answers.push([id, option]);
            return 'applied';
          },
          async answer_user_input(id, answers) {
            calls.user_answers.push([id, answers]);
            return 'applied';
          },
        } as BackendChild;
      },
    };
  },
  catalog(_host, _owners, on_failure) {
    return new MacCatalog({
      on_failure,
      async stop_sources() {},
      snapshots: {
        retained: () => null,
        async snapshot(backend_key) {
          return {
            backend_key,
            installed: true,
            executable_path: '/scripted',
            version: '1.0.0',
            identity: null,
            available_models: [
              {
                model_id: 'scripted',
                display_name: null,
                detail: null,
                reasoning_effort_options: ['low', 'medium', 'high', 'xhigh', 'max'],
                selected_variant: null,
                version: [],
                resolved_model_id: null,
              },
            ],
            reasoning_effort_options: ['low', 'medium', 'high', 'xhigh', 'max'],
            default_model_id: 'scripted',
            default_reasoning_effort: 'medium',
            update_advisory: null,
            diagnoses: [],
            models_complete: true,
          };
        },
      },
    });
  },
};
const service = await MacService.open(configuration, bindings, options());
const serialise = (value: unknown) =>
  JSON.stringify(value, (_key, item) => (typeof item === 'bigint' ? Number(item) : item));
const write = (value: unknown) => process.stdout.write(serialise(value) + '\n');
async function state() {
  const unreported = await service.journal.unreported(1);
  let waiting = false;
  try {
    for await (const result of unreported) {
      waiting = true;
      await result.result.close();
    }
  } finally {
    await unreported.close();
  }
  return {
    identity: service.identity,
    leased: service.presence.leased(),
    admission_stopped: service.runner.admission_stopped,
    calls,
    turn,
    children: service.runner.native_children.retained_children().map((child) => ({
      reference: child.reference,
      capability: child.capability,
      ticket_id: child.ticket_id,
    })),
    pending: service.outbox
      .pending()
      .map((row) => as_object(row_envelope(service.outbox.sender_id, row), 'QueuedEnvelope')),
    unreported: waiting,
  };
}
async function emit(kind: string, id?: string) {
  assert.ok(sink && turn);
  if (kind === 'end') return sink.report(turn, new TurnEnded({ ending: 'completed' }));
  if (kind === 'tool')
    return sink.report(
      turn,
      new ToolCallStarted({
        tool_call_id: id ?? 'idle',
        title: 'While idle',
        tool_kind: 'other',
        detail: null,
      }),
    );
  if (kind === 'ask')
    return sink.report(
      turn,
      new PermissionAskRaised({
        ask: new BackendPermissionAsk({
          ask_id: 'ask_permission',
          title: 'Run the scripted action?',
          detail: null,
          options: [
            new PermissionAskOption({
              option_id: 'allow',
              label: 'Allow',
              option_kind: 'allow_once',
            }),
            new PermissionAskOption({
              option_id: 'deny',
              label: 'Deny',
              option_kind: 'reject_once',
            }),
          ],
        }),
      }),
    );
  assert.equal(kind, 'input');
  return sink.report(
    turn,
    new UserInputRequested({
      request: new BackendUserInputRequest({
        request_id: id ?? 'request_input',
        questions: [
          new UserInputQuestion({
            question_id: 'q_one',
            header: 'First',
            question: 'Choose one',
            options: [
              new UserInputOption({ label: 'Yes', description: 'Proceed' }),
              new UserInputOption({ label: 'No', description: 'Decline' }),
            ],
            multi_select: false,
            allow_other: false,
          }),
          new UserInputQuestion({
            question_id: 'q_two',
            header: 'Colors',
            question: 'Choose colors',
            options: [
              new UserInputOption({ label: 'Red', description: 'Warm' }),
              new UserInputOption({ label: 'Blue', description: 'Cool' }),
            ],
            multi_select: true,
            allow_other: false,
          }),
        ],
      }),
    }),
  );
}
const lines = createInterface({ input: process.stdin });
try {
  await service.start();
  write({ ready: true });
  for await (const line of lines) {
    const command = JSON.parse(line);
    try {
      if (command.operation === 'stop') {
        await service.stop(options());
        await service.settled();
        write({ id: command.id, result: { stopped: true, calls } });
        break;
      }
      if (command.operation === 'emit') await emit(command.kind, command.native_id);
      else if (command.operation === 'configure') {
        if ('expected_input' in command) expected_input = command.expected_input;
        if ('early_event' in command) early_event = command.early_event;
      } else assert.equal(command.operation, 'state');
      write({ id: command.id, result: await state() });
    } catch (error) {
      write({ id: command.id, error: error instanceof Error ? error.stack : String(error) });
    }
  }
} finally {
  lines.close();
  try {
    await service.stop(options());
  } finally {
    await service.settled();
  }
}
