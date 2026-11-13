/** Config already covers boolean spellings. This proves that our I/O doors use its result. */
import { expect, test, vi } from 'vitest';

const recording = vi.hoisted(() => ({
  spawn: vi.fn(() => {
    throw new Error('Unexpected recorded subprocess call');
  }),
  request: vi.fn((..._arguments: unknown[]) => {
    throw new Error('Unexpected recorded HTTP call');
  }),
  proxyAgent: vi.fn(function (_proxy: unknown, _options: unknown) {
    return { destroy: vi.fn() };
  }),
}));
vi.mock('node:child_process', async (original) => ({
  ...(await original<typeof import('node:child_process')>()),
  spawn: recording.spawn,
}));
vi.mock('node:http', async (original) => ({
  ...(await original<typeof import('node:http')>()),
  request: recording.request,
}));
vi.mock('node:https', async (original) => ({
  ...(await original<typeof import('node:https')>()),
  request: recording.request,
}));
vi.mock('https-proxy-agent', () => ({ HttpsProxyAgent: recording.proxyAgent }));

import { SubprocessBackendProbeEnvironment } from '../conversation/snapshot/environment.ts';
import { http_get_usage } from '../conversation/backend_usage/http.ts';
import { trim_surrounding_silence } from '../conversation/voice_transcription/production.ts';
import { post_voice_http } from '../conversation/voice_transcription/http.ts';
import { fetchRates } from '../conversation/usage/http.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';

// Gap: ordinary Vitest/Pytest markers previously hid the PLAN_TEST_MODE=true guard mismatch.
test('parsed test mode blocks production subprocess and HTTP doors without runner markers', async () => {
  vi.stubEnv('VITEST', undefined);
  vi.stubEnv('PYTEST_CURRENT_TEST', undefined);
  vi.stubEnv('PLAN_TEST_MODE', '0');
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    throw new Error('Unexpected recorded fetch call');
  });
  const children = new OneShotChildren();
  const environment = new SubprocessBackendProbeEnvironment(children);
  vi.stubEnv('PLAN_TEST_MODE', 'true');
  try {
    expect(() => new SubprocessBackendProbeEnvironment(children)).toThrow(
      'Production backend I/O is unavailable in tests',
    );
    await expect(environment.run(['recorded-provider'], { timeout_seconds: 1 })).rejects.toThrow(
      'Production backend I/O is unavailable in tests',
    );
    await expect(
      environment.latest_released_version('recorded-package', { timeout_seconds: 1 }),
    ).rejects.toThrow('Production backend I/O is unavailable in tests');
    await expect(http_get_usage('https://recorded.invalid/usage', {}, 1)).rejects.toThrow(
      'Production backend I/O is unavailable in tests',
    );
    await expect(trim_surrounding_silence(new Uint8Array([1]))).rejects.toThrow(
      'Production voice I/O is unavailable in tests',
    );
    await expect(
      post_voice_http({
        url: 'https://recorded.invalid/transcription',
        headers: {},
        data: {},
        timeout_seconds: 1,
        file: { filename: 'recorded.ogg', contents: new Uint8Array([1]), media_type: 'audio/ogg' },
      }),
    ).rejects.toThrow('Production voice I/O is unavailable in tests');
    await expect(fetchRates()).rejects.toThrow(
      'Production usage rates I/O is unavailable in tests',
    );
    expect(recording.spawn).not.toHaveBeenCalled();
    expect(recording.request).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  } finally {
    await environment.shutdown();
    vi.unstubAllEnvs();
    fetch.mockRestore();
  }
});

// Gap: the request signal alone cannot cancel a proxy socket during a pending CONNECT.
test('the proxy CONNECT agent receives the same abort signal as the rates request', async () => {
  vi.stubEnv('VITEST', undefined);
  vi.stubEnv('PYTEST_CURRENT_TEST', undefined);
  vi.stubEnv('PLAN_TEST_MODE', '0');
  vi.stubEnv('https_proxy', 'http://recorded-proxy.invalid:8080');
  vi.stubEnv('no_proxy', '');
  vi.stubEnv('NO_PROXY', undefined);
  const controller = new AbortController();
  const reason = new Error('Recorded CONNECT cancellation');
  let requestSignal: AbortSignal | undefined;
  recording.request.mockImplementationOnce((...arguments_: unknown[]) => {
    requestSignal = (arguments_[1] as { signal: AbortSignal }).signal;
    controller.abort(reason);
    throw reason;
  });
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    throw new Error('Unexpected recorded fetch call');
  });
  try {
    await expect(fetchRates({ signal: controller.signal })).rejects.toBe(reason);
    expect(recording.proxyAgent).toHaveBeenCalledTimes(1);
    const options = recording.proxyAgent.mock.calls[0]![1] as { signal: AbortSignal };
    expect(options.signal).toBe(requestSignal);
    expect(options.signal.aborted).toBe(true);
    expect(options.signal.reason).toBe(reason);
    expect(recording.proxyAgent.mock.results[0]!.value.destroy).toHaveBeenCalledTimes(1);
    expect(recording.spawn).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllEnvs();
    fetch.mockRestore();
    vi.clearAllMocks();
  }
});
