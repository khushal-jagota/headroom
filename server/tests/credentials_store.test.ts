import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
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
import { DatabaseCredentialResolver, FileCredentialCipher } from '../credentials/store.ts';
import { read_metadata } from '../credentials/data.ts';

const repository = resolve(import.meta.dirname, '../..');
const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function key_path(): string {
  const root = mkdtempSync(join(tmpdir(), 'panels-credential-key-'));
  roots.push(root);
  return join(root, 'private', 'key');
}
function python(db_path: string, key_path: string, script: string): void {
  execFileSync(
    join(repository, '.venv/bin/python'),
    [
      '-c',
      `
import pathlib, sqlite3, sys
from planner.credentials import actions, data
from planner.credentials.contracts import *
from planner.credentials.store import FileCredentialCipher, DatabaseCredentialResolver
from planner.core.contracts import Principal, PrincipalKind
conn = sqlite3.connect(sys.argv[1])
conn.row_factory = sqlite3.Row
key = pathlib.Path(sys.argv[2])
owner = Principal(PrincipalKind.owner, 'owner')
${script}
conn.close()
`,
      db_path,
      key_path,
    ],
    { cwd: repository, stdio: 'pipe' },
  );
}

// HTTP metadata comparisons cannot establish authenticated ciphertext compatibility.
// This single persisted database passes both ways through the actual Python store.
test('Python and TS resolve each other’s saved credentials, including legacy AAD and Unicode', () => {
  const database = fixture();
  const path = fixture_path(database);
  const key = key_path();
  python(
    path,
    key,
    `
write = CredentialWrite(CredentialReference(CredentialScheme.local, 'café 😀'), CredentialAuthKind.bearer, BearerSecret('synthetic-token'))
actions.create_credential(conn, write, principal=owner, now=100, key_path=key)
legacy = CredentialMetadata('legacy', CredentialReference(CredentialScheme.local, 'legacy'), CredentialAuthKind.header, 1, 100, 100)
aad = b'{"account":"old-account","auth_kind":"header","name":"legacy","project_id":"old-project","scheme":"local"}'
encrypted = FileCredentialCipher(key, has_records=True).encrypt(HeaderSecret('X-Demo', 'synthetic-header'), associated_data=aad)
conn.execute('INSERT INTO credentials VALUES (?,?,?,?,?,?,?,?,?,?,?)', ('legacy','local','legacy','header',1,aad,encrypted.nonce,encrypted.ciphertext,1,100,100))
conn.commit()
`,
  );
  expect(statSync(key).mode & 0o777).toBe(0o600);
  expect(statSync(dirname(key)).mode & 0o777).toBe(0o700);
  const options = { principal: OWNER, now: 101, key_path: key };
  const read = (name: string) =>
    database.read((sql) =>
      new DatabaseCredentialResolver(sql, new FileCredentialCipher(key, true)).resolve({
        scheme: 'local',
        name,
      }),
    );
  const bearer = read('café 😀').secret;
  const header = read('legacy').secret;
  expect(bearer.kind === 'bearer' && bearer.token === 'synthetic-token').toBe(true);
  expect(
    header.kind === 'header' &&
      header.header_name === 'X-Demo' &&
      header.value === 'synthetic-header',
  ).toBe(true);
  database.write((tx) =>
    replace_credential_in_transaction(
      tx,
      'legacy',
      { kind: 'header', header_name: 'X-Demo', value: 'synthetic-replaced' },
      1,
      options,
    ),
  );
  const current = read('café 😀').metadata;
  database.write((tx) => rename_credential(tx, current.id, 'renamed 😀', 1, options));
  database.write((tx) =>
    create_credential_in_transaction(
      tx,
      {
        reference: { scheme: 'local', name: 'ts-basic' },
        auth_kind: 'basic',
        secret: { kind: 'basic', username: '', password: 'synthetic-basic' },
      },
      options,
    ),
  );
  python(
    path,
    key,
    `
resolver = DatabaseCredentialResolver(conn, FileCredentialCipher(key, has_records=True))
assert resolver.resolve(CredentialReference(CredentialScheme.local, 'renamed 😀')).secret == BearerSecret('synthetic-token')
assert resolver.resolve(CredentialReference(CredentialScheme.local, 'legacy')).secret == HeaderSecret('X-Demo', 'synthetic-replaced')
assert resolver.resolve(CredentialReference(CredentialScheme.local, 'ts-basic')).secret == BasicSecret('', 'synthetic-basic')
metadata = data.find_metadata(conn, CredentialReference(CredentialScheme.local, 'ts-basic'))
actions.replace_credential(conn, metadata.id, BasicSecret('demo', 'synthetic-python-replaced'), expected_revision=1, principal=owner, now=102, key_path=key)
`,
  );
  const resolved = read('ts-basic');
  expect(resolved.metadata.revision).toBe(2);
  expect(
    resolved.secret.kind === 'basic' &&
      resolved.secret.username === 'demo' &&
      resolved.secret.password === 'synthetic-python-replaced',
  ).toBe(true);
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
