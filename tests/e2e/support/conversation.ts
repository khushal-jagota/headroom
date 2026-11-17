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

/** Use real paging to leave the newest edge after the normal newest entry. */
export async function navigateToRetainedHistory(page: Page, newestSequence: number): Promise<void> {
  await page.evaluate(() => {
    const w = window as any;
    w.historyPagingStreams = [];
    w.historyPagingReads = 0;
    const Original = window.EventSource;
    w.EventSource = class extends Original {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        if (String(url).includes('/view/tail')) w.historyPagingStreams.push(this);
      }
    };
    const originalFetch = window.fetch;
    w.fetch = async (...args: any[]) => {
      const url = typeof args[0] === 'string' ? args[0] : args[0].url;
      const tracked = url.includes('/view/page') || url.includes('/owner-read');
      if (tracked) w.historyPagingReads++;
      try {
        return await (originalFetch as any)(...args);
      } finally {
        if (tracked) w.historyPagingReads--;
      }
    };
    w.historyPagingBounds = () => {
      const rows = [
        ...document.querySelectorAll<HTMLElement>('[data-conversation-thread] [data-conversation-view-key]'),
      ];
      const values = rows
        .map((row) => /(?:^|:)e(\d+)$/.exec(row.dataset.conversationViewKey || ''))
        .filter(Boolean)
        .map((match) => Number(match![1]));
      return { oldest: Math.min(...values), newest: Math.max(...values) };
    };
  });
  const thread = page.locator('[data-conversation-thread]');
  // The latest record can be a hidden finish event. Leave the actual retained
  // newest row, rather than mistaking that hidden sequence gap for history.
  const retainedNewest: number = await page.evaluate('window.historyPagingBounds().newest');
  const historyBefore = Math.min(newestSequence, retainedNewest);
  for (let attempt = 0; attempt < 12; attempt++) {
    const bounds: { oldest: number; newest: number } = await page.evaluate('window.historyPagingBounds()');
    if (bounds.newest < historyBefore) break;
    await thread.hover();
    await page.mouse.wheel(0, -100000);
    await page.waitForFunction(
      (oldest: number) => (window as any).historyPagingBounds().oldest < oldest,
      bounds.oldest,
      { timeout: WAIT_MS },
    );
    await page.waitForFunction(
      () => {
        const w = window as any;
        const streams = w.historyPagingStreams;
        return (
          streams.length > 0 && streams.at(-1).readyState === EventSource.OPEN && w.historyPagingReads === 0
        );
      },
      undefined,
      { timeout: WAIT_MS },
    );
  }
  expect(await page.evaluate('window.historyPagingBounds().newest')).toBeLessThan(historyBefore);
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  await page.waitForFunction('window.historyPagingReads===0', undefined, { timeout: WAIT_MS });
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
