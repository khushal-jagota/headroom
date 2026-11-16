/** Real pre-parser transport boundary; route lists send already materialized JSON. */
import { Readable } from 'node:stream';
import { expect, test } from 'vitest';
import { createApp } from '../app.ts';
import { loadConfig, MAX_CONVERSATION_SEND_REQUEST_BYTES } from '../core/config.ts';
import { fixture, fixture_path } from './ticket_fixture.ts';

// Distinct gap: no TS test proved either declared or streaming overflow before JSON/dispatch.
// Reuse the normal app and Ticket DB fixture, with no listening host or scripted provider.
test('send intake rejects declared and streamed overflow before parsing or dispatch', async () => {
  const database = fixture();
  let dispatched = 0;
  const app = await createApp(
    loadConfig(undefined, { PLAN_TEST_MODE: '1', PLAN_DB_PATH: fixture_path(database) }),
    {
      database,
      areas: [
        {
          prefix: '/api/conversation',
          routes: async (scope) => {
            scope.post('/conversations/intake/send', async () => {
              dispatched += 1;
              return { ok: true };
            });
          },
        },
      ],
    },
  );
  try {
    const declared = await app.inject({
      method: 'POST',
      url: '/api/conversation/conversations/intake/send',
      payload: 'not JSON',
      headers: {
        'content-type': 'application/json',
        'content-length': String(MAX_CONVERSATION_SEND_REQUEST_BYTES + 1),
      },
    });
    expect(declared.statusCode).toBe(413);
    expect(declared.json()).toEqual({ detail: 'a conversation send request is too large' });
    const streamed = await app.inject({
      method: 'POST',
      url: '/api/conversation/conversations/intake/send',
      headers: { 'content-type': 'application/json', 'transfer-encoding': 'chunked' },
      payload: Readable.from([
        Buffer.from('{"text":"'),
        Buffer.alloc(MAX_CONVERSATION_SEND_REQUEST_BYTES, 0x61),
        Buffer.from('"}'),
      ]),
    });
    expect(streamed.statusCode).toBe(413);
    expect(streamed.json()).toEqual(declared.json());
    expect(dispatched).toBe(0);
  } finally {
    await app.close();
  }
});
