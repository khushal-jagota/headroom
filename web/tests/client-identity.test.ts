import { afterEach, expect, it, vi } from 'vitest';

// A reload must report the same device. An external artifact must not receive that ID.
it('retains browser identity across module reloads and keeps it on Panels requests', async () => {
  const values = new Map<string, string>();
  // Plain HTTP exposes getRandomValues, but not the secure-context randomUUID API.
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
  vi.stubGlobal('location', { href: 'http://panels.example/', origin: 'http://panels.example' });
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value) });
  vi.stubGlobal('document', { querySelector: () => ({ content: 'boot-sha' }) });
  const request = vi.fn<typeof fetch>(async () => new Response('{}'));
  vi.stubGlobal('fetch', request);
  const first = await import('../src/lib/clientIdentity');
  await first.clientFetch('/api/meta', { headers: { 'X-Panels-Client': 'renderer override' } });
  const identity = JSON.parse((request.mock.calls[0][1]?.headers as Record<string, string>)['X-Panels-Client']);
  expect(identity).toMatchObject({ kind: 'browser', version: 'boot-sha', device_id: expect.any(String) });
  vi.resetModules();
  const second = await import('../src/lib/clientIdentity');
  await second.clientFetch('/files/tickets/note.md');
  expect(JSON.parse((request.mock.calls[1][1]?.headers as Record<string, string>)['X-Panels-Client'])).toEqual(identity);
  await second.clientFetch('https://external.example/note.md');
  expect(request.mock.calls[2][1]?.headers).toBeUndefined();
});

afterEach(() => vi.unstubAllGlobals());
