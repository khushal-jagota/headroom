/** Existing record tests cover neither server authority persistence nor pre-authentication fencing. */
import { expect, test } from 'vitest';
import Fastify from 'fastify';
import { fixture, fixture_path } from '../tests/ticket_fixture.ts';
import { createDatabase } from '../core/database/index.ts';
import { initialize_server_location, read_server_location, set_server_address } from './data.ts';
import { install_server_link } from './http.ts';
import { MOVE_NUMBER_HEADER, SERVER_ID_HEADER, SERVER_LOCATION_HEADER } from './contracts.ts';

test('server identity survives a database reopen', () => {
  const database = fixture();
  const original = initialize_server_location(database);
  const reopened = createDatabase(fixture_path(database));
  try {
    expect(initialize_server_location(reopened)).toEqual(original);
  } finally {
    reopened.close();
  }
});

test('server address check refuses HTTP, failed probes and other identities without a record change', async () => {
  const database = fixture();
  const location = initialize_server_location(database);
  await expect(
    set_server_address(database, 'http://server.example', async () => location),
  ).rejects.toThrow(/HTTPS/);
  await expect(
    set_server_address(database, 'https://server.example', async () => {
      throw new Error('unreachable');
    }),
  ).rejects.toThrow(/unreachable/);
  await expect(
    set_server_address(database, 'https://server.example', async () => ({
      ...location,
      server_id: 'other',
    })),
  ).rejects.toThrow(/current active server/);
  expect(database.read(read_server_location)).toEqual(location);
  expect(
    await set_server_address(database, 'https://server.example/', async () => location),
  ).toEqual({ ...location, server_url: 'https://server.example' });
});

test('authority fences machine requests before authentication and body effects', async () => {
  const database = fixture();
  const location = initialize_server_location(database);
  const app = Fastify();
  let effects = 0;
  install_server_link(app, () => database.read(read_server_location)!);
  app.addHook('onRequest', async () => {
    effects++;
  });
  app.post('/api/executor/items', async () => {
    effects++;
    return { accepted: true };
  });
  try {
    for (const headers of [
      { [SERVER_ID_HEADER]: location.server_id, [MOVE_NUMBER_HEADER]: '1' },
      { [SERVER_ID_HEADER]: 'other', [MOVE_NUMBER_HEADER]: '0' },
      { [SERVER_ID_HEADER]: location.server_id },
    ]) {
      const reply = await app.inject({
        method: 'POST',
        url: '/api/executor/items',
        headers,
        payload: 'not JSON',
      });
      expect(reply.statusCode).toBe(409);
      expect(
        JSON.parse(
          Buffer.from(reply.headers[SERVER_LOCATION_HEADER] as string, 'base64url').toString(),
        ),
      ).toEqual(location);
    }
    expect(effects).toBe(0);
    expect(
      (
        await app.inject({
          method: 'POST',
          url: '/api/executor/items',
          headers: { [SERVER_ID_HEADER]: location.server_id, [MOVE_NUMBER_HEADER]: '0' },
        })
      ).statusCode,
    ).toBe(200);
    expect(effects).toBe(2);
  } finally {
    await app.close();
  }
});
