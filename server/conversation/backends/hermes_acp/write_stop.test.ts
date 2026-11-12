import { afterEach, expect, test, vi } from 'vitest';
import { BackendPromptAccepted } from '../contracts.ts';
import {
  ConversationAccess,
  ConversationBackendKey,
  PromptDeliveryMode,
  ResolvedConversationStart,
} from '../../contracts.ts';
import { text_message_content } from '../../message_content.ts';
import type { ConversationMessageFiles } from '../../message_files.ts';
import { HermesAcpBackendChild } from './adapter.ts';
import { HermesAcpLauncher } from './client.ts';
import type { HermesAcpClient, HermesAcpHandlers } from './client.ts';
import { Deferred } from './events.ts';

afterEach(() => vi.restoreAllMocks());
test('stop after a successful prompt write cannot turn acceptance into an unwritten refusal', async () => {
  const written = new Deferred<void>();
  const entered = new Deferred<void>();
  const response = new Deferred<{ stopReason: 'cancelled' }>();
  const client = {
    signal: new AbortController().signal,
    async request(method: string) {
      if (method === 'session/new')
        return { sessionId: 'synthetic', _meta: { panels: { currentModelId: 'synthetic' } } };
      return {};
    },
    begin_request() {
      entered.resolve();
      return { written: written.promise, response: response.promise, cancel() {} };
    },
    close() {
      response.resolve({ stopReason: 'cancelled' });
    },
  };
  let update!: NonNullable<HermesAcpHandlers['session_update']>;
  vi.spyOn(HermesAcpLauncher.prototype, 'start').mockImplementation(
    async (_launch, _start, handlers) => {
      update = handlers!.session_update!;
      return client as unknown as HermesAcpClient;
    },
  );
  vi.spyOn(HermesAcpLauncher.prototype, 'stop').mockResolvedValue();
  const resolved = new ResolvedConversationStart({
    conversation_id: 'synthetic',
    backend_key: ConversationBackendKey.hermes,
    model: 'synthetic',
    workspace_folder: '/synthetic',
    access: ConversationAccess.full,
    reasoning_effort: null,
    role_materials: null,
  });
  const seen: string[] = [];
  const child = new HermesAcpBackendChild({
    launch: { argv: ['/synthetic'], environment_overrides: [] },
    resolved_start: resolved,
    event_sink: {
      async report(_turn, event) {
        seen.push(event.constructor.name);
      },
    },
    message_files: {} as ConversationMessageFiles,
  });
  try {
    await child.start(resolved, { vendor_session_cursor: null });
    const content = text_message_content('one delivery');
    const receipt = child.write_prompt({ conversation_id: 'synthetic', turn_number: 1 }, content, {
      sender_label: 'Owner',
      sender_content: content,
      mode: PromptDeliveryMode.queue,
      model_change: null,
      reasoning_effort_change: null,
    });
    await entered.promise;
    await update({
      sessionId: 'synthetic',
      update: {
        sessionUpdate: 'agent_message_chunk',
        content: { type: 'text', text: 'before actual write' },
      },
    });
    expect(seen).not.toContain('AgentMessageDelta');
    written.resolve();
    await child.stop();
    await expect(receipt).resolves.toBeInstanceOf(BackendPromptAccepted);
  } finally {
    written.resolve();
    response.resolve({ stopReason: 'cancelled' });
    await child.stop();
  }
});
