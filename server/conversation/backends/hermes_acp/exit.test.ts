import { expect, test } from 'vitest';
import {
  ConversationAccess,
  ConversationBackendKey,
  ResolvedConversationStart,
} from '../../contracts.ts';
import { HermesAcpLauncher } from './client.ts';

// A real child exit and a buffered reply are separate OS facts. Memory streams
// cannot prove that the launcher preserves the latter after the former.
test('request timeout and cancellation keep the same child until its ordered final exit', async () => {
  const launcher = new HermesAcpLauncher();
  const order: string[] = [];
  const program = `
    const readline = require('node:readline');
    readline.createInterface({ input: process.stdin }).on('line', line => {
      const request = JSON.parse(line);
      if (request.method === 'pending' || request.method === '$/cancel_request') return;
      if (request.method === 'alive') {
        process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result:{pid:process.pid}})+'\\n');
        return;
      }
      const update = { jsonrpc: '2.0', method: 'session/update', params: {
        sessionId: String(process.pid), update: {
          sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'last text' }
        }
      } };
      const reply = { jsonrpc: '2.0', id: request.id, result: { stopReason: 'end_turn' } };
      process.stdout.write(JSON.stringify(update) + '\\n' + JSON.stringify(reply) + '\\n',
        () => process.exit(0));
    });
  `;
  const home = process.env.HOME!; // vitest supplies a private temporary home.
  const resolved = new ResolvedConversationStart({
    conversation_id: 'unit16-fast-exit',
    backend_key: ConversationBackendKey.hermes,
    model: 'synthetic',
    reasoning_effort: null,
    role_materials: null,
    workspace_folder: home,
    access: ConversationAccess.full,
  });
  try {
    const client = await launcher.start(
      { argv: [process.execPath, '-e', program], environment_overrides: [] },
      resolved,
      {
        async session_update(params) {
          order.push('notification started');
          const pid = Number(params.sessionId);
          expect(pid).toBeGreaterThan(0);
          await new Promise((resolve) => setTimeout(resolve, 20));
          await expect
            .poll(
              () => {
                try {
                  process.kill(pid, 0);
                  return false;
                } catch (error) {
                  if ((error as NodeJS.ErrnoException).code === 'ESRCH') return true;
                  throw error;
                }
              },
              { timeout: 1000 },
            )
            .toBe(true);
          order.push('child exited before notification finished');
        },
      },
    );
    const before = await client.request<{ pid: number }>('alive', {});
    const timed = client.begin_request('pending', {}, { timeout_ms: 5 });
    await expect(timed.response).rejects.toBeInstanceOf(Error);
    const cancelled = client.begin_request('pending', {});
    const rejected = expect(cancelled.response).rejects.toBeInstanceOf(Error);
    await cancelled.written;
    cancelled.cancel();
    await rejected;
    const after = await client.request<{ pid: number }>('alive', {});
    expect(after.pid).toBe(before.pid);
    expect(client.signal.aborted).toBe(false);
    process.kill(after.pid, 0); // Only the synthetic child that this launcher owns.
    const prompt = client.begin_request('session/prompt', { sessionId: 'synthetic', prompt: [] });
    await prompt.written;
    await expect(prompt.response).resolves.toMatchObject({ stopReason: 'end_turn' });
    order.push('reply');
    expect(order).toEqual([
      'notification started',
      'child exited before notification finished',
      'reply',
    ]);
  } finally {
    await launcher.stop();
  }
});
