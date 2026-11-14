/** Risks unique to JS promise cancellation, service serialization, and drained evidence. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { afterEach, expect, test, vi } from 'vitest';
import {
  CodexSignInService,
  CodexSignInUnavailable,
  UnknownSignInSession,
  type SignInClient,
} from './login_session.ts';
import type { CodexServerMessageHandler } from './client.ts';
import { Deferred } from '../../runtime/state.ts';
import { StoredCodexLogin } from './login.ts';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
const now = () => '2026-11-15T10:00:00.123456+00:00';
const reading = (generation = 'before') =>
  new StoredCodexLogin({
    present: true,
    readable: true,
    account_label: 'synthetic@example.invalid',
    generation: { account_id: 'synthetic-account', last_refresh: generation },
  });
class FakeClient implements SignInClient {
  is_alive = false;
  handler: CodexServerMessageHandler;
  start_calls = 0;
  stop_calls = 0;
  constructor(handler: CodexServerMessageHandler) {
    this.handler = handler;
  }
  async start() {
    this.start_calls += 1;
    this.is_alive = true;
  }
  async request(method: string) {
    if (method === 'account/login/start')
      return {
        type: 'chatgptDeviceCode',
        loginId: 'login',
        userCode: 'SYNTHETIC',
        verificationUrl: 'https://example.invalid/device',
      };
    return {};
  }
  async notify() {}
  async stop() {
    this.stop_calls += 1;
    this.is_alive = false;
  }
  async completion(success: boolean, loginId = 'login') {
    await this.handler.on_notification('account/login/completed', {
      loginId,
      success,
      error: null,
    });
  }
}

test('old handlers and stale cancellation cannot alter the replacement session', async () => {
  const clients: FakeClient[] = [];
  const service = new CodexSignInService({
    child_processes: new ChildProcesses(),
    resolve_argv: async () => ['/synthetic/codex', 'app-server'],
    now,
    read_login: () => reading(),
    client_factory: ({ handler }) => {
      const client = new FakeClient(handler);
      clients.push(client);
      return client;
    },
  });
  try {
    const old = await service.start();
    await service.cancel(old.session_id);
    const current = await service.start();
    await clients[0]!.completion(true);
    await service.start();
    expect(service.read()).toEqual(current);
    await expect(service.cancel(old.session_id)).rejects.toBeInstanceOf(UnknownSignInSession);
    await clients[1]!.completion(true, 'another-login');
    await service.start();
    expect(service.read()?.state).toBe('pending');
  } finally {
    await service.shutdown();
  }
  await expect(service.start()).rejects.toBeInstanceOf(CodexSignInUnavailable);
  expect(clients).toHaveLength(2);
});

test('a timed out resolver cannot spawn a child after startup failure', async () => {
  const argv = new Deferred<readonly string[] | null>();
  let client!: FakeClient;
  const service = new CodexSignInService({
    child_processes: new ChildProcesses(),
    resolve_argv: () => argv.promise,
    now,
    bounds: { child_startup_seconds: 0.01 },
    read_login: () => reading(),
    client_factory: ({ handler }) => {
      client = new FakeClient(handler);
      return client;
    },
  });
  try {
    const start = service.start();
    const rejected = expect(start).rejects.toThrow();
    await rejected;
    argv.resolve(['/synthetic/codex', 'app-server']);
    await Promise.resolve();
    expect(client.start_calls).toBe(0);
    expect(client.stop_calls).toBe(1);
    expect(service.read()?.state).toBe('ended');
  } finally {
    await service.shutdown();
  }
});
