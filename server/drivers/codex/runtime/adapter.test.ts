import { parse_backend_instance_id } from '../../core/identity.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { expect, test, vi } from 'vitest';
import { JsonNumber } from '../../../core/http/numeric_json.ts';
import { ASK_WITHDRAWAL_TIMEOUT_SECONDS } from './adapter_configuration.ts';
import * as B from '../../../conversation/backends/contracts.ts';
import {
  ResolvedConversationStart,
  ConversationAccess,
  PromptDeliveryMode,
} from '../../../conversation/contracts.ts';
import { MessageImage, text_message_content } from '../../../conversation/message_content.ts';
import { MessageFileMissing } from '../../../conversation/record/message_files.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import type { CodexServerMessageHandler } from './client.ts';
import { CodexRequestRejected, CodexWireFailed } from './client.ts';
import { CodexAppServerBackendChild } from './adapter.ts';
import type { CodexClient } from './adapter.ts';
import { Deferred } from '../../../core/deferred.ts';

async function setup(message_files = {} as ConversationMessageFiles) {
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
      return { request_id, promise: Promise.resolve({}) };
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
    backend_key: parse_backend_instance_id('codex'),
    model: 'test-model',
    reasoning_effort: 'low',
    workspace_folder: '/tmp',
    access: ConversationAccess.full,
    role_materials: null,
  });
  const child = new CodexAppServerBackendChild({
    child_processes: new ChildProcesses(),
    launch: { argv: ['/never/run/codex', 'app-server'] },
    resolved_start: start,
    event_sink: { report },
    message_files,
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

// Existing post-request failure coverage does not distinguish local input failure from a send.
test('missing prompt input refuses before any native start and leaves no active turn', async () => {
  const state = await setup({
    path_of() {
      throw new MessageFileMissing('missing');
    },
  } as unknown as ConversationMessageFiles);
  try {
    const content = [new MessageImage({ stored_file_id: 'missing', media_type: 'image/png' })];
    const begin = vi.spyOn(state.client, 'begin_request');
    await expect(
      state.child.write_prompt(state.token, content, {
        sender_label: 'Owner',
        sender_content: content,
        mode: PromptDeliveryMode.queue,
        model_change: null,
        reasoning_effort_change: null,
      }),
    ).rejects.toBeInstanceOf(B.PromptWriteRefused);
    expect(begin).not.toHaveBeenCalled();
    await expect(
      state.child.steer(state.token, text_message_content('more'), { sender_label: 'Owner' }),
    ).resolves.toMatchObject({ refusal_reason: 'no_running_turn_to_steer_into' });
    expect(state.report.mock.calls.some(([, event]) => event instanceof B.TurnEnded)).toBe(false);
  } finally {
    await state.child.stop();
  }
});

// Native commands bypass ordinary input preparation, so their validation needs its own proof.
test('invalid native command input refuses before a native command starts', async () => {
  const state = await setup();
  try {
    const content = [
      ...text_message_content('/review'),
      new MessageImage({ stored_file_id: 'attachment', media_type: 'image/png' }),
    ];
    const begin = vi.spyOn(state.client, 'begin_request');
    await expect(
      state.child.write_prompt(state.token, content, {
        sender_label: 'Owner',
        sender_content: content,
        mode: PromptDeliveryMode.queue,
        model_change: null,
        reasoning_effort_change: null,
      }),
    ).rejects.toBeInstanceOf(B.PromptWriteRefused);
    expect(begin).not.toHaveBeenCalled();
    await expect(
      state.child.steer(state.token, text_message_content('more'), { sender_label: 'Owner' }),
    ).resolves.toMatchObject({ refusal_reason: 'no_running_turn_to_steer_into' });
  } finally {
    await state.child.stop();
  }
});

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

// Risk: the child exits while turn/start is pending, the wire rejects the request, and the
// failed start is reported as a turn ending or leaves a turn behind for the next prompt.
// Gap: the case above proves the rejection only through an invalid reply after the death.
test('child death while turn/start is pending fails the write through the broken wire', async () => {
  const state = await setup();
  try {
    const writing = state.prompt();
    await Promise.resolve();
    await state.handler.on_child_ended();
    state.pending
      .get(1)!
      .reject(
        new CodexWireFailed('the codex process ended', { request_may_have_been_written: true }),
      );
    await expect(writing).rejects.toBeInstanceOf(B.PromptWriteFailed);
    expect(state.report.mock.calls.some(([, event]) => event instanceof B.TurnEnded)).toBe(false);
    expect((state.child as unknown as { turn: unknown }).turn).toBeNull();
  } finally {
    await state.child.stop();
  }
});

// Risk: a child that died while idle blocks the conversation until the idle janitor runs.
// Gap: no test sends a prompt to a dead idle child. Layer: adapter unit, reusing the fake client.
test('a prompt to a child that ended while idle asks for a new child', async () => {
  const state = await setup();
  try {
    Object.assign(state.client, { is_alive: false, end_reason: 'synthetic exit' });
    await state.handler.on_child_ended();
    const begin = vi.spyOn(state.client, 'begin_request');
    const refusal = await state.prompt().catch((error: unknown) => error);
    expect(refusal).toBeInstanceOf(B.NeedsRebind);
    expect(refusal).toMatchObject({
      failed_child_recovery: true,
      message: expect.stringContaining('synthetic exit'),
    });
    expect(begin).not.toHaveBeenCalled();
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

// The client owns native cleanup, so detach must preserve the core's absolute budget.
test('detach forwards the existing stop deadline to its client', async () => {
  const state = await setup();
  const stop = vi.spyOn(state.client, 'stop');
  const options = { deadline: performance.now() / 1000 + 1 };
  try {
    await state.child.detach(options);
    expect(stop).toHaveBeenCalledWith(options);
  } finally {
    await state.child.stop();
  }
});

test('records Codex last input separately from cumulative usage', async () => {
  const state = await setup();
  try {
    const writing = state.prompt();
    await Promise.resolve();
    state.reply(1, { turn: { id: 'native', status: 'inProgress' } });
    await writing;
    await state.handler.on_notification('thread/tokenUsage/updated', {
      threadId: 'thread',
      turnId: 'native',
      tokenUsage: {
        total: { inputTokens: 90000, outputTokens: 10, cachedInputTokens: 3 },
        last: { inputTokens: 12000, outputTokens: 4, cachedInputTokens: 2 },
      },
    });
    expect(
      state.report.mock.calls.find(([, event]) => event instanceof B.TokenUsageReported)?.[1],
    ).toMatchObject({ input_tokens: 90000, context_input_tokens: 12000 });
  } finally {
    await state.child.stop();
  }
});

// Gap: string-only answers never prove that a native request carries an attachment path with readable bytes.
test('Codex receives a readable managed file reference in its structured answer and retries missing files', async () => {
  const { mkdtempSync, readFileSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { ConversationMessageFiles } =
    await import('../../../conversation/record/message_files.ts');
  const { MessageFile } = await import('../../../conversation/message_content.ts');
  const { UserInputAnswer } = await import('../../../conversation/events.ts');
  const root = mkdtempSync(join(tmpdir(), 'codex-answer-'));
  const files = new ConversationMessageFiles(join(root, 'record.sqlite'));
  const state = await setup(files);
  try {
    const writing = state.prompt();
    await Promise.resolve();
    state.reply(1, { turn: { id: 'native', status: 'inProgress' } });
    await writing;
    await state.handler.on_server_request('item/tool/requestUserInput', 7, {
      threadId: 'thread',
      turnId: 'native',
      itemId: 'question',
      questions: [
        {
          id: 'proof',
          header: 'Proof',
          question: 'What proof?',
          isOther: true,
          isSecret: false,
          options: [],
        },
      ],
    });
    const request = state.report.mock.calls.find(
      ([, event]) => event instanceof B.UserInputRequested,
    )![1] as B.UserInputRequested;
    await expect(
      state.child.answer_user_input(request.request.request_id, [
        new UserInputAnswer({
          question_id: 'proof',
          answers: [],
          attachments: [
            new MessageFile({
              stored_file_id: 'missing',
              file_name: 'missing.txt',
              media_type: 'text/plain',
              byte_count: 1,
            }),
          ],
        }),
      ]),
    ).rejects.toBeInstanceOf(B.UserInputAnswerWriteFailed);
    expect(state.client.respond).not.toHaveBeenCalled();
    const bytes = Buffer.from('native Codex answer proof');
    const kept = await files.keep('conversation', bytes, { media_type: 'text/plain' });
    await state.child.answer_user_input(request.request.request_id, [
      new UserInputAnswer({
        question_id: 'proof',
        answers: [],
        attachments: [
          new MessageFile({
            stored_file_id: kept.stored_file_id,
            file_name: 'proof.txt',
            media_type: 'text/plain',
            byte_count: bytes.length,
          }),
        ],
      }),
    ]);
    const native = vi.mocked(state.client.respond).mock.calls.at(-1)![1] as {
      answers: { proof: { answers: string[] } };
    };
    const reference = native.answers.proof.answers[0].match(/is available at (.+)\.$/)![1];
    expect(readFileSync(reference)).toEqual(bytes);
  } finally {
    await state.child.stop();
    rmSync(root, { recursive: true, force: true });
  }
});
