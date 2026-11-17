/**
 * Agent identity and connected conversation rows for browser tests.
 *
 * No route issues an agent launch token, so these helpers open the test server's own
 * database and use the server's token issuer, as a launched agent's start does.
 */
import { randomUUID } from 'node:crypto';
import { expect } from '@playwright/test';
import { createDatabase } from '../../../server/core/database/index.ts';
import type { Database } from '../../../server/core/database/contracts.ts';
import {
  authenticate_launch_token,
  issue_launch_token,
} from '../../../server/agent_identity/data.ts';
import type { JsonObject, ServerHandle } from './server.ts';

export interface Principal {
  kind: string;
  id: string;
}

export const OWNER: Principal = { kind: 'owner', id: 'owner' };
export const CHIEF: Principal = { kind: 'chief', id: 'chief' };
export const ticketPrincipal = (id: string): Principal => ({ kind: 'ticket', id });
export const managerPrincipal = (id: string): Principal => ({ kind: 'manager', id });

/** Read or write the test server's database from the test process. */
export function withDatabase<T>(server: ServerHandle, work: (database: Database) => T): T {
  const database = createDatabase(server.dbPath);
  try {
    return work(database);
  } finally {
    database.close();
  }
}

const tokens = new Map<string, string>();

async function post(server: ServerHandle, path: string, body: unknown, headers: Record<string, string> = {}) {
  const response = await fetch(server.base + path, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  expect(response.ok, `POST ${path} -> ${response.status}: ${text}`).toBe(true);
  return { status: response.status, body: (text ? JSON.parse(text) : {}) as JsonObject };
}

/**
 * Headers for a real agent principal: a Ticket's Worker, a Manager, or the Chief.
 * When the agent has no conversation, a fixture conversation is started and bound.
 */
export async function agentHeaders(
  server: ServerHandle,
  actor: 'worker' | 'manager' | 'chief' | string,
  identity?: string,
  options: { conversationId?: string } = {},
): Promise<{ Authorization: string }> {
  let key: string;
  let current: string | null;
  const read = (sql: string, id: string) =>
    withDatabase(server, (db) => db.read((tx) => tx.get<{ conversation_id: string | null }>(sql, [id])));
  if (actor === 'worker' && identity) {
    const row = read('SELECT conversation_id FROM tickets WHERE id=?', identity);
    if (row === undefined) return { Authorization: 'Bearer invalid-fixture-principal' };
    key = identity;
    current = row.conversation_id;
  } else if (actor === 'chief') {
    key = 'chief_of_staff';
    current = read('SELECT conversation_id FROM agents WHERE agent_key=?', key)?.conversation_id ?? null;
  } else if (actor === 'manager' && identity) {
    const exists = withDatabase(server, (db) =>
      db.read((tx) => tx.get('SELECT 1 FROM managers WHERE id=?', [identity])),
    );
    if (exists === undefined) return { Authorization: 'Bearer invalid-fixture-principal' };
    key = 'manager_' + identity;
    current = read('SELECT conversation_id FROM agents WHERE agent_key=?', key)?.conversation_id ?? null;
  } else {
    return { Authorization: 'Bearer invalid-fixture-principal' };
  }
  const conversation = options.conversationId ?? current ?? 'fixture-identity-' + randomUUID().replaceAll('-', '');
  if (current !== null && conversation !== current)
    throw new Error('An identity fixture must not replace a current conversation');
  const cacheKey = `${server.dbPath}\0${conversation}`;
  const cached = tokens.get(cacheKey);
  if (cached !== undefined) {
    // A scripted child that respawned holds a newly issued credential. A retired
    // conversation stays retired.
    if (
      current === conversation &&
      withDatabase(server, (db) => db.read((tx) => authenticate_launch_token(tx, cached))) === null
    ) {
      const reissued = withDatabase(server, (db) => issue_launch_token(db, conversation));
      tokens.set(cacheKey, reissued);
      return { Authorization: 'Bearer ' + reissued };
    }
    return { Authorization: 'Bearer ' + cached };
  }
  const started = withDatabase(server, (db) =>
    db.read((tx) => tx.get('SELECT 1 FROM conversations WHERE conversation_id=?', [conversation])),
  );
  if (started === undefined) {
    const answer = await post(server, '/api/conversation/conversations', {
      conversation_id: conversation,
      backend_key: 'codex',
      model: 'gpt-6.1-sol',
    });
    expect(answer.status).toBe(201);
  }
  const token = withDatabase(server, (db) => {
    db.write((tx) => {
      if (actor === 'worker') {
        tx.run('INSERT OR IGNORE INTO ticket_conversations(conversation_id,ticket_id) VALUES(?,?)', [
          conversation,
          key,
        ]);
        tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', [conversation, key]);
      } else {
        tx.run(
          'INSERT INTO agents(agent_key,conversation_id) VALUES(?,?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id',
          [key, conversation],
        );
      }
    });
    return issue_launch_token(db, conversation);
  });
  tokens.set(cacheKey, token);
  return { Authorization: 'Bearer ' + token };
}

/** The environment a launched agent's CLI runs with. */
export async function agentEnvironment(
  server: ServerHandle,
  actor: string,
  identity?: string,
  options: { conversationId?: string } = {},
): Promise<{ PLAN_LAUNCH_TOKEN: string }> {
  const headers = await agentHeaders(server, actor, identity, options);
  return { PLAN_LAUNCH_TOKEN: headers.Authorization.replace(/^Bearer /, '') };
}

/** Start a scripted conversation record over the public API. */
export async function startConversation(server: ServerHandle, conversationId: string): Promise<void> {
  await post(server, '/api/conversation/conversations', {
    conversation_id: conversationId,
    model: 'gpt-6.1-sol',
    backend_key: 'codex',
  });
}

/** Append a delivered message to a conversation. Test host only. */
export async function connectedMessage(
  server: ServerHandle,
  conversationId: string,
  sender: Principal,
  text: string,
): Promise<void> {
  await post(server, `/api/test/connected/${conversationId}/message`, {
    kind: sender.kind,
    id: sender.id,
    text,
  });
}

/** Bind an agent (`ticket`, `manager`, `chief`) to a conversation. Test host only. */
export async function bindConversation(
  server: ServerHandle,
  kind: string,
  identity: string,
  conversationId = '',
): Promise<void> {
  await post(server, '/api/test/connected/bind', { kind, id: identity, conversation_id: conversationId });
}

/** File a Worker proposal through the public route, as the Ticket's own Worker. */
export async function proposeAsWorker(server: ServerHandle, ticketId: string, body: string): Promise<void> {
  await post(
    server,
    `/api/tickets/${ticketId}/propose`,
    { body, recap: 'The next result is ready for review.' },
    await agentHeaders(server, 'worker', ticketId),
  );
}
