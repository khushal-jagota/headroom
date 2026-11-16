import { mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import Fastify from 'fastify';
import { loadConfig } from '../core/config.ts';
import { buildClock } from '../core/clock.ts';
import { registerArea } from '../core/authority/register.ts';
import { routes } from '../credentials/routes.ts';
import { PlannerError } from '../core/contracts.ts';
import { OWNER } from '../core/contracts.ts';
import { fixture, fixture_path } from './ticket_fixture.ts';
import {
  create_credential_in_transaction,
  rename_credential,
  replace_credential_in_transaction,
} from '../credentials/actions.ts';
import { FileCredentialCipher } from '../credentials/store.ts';
import { read_metadata } from '../credentials/data.ts';
import {
  associated_data,
  encode_secret,
  validate_encryption_context,
} from '../credentials/logic/validation.ts';
import type { CredentialMetadata, CredentialSecret } from '../credentials/contracts.ts';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function key_path(): string {
  const root = mkdtempSync(join(tmpdir(), 'panels-credential-key-'));
  roots.push(root);
  return join(root, 'private', 'key');
}
// A committed Python oracle checks the cross-language cipher format without a live
// Python implementation dependency in the server suite. Live writes run in pytest.
test('Python-produced cipher vectors pin exact associated data and decrypt with a synthetic key', () => {
  const oracle = JSON.parse(
    readFileSync(join(import.meta.dirname, '../credentials/fixtures/python-cipher.json'), 'utf8'),
  ) as {
    key_hex: string;
    credentials: Array<{
      metadata: CredentialMetadata;
      version: number;
      associated_data_hex: string;
      canonical_associated_data_hex: string;
      nonce_hex: string;
      ciphertext_hex: string;
      secret: CredentialSecret;
    }>;
  };
  const key = key_path();
  mkdirSync(dirname(key), { mode: 0o700 });
  writeFileSync(key, Buffer.from(oracle.key_hex, 'hex'), { mode: 0o600 });
  const cipher = new FileCredentialCipher(key, true);
  for (const vector of oracle.credentials) {
    const context = Buffer.from(vector.associated_data_hex, 'hex');
    expect(
      associated_data(vector.metadata).equals(
        Buffer.from(vector.canonical_associated_data_hex, 'hex'),
      ),
    ).toBe(true);
    validate_encryption_context(vector.metadata, {
      version: vector.version,
      associated_data: context,
    });
    const recovered = cipher.decrypt(
      {
        nonce: Buffer.from(vector.nonce_hex, 'hex'),
        ciphertext: Buffer.from(vector.ciphertext_hex, 'hex'),
      },
      context,
    );
    expect(encode_secret(recovered).equals(encode_secret(vector.secret))).toBe(true);
  }
});

// A wrong key must not silently replace existing encrypted values or reset a store.
test('wrong keys and stale revisions leave ciphertext and metadata unchanged', () => {
  const database = fixture();
  const key = key_path();
  const options = { principal: OWNER, now: 100, key_path: key };
  const metadata = database.write((tx) =>
    create_credential_in_transaction(
      tx,
      {
        reference: { scheme: 'local', name: 'saved' },
        auth_kind: 'bearer',
        secret: { kind: 'bearer', token: 'synthetic-token' },
      },
      options,
    ),
  );
  expect(statSync(key).mode & 0o777).toBe(0o600);
  expect(statSync(dirname(key)).mode & 0o777).toBe(0o700);
  const original = database.read((sql) => sql.all('SELECT * FROM credentials'));
  const originalKey = readFileSync(key);
  writeFileSync(key, Buffer.alloc(32, 7));
  expect(() =>
    database.write((tx) =>
      replace_credential_in_transaction(
        tx,
        metadata.id,
        { kind: 'bearer', token: 'replacement' },
        1,
        options,
      ),
    ),
  ).toThrow('credential could not be decrypted');
  expect(() =>
    database.write((tx) =>
      create_credential_in_transaction(
        tx,
        {
          reference: { scheme: 'local', name: 'second' },
          auth_kind: 'bearer',
          secret: { kind: 'bearer', token: 'second' },
        },
        options,
      ),
    ),
  ).toThrow('credential could not be decrypted');
  writeFileSync(key, originalKey);
  expect(() =>
    database.write((tx) => rename_credential(tx, metadata.id, 'changed', 2, options)),
  ).toThrow('credential changed');
  expect(database.read((sql) => sql.all('SELECT * FROM credentials'))).toEqual(original);
  expect(database.read((sql) => read_metadata(sql, metadata.id)).revision).toBe(1);
});

// JSON comparison lists cannot send malformed raw UTF-8 or prove parser error privacy.
// Fastify injection reuses the existing fixture; no browser or network host is needed.
test('private HTTP parsing rejects malformed bytes without secret reflection and checks owner first', async () => {
  const database = fixture();
  const key = key_path();
  const config = loadConfig(join(dirname(key), 'absent.yaml'), {
    PLAN_DB_PATH: fixture_path(database),
    PLAN_CREDENTIAL_KEY_PATH: key,
    PLAN_TEST_MODE: '1',
    PLAN_FAKE_NOW: '2026-11-11T10:00:00+00:00',
  });
  const app = Fastify();
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof PlannerError) reply.code(error.statusCode).send(error.toPayload());
    else reply.send(error);
  });
  registerArea(app, { prefix: '/api', routes }, { database, config, clock: buildClock(config) });
  try {
    const invalidBytes = Buffer.concat([
      Buffer.from('{"scheme":"local","name":"invalid","auth_kind":"bearer","secret":{"token":"'),
      Buffer.from([0xff]),
      Buffer.from('"}}'),
    ]);
    const invalid = await app.inject({
      method: 'POST',
      url: '/api/credentials',
      headers: { 'content-type': 'application/json' },
      payload: invalidBytes,
    });
    expect(invalid.statusCode).toBe(400);
    expect(invalid.json()).toEqual({
      error: { code: 'validation', message: 'invalid credential input', detail: {} },
    });
    const malformed = await app.inject({
      method: 'POST',
      url: '/api/credentials',
      headers: { 'content-type': 'application/json' },
      payload: '{"secret":"synthetic-private-malformed',
    });
    expect(malformed.statusCode).toBe(400);
    expect(malformed.body.includes('synthetic-private-malformed')).toBe(false);
    const chief = await app.inject({
      method: 'POST',
      url: '/api/credentials',
      headers: { 'content-type': 'application/json', 'x-plan-actor': 'chief' },
      payload: 'not-json',
    });
    expect(chief.json()).toEqual({
      error: {
        code: 'agent_forbidden',
        message: 'only the owner can submit credential secrets',
        detail: {},
      },
    });
    const plaintext = await app.inject({
      method: 'POST',
      url: '/api/credentials',
      headers: { 'content-type': 'text/plain' },
      payload: JSON.stringify({
        scheme: 'local',
        name: 'plain content type',
        auth_kind: 'bearer',
        secret: { token: 'synthetic-private-valid' },
      }),
    });
    expect(plaintext.statusCode).toBe(200);
    expect(plaintext.body.includes('synthetic-private-valid')).toBe(false);
  } finally {
    await app.close();
  }
});
