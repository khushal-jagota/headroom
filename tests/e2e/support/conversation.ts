/**
 * Conversation helpers shared by the conversation signal, Ticket conversation and video
 * upload browser tests.
 */
import { expect, type BrowserContext, type Page } from '@playwright/test';
import { cli } from './api.ts';
import { bindConversation } from './agents.ts';
import { WAIT_MS, type JsonObject, type ServerHandle } from './server.ts';
import { openPage } from './fixtures.ts';

/** POST JSON (an empty object by default) and require a 2xx answer. */
export async function post(server: ServerHandle, path: string, body: unknown = {}): Promise<JsonObject> {
  const response = await fetch(server.base + path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  const text = await response.text();
  expect(response.ok, text).toBe(true);
  return JSON.parse(text) as JsonObject;
}

/** GET and decode JSON without a status check, as a bare `httpx.get(...).json()` does. */
export async function getJson(server: ServerHandle, path: string): Promise<any> {
  const response = await fetch(server.base + path);
  return response.json();
}

/** Create a Ticket with a settled Brief, start a scripted conversation and bind it. */
export async function createTicketConversation(
  server: ServerHandle,
  options: { conversationId: string; title: string },
): Promise<string> {
  const ticketId: string = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: options.title,
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id;
  await post(server, `/api/test/signals/${options.conversationId}/start`);
  await bindConversation(server, 'ticket', ticketId, options.conversationId);
  return ticketId;
}

export async function openTicketConversation(
  server: ServerHandle,
  ticketId: string,
  context: BrowserContext,
  readySelector: string,
): Promise<Page> {
  const page = await openPage(
    context,
    server,
    `#/workspace/${ticketId}`,
    `[data-screen="ticket"][data-ticket-id="${ticketId}"]`,
  );
  await openConversationLayer(page);
  await page.waitForSelector(readySelector, { timeout: WAIT_MS });
  return page;
}

export async function openConversationLayer(page: Page): Promise<void> {
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  await page.locator('[data-conversation-state="opened"]').waitFor({ timeout: WAIT_MS });
}
