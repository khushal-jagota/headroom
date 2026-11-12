/** Risks unique to JS promise cancellation, service serialization, and drained evidence. */
import { afterEach, expect, test, vi } from 'vitest';
import { CodexSignInService, UnknownSignInSession, type SignInClient } from './login_session.ts';
import type { CodexServerMessageHandler } from './client.ts';
import { StoredCodexLogin, RememberedCodexLoginRejection } from './login.ts';

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
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}
class FakeClient implements SignInClient {
  is_alive = false;
  handler: CodexServerMessageHandler;
  start_calls = 0;
  stop_calls = 0;
  on_stop: (() => Promise<void>) | null = null;
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
    await this.on_stop?.();
  }
  async completion(success: boolean, loginId = 'login') {
    await this.handler.on_notification('account/login/completed', {
      loginId,
      success,
      error: null,
    });
  }
}

test('cancel drains success from child stop before the final login read and preserves microseconds', async () => {
  const memory = new RememberedCodexLoginRejection();
  memory.remember(reading().generation);
  const forget = vi.spyOn(memory, 'forget');
  const order: string[] = [];
  let generation = 'before';
  let client!: FakeClient;
  const service = new CodexSignInService({
    resolve_argv: async () => ['/synthetic/codex', 'app-server'],
    now,
    remembered_login_rejection: memory,
    read_login: () => {
      order.push(`read:${generation}`);
      return reading(generation);
    },
    client_factory: ({ handler }) => {
      client = new FakeClient(handler);
      client.on_stop = async () => {
        order.push('stop');
        generation = 'after';
        await client.completion(true);
      };
      return client;
    },
  });
  try {
    const [first, concurrent] = await Promise.all([service.start(), service.start()]);
    expect(concurrent.session_id).toBe(first.session_id);
    expect(client.start_calls).toBe(1);
    expect(first.started_at).toBe('2026-11-15T10:00:00.123456+00:00');
    expect(first.deadline_at).toBe('2026-11-15T10:10:00.123456+00:00');
    const final = await service.cancel(first.session_id);
    expect(final).toMatchObject({
      state: 'confirmed',
      detail: 'Signed in.',
      user_code: null,
      verification_url: null,
    });
    expect(order).toEqual(['read:before', 'stop', 'read:after']);
    expect(forget).not.toHaveBeenCalled();
  } finally {
    await service.shutdown();
  }
});

test('old handlers and stale cancellation cannot alter the replacement session', async () => {
  const clients: FakeClient[] = [];
  const service = new CodexSignInService({
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
});

test('a timed out resolver cannot spawn a child after startup failure', async () => {
  vi.useFakeTimers();
  const argv = deferred<readonly string[] | null>();
  let client!: FakeClient;
  const service = new CodexSignInService({
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
    await vi.advanceTimersByTimeAsync(10);
    await rejected;
    argv.resolve(['/synthetic/codex', 'app-server']);
    await vi.advanceTimersByTimeAsync(0);
    expect(client.start_calls).toBe(0);
    expect(client.stop_calls).toBe(1);
    expect(service.read()?.state).toBe('ended');
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    await service.shutdown();
  }
});
