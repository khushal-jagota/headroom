import Fastify from 'fastify';
import { expect, test } from 'vitest';
import { requestOrigin } from '../core/http/origin.ts';
// A blanket trustProxy switch also trusts X-Forwarded-Host, unlike Python.
// Injection proves peer and header selection without another network host.
test('public origins trust only the default proxy peer and keep the actual Host', async () => {
  const app = Fastify();
  app.get('/', (request) => requestOrigin(request));
  const headers = {
    host: 'panels.example',
    'x-forwarded-proto': 'https',
    'x-forwarded-host': 'wrong.example',
  };
  try {
    expect((await app.inject({ url: '/', headers, remoteAddress: '127.0.0.1' })).body).toBe(
      'https://panels.example',
    );
    expect((await app.inject({ url: '/', headers, remoteAddress: '198.51.100.1' })).body).toBe(
      'http://panels.example',
    );
    expect(
      (
        await app.inject({
          url: '/',
          headers: { ...headers, 'x-forwarded-proto': 'invalid' },
          remoteAddress: '127.0.0.1',
        })
      ).body,
    ).toBe('http://panels.example');
  } finally {
    await app.close();
  }
});
