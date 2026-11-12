import { expect, test, vi } from 'vitest';
import { JsonNumber } from '../../../core/http/numeric_json.ts';
import { ASK_WITHDRAWAL_TIMEOUT_SECONDS } from './adapter_configuration.ts';
import * as B from '../contracts.ts';
import {
  ResolvedConversationStart,
  ConversationBackendKey,
  ConversationAccess,
  PromptDeliveryMode,
} from '../../contracts.ts';
import { text_message_content } from '../../message_content.ts';
import type { ConversationMessageFiles } from '../../message_files.ts';
import type { CodexServerMessageHandler } from './client.ts';
import { CodexRequestRejected, CodexWireFailed } from './client.ts';
import { CodexAppServerBackendChild } from './adapter.ts';
import type { CodexClient } from './adapter.ts';
import { Deferred } from './turn.ts';

async function setup() {
  let handler!: CodexServerMessageHandler;
  let next = 0;
  const pending = new Map<number, Deferred<unknown>>();
  const calls: [string, unknown][] = [];
  const report = vi.fn(async (_turn: B.TurnToken | null, _event: B.BackendEventValue) => {});
  const client: CodexClient = {
    is_alive: true,
    end_reason: null,
    async start() {},
    async stop() {},
    async request(method, params) {
      calls.push([method, params]);
      if (method === 'thread/start') return { thread: { id: 'thread', ephemeral: false } };
      if (method === 'skills/list') return { data: [] };
      if (method === 'plugin/installed') return { marketplaces: [] };
      if (method === 'app/installed') return { apps: [] };
      return {};
    },
    async begin_request(method, params) {
      calls.push([method, params]);
      const request_id = ++next;
      const answer = new Deferred<unknown>();
      pending.set(request_id, answer);
      return { request_id, promise: Promise.resolve({ envelope: {}, raw_result: null }) };
    },
    async finish_request(_method, answer) {
      return pending.get(answer.request_id)!.promise;
    },
    async notify() {},
    respond: vi.fn(async () => {}),
    async respond_with_error() {},
    standard_error_tail: () => null,
    standard_error_bytes_so_far: () => 0,
    standard_error_since: () => null,
    async settle_standard_error() {},
  };
  const start = new ResolvedConversationStart({
    conversation_id: 'conversation',
    backend_key: ConversationBackendKey.codex,
    model: 'test-model',
    reasoning_effort: 'low',
    workspace_folder: '/tmp',
    access: ConversationAccess.full,
    role_materials: null,
  });
  const child = new CodexAppServerBackendChild({
    launch: { argv: ['/never/run/codex', 'app-server'] },
    resolved_start: start,
    event_sink: { report },
    message_files: {} as ConversationMessageFiles,
    client_factory(options) {
      handler = options.handler;
      return client;
    },
  });
  await child.start(start, { vendor_session_cursor: null });
  const token = { conversation_id: 'conversation', turn_number: 1 };
  const prompt = () =>
    child.write_prompt(token, text_message_content('hello'), {
      sender_label: 'Owner',
      sender_content: text_message_content('hello'),
      mode: PromptDeliveryMode.queue,
      model_change: null,
      reasoning_effort_change: null,
    });
  const reply = (id: number, result: unknown) => pending.get(id)!.resolve(result);
  return { child, client, handler, report, calls, token, prompt, reply, pending };
}

test('an invalid or failed permission answer retains the ask for another answer', async () => {
  const state = await setup();
  try {
    const writing = state.prompt();
    await Promise.resolve();
    state.reply(1, { turn: { id: 'native', status: 'inProgress' } });
    await writing;
    vi.spyOn(state.client, 'request').mockRejectedValueOnce(
      new CodexRequestRejected('turn/steer', -32000, 'rejected', {
        codexErrorInfo: { activeTurnNotSteerable: new JsonNumber('1') },
      }),
    );
    await expect(
      state.child.steer(state.token, text_message_content('more'), { sender_label: 'Owner' }),
    ).resolves.toMatchObject({ refusal_reason: 'backend_rejected_steer' });
    await state.handler.on_server_request('item/commandExecution/requestApproval', 7, {
      threadId: 'thread',
      turnId: 'native',
      itemId: 'tool',
      command: 'echo safe',
      cwd: '/tmp',
      reason: null,
    });
    const ask = state.report.mock.calls.find(
      ([, event]) => event instanceof B.PermissionAskRaised,
    )![1] as B.PermissionAskRaised;
    await expect(
      state.child.answer_permission_ask(ask.ask.ask_id, 'invented'),
    ).rejects.toBeInstanceOf(B.PermissionAnswerWriteFailed);
    vi.mocked(state.client.respond).mockRejectedValueOnce(
      new CodexWireFailed('synthetic failed answer'),
    );
    await expect(
      state.child.answer_permission_ask(ask.ask.ask_id, 'accept'),
    ).rejects.toBeInstanceOf(B.PermissionAnswerWriteFailed);
    await expect(state.child.answer_permission_ask(ask.ask.ask_id, 'decline')).resolves.toBe(
      'applied',
    );
    expect(state.client.respond).toHaveBeenLastCalledWith(7, { decision: 'decline' });
  } finally {
    await state.child.stop();
  }
});

test('child death before admission leaves ending and login diagnosis to the failed request', async () => {
  const state = await setup();
  try {
    const writing = state.prompt();
    await Promise.resolve();
    const login = vi.spyOn(state.client, 'standard_error_since');
    await state.handler.on_child_ended();
    expect(state.report.mock.calls.some(([, event]) => event instanceof B.TurnEnded)).toBe(false);
    expect(login).not.toHaveBeenCalled();
    state.reply(1, { turn: {} });
    await expect(writing).rejects.toBeInstanceOf(B.PromptWriteFailed);
  } finally {
    await state.child.stop();
  }
});

test('close fences a blocked catalog publication before it adopts a new snapshot', async () => {
  vi.useFakeTimers();
  const state = await setup();
  const entered = new Deferred<void>();
  const release = new Deferred<void>();
  try {
    await vi.advanceTimersByTimeAsync(0);
    const internal = state.child as unknown as { catalog: unknown; published_catalog: unknown };
    const before = [internal.catalog, internal.published_catalog];
    const request = state.client.request.bind(state.client);
    vi.spyOn(state.client, 'request').mockImplementation(async (method, params, options) =>
      method === 'skills/list'
        ? {
            data: [
              {
                skills: [
                  {
                    name: 'late',
                    path: '/tmp/late',
                    enabled: true,
                    description: 'Late',
                    shortDescription: null,
                    interface: null,
                  },
                ],
              },
            ],
          }
        : request(method, params, options),
    );
    state.report.mockImplementation(async (_turn, event) => {
      if (event instanceof B.ComposerCatalogReported) {
        entered.resolve();
        await release.promise;
      }
    });
    await state.handler.on_notification('skills/changed', {});
    await vi.advanceTimersByTimeAsync(50);
    await entered.promise;
    const stop = vi.spyOn(state.client, 'stop');
    await state.child.stop();
    expect(stop).toHaveBeenCalledTimes(1);
    const publications = state.report.mock.calls.length;
    release.resolve();
    await vi.advanceTimersByTimeAsync(300_000);
    expect([internal.catalog, internal.published_catalog]).toEqual(before);
    expect(state.report.mock.calls).toHaveLength(publications);
  } finally {
    release.resolve();
    await state.child.stop();
    vi.useRealTimers();
  }
});

test('shutdown reaches child cleanup when a parked ask withdrawal stalls', async () => {
  const state = await setup();
  try {
    const writing = state.prompt();
    await Promise.resolve();
    state.reply(1, { turn: { id: 'native', status: 'inProgress' } });
    await writing;
    await state.handler.on_server_request('item/commandExecution/requestApproval', 7, {
      threadId: 'thread',
      turnId: 'native',
      itemId: 'tool',
      command: 'echo safe',
      cwd: '/tmp',
      reason: null,
    });
    vi.mocked(state.client.respond).mockImplementationOnce(() => new Promise<void>(() => {}));
    const stop = vi.spyOn(state.client, 'stop');
    vi.useFakeTimers();
    const stopping = state.child.stop();
    await vi.advanceTimersByTimeAsync(ASK_WITHDRAWAL_TIMEOUT_SECONDS * 1000);
    await stopping;
    expect(state.client.respond).toHaveBeenCalledWith(7, { decision: 'decline' });
    expect(stop).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});
