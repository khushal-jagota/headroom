import { parse_backend_instance_id } from '../core/identity.ts';
import { ChildProcesses, OneShotChildren } from '../../lifecycle/owned_processes.ts';
import { afterEach, expect, test, vi } from 'vitest';
import {
  BackendPromptAccepted,
  PromptWriteRefused,
} from '../../conversation/backends/contracts.ts';
import {
  ConversationAccess,
  PromptDeliveryMode,
  ResolvedConversationStart,
} from '../../conversation/contracts.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import type { ConversationMessageFiles } from '../../conversation/message_files.ts';
import { AcpBackendChild } from './child.ts';
import { AcpLauncher } from './client.ts';
import type { AcpClient, AcpHandlers } from './client.ts';
import { Deferred } from '../../core/deferred.ts';
import { uncontained_worker_supervision } from '../testing/uncontained_supervision.ts';

afterEach(() => vi.restoreAllMocks());
/** A child on a fake client. `write_started` says whether the prompt's stream write began. */
function synthetic_child(write_started: () => boolean) {
  const written = new Deferred<void>();
  const entered = new Deferred<void>();
  const response = new Deferred<{ stopReason: 'cancelled' }>();
  const client = {
    signal: new AbortController().signal,
    async request(method: string) {
      if (method === 'session/new')
        return {
          sessionId: 'synthetic',
          configOptions: [
            {
              id: 'model',
              name: 'Model',
              category: 'model',
              type: 'select',
              currentValue: 'x',
              options: [],
            },
          ],
        };
      return {};
    },
    begin_request() {
      entered.resolve();
      return { written: written.promise, response: response.promise, cancel() {}, write_started };
    },
    close() {
      response.resolve({ stopReason: 'cancelled' });
    },
  };
  let update!: NonNullable<AcpHandlers['session_update']>;
  vi.spyOn(AcpLauncher.prototype, 'start').mockImplementation(async (_launch, _start, handlers) => {
    update = handlers!.session_update!;
    return client as unknown as AcpClient;
  });
  const stop = vi.spyOn(AcpLauncher.prototype, 'stop').mockResolvedValue();
  const resolved = new ResolvedConversationStart({
    conversation_id: 'synthetic',
    backend_key: parse_backend_instance_id('antigravity'),
    model: 'synthetic',
    workspace_folder: '/synthetic',
    access: ConversationAccess.full,
    reasoning_effort: null,
    role_materials: null,
  });
  const seen: string[] = [];
  const child_processes = new ChildProcesses();
  const child = new AcpBackendChild(
    { label: 'Synthetic', launch: () => ({ argv: ['/synthetic'], environment_overrides: [] }) },
    {
      child_processes,
      worker_supervision: uncontained_worker_supervision(new OneShotChildren(child_processes)),
    },
    {
      resolved_start: resolved,
      event_sink: {
        async report(_turn, event) {
          seen.push(event.constructor.name);
        },
      },
      message_files: {} as ConversationMessageFiles,
    },
  );
  const write = () => {
    const content = text_message_content('one delivery');
    return child.write_prompt({ conversation_id: 'synthetic', turn_number: 1 }, content, {
      sender_label: 'Owner',
      sender_content: content,
      mode: PromptDeliveryMode.queue,
      model_change: null,
      reasoning_effort_change: null,
    });
  };
  return { child, resolved, written, entered, response, stop, seen, write, update: () => update };
}
test('stop after a successful prompt write cannot turn acceptance into an unwritten refusal', async () => {
  const { child, resolved, written, entered, response, stop, seen, write, update } =
    synthetic_child(() => true);
  try {
    await child.start(resolved, { vendor_session_cursor: null });
    const receipt = write();
    await entered.promise;
    await update()({
      sessionId: 'synthetic',
      update: {
        sessionUpdate: 'agent_message_chunk',
        content: { type: 'text', text: 'before actual write' },
      },
    });
    expect(seen).not.toContain('AgentMessageDelta');
    written.resolve();
    const options = { deadline: performance.now() / 1000 + 1 };
    await child.detach(options);
    expect(stop).toHaveBeenCalledWith(options);
    await expect(receipt).resolves.toBeInstanceOf(BackendPromptAccepted);
  } finally {
    written.resolve();
    response.resolve({ stopReason: 'cancelled' });
    await child.stop();
  }
});
// Risk: a stop that ends a prompt still queued behind the wire reads as possibly delivered, when
// nothing reached the agent.
test('stop before the prompt write began refuses the prompt', async () => {
  const { child, resolved, written, entered, response, write } = synthetic_child(() => false);
  try {
    await child.start(resolved, { vendor_session_cursor: null });
    const receipt = write();
    await entered.promise;
    await child.stop();
    written.reject(new Error('ACP connection closed'));
    await expect(receipt).rejects.toBeInstanceOf(PromptWriteRefused);
  } finally {
    response.resolve({ stopReason: 'cancelled' });
  }
});
