/**
 * Browser proof that open screens follow the server, and that composing survives it.
 *
 * The server says only "something changed"; the browser answers by refetching what it is
 * showing. These two tests hold that behaviour to what a person actually experiences: a
 * change made elsewhere lands on the open screen without a reload, and a change that lands
 * while someone is typing leaves the typing — the caret, the text, and the place on the
 * page — exactly where it was.
 *
 * Waits are on the browser's own counters (`window.__plannerDebug`) plus the resulting
 * DOM, never on a sleep.
 */
import type { Page, WebSocketRoute } from '@playwright/test';
import { WAIT_MS, api, cli, expect, openPage, test, type ServerHandle } from './support/fixtures.ts';

const RECAP_SEED = 'Recap as it stood before anyone started typing.';
const FIRST_HALF = ' Composed the first half,';
const SECOND_HALF = ' and the second half after the change.';

/** Page-side state these tests keep on `window`. */
type TestWindow = Window & {
  __composer: { editor: HTMLElement; scroller: Element };
  __plannerDebug: { flushes: number };
  __documentMark?: string;
};

// Find the surface the composer scrolls inside, bring the composer to its top, and keep
// both for later: the same editor node must still hold the caret at the end, and its
// surface must still be sitting at the same place. The page header sits tight above the
// recap, so centring the recap would not scroll; bringing it to the top always does.
const CAPTURE_COMPOSER = (selector: string): number => {
  const editor = document.querySelector(selector) as HTMLElement;
  editor.scrollIntoView({ block: 'start' });
  let node = editor.parentElement;
  while (node) {
    const style = getComputedStyle(node);
    const scrolls = style.overflowY === 'auto' || style.overflowY === 'scroll';
    if (scrolls && node.scrollHeight > node.clientHeight + 1) break;
    node = node.parentElement;
  }
  const scroller = node || document.scrollingElement!;
  (window as unknown as TestWindow).__composer = { editor, scroller };
  return scroller.scrollTop;
};

// Focus without scrolling (focus() would otherwise move the page), then put the caret at
// the end of what is already written — where a person would carry on.
const FOCUS_AT_END = (): void => {
  const editor = (window as unknown as TestWindow).__composer.editor;
  editor.focus({ preventScroll: true });
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false);
  const selection = getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
};

async function setRecap(server: ServerHandle, ticketId: string, recap: string): Promise<void> {
  await api.patch(server, `/api/tickets/${ticketId}`, { recap });
}

async function flushes(page: Page): Promise<number> {
  return Number(await page.evaluate(() => (window as unknown as TestWindow).__plannerDebug.flushes));
}

async function waitForFlush(page: Page, previous: number): Promise<void> {
  await page.waitForFunction(
    (previous) => (window as unknown as TestWindow).__plannerDebug.flushes > previous,
    previous,
    { timeout: WAIT_MS },
  );
}

/** A value that only survives while this document does — a reload wipes it. */
async function markPage(page: Page): Promise<void> {
  await page.evaluate(() => {
    (window as unknown as TestWindow).__documentMark = 'same document';
  });
}

async function assertSameDocument(page: Page): Promise<void> {
  expect(await page.evaluate(() => (window as unknown as TestWindow).__documentMark)).toBe('same document');
}

test('test_composing_survives_a_change_to_the_same_ticket', async ({ server, newContext }) => {
  const ticketId = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: 'Composer ticket before the change',
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id as string;
  await setRecap(server, ticketId, RECAP_SEED);

  const page = await openPage(
    await newContext(),
    server,
    `#/workspace/${ticketId}`,
    `section[data-screen="ticket"][data-ticket-id="${ticketId}"]`,
  );
  // A short window makes the ticket's own column scroll, so "the page did not jump"
  // is a claim with something behind it.
  await page.setViewportSize({ width: 1280, height: 400 });

  const composer = '[data-recap] [data-markdown-inline-edit]';
  await page.locator(composer).waitFor({ state: 'visible', timeout: WAIT_MS });
  const scrollTop = await page.evaluate(CAPTURE_COMPOSER, composer);
  expect(scrollTop, String(scrollTop)).toBeGreaterThan(0);

  await page.evaluate(FOCUS_AT_END);
  await page.keyboard.type(FIRST_HALF);
  await page.waitForFunction(
    (text) => (window as unknown as TestWindow).__composer.editor.textContent!.includes(text),
    FIRST_HALF.trim(),
    { timeout: WAIT_MS },
  );

  const before = await flushes(page);
  await markPage(page);
  await api.patch(server, `/api/tickets/${ticketId}`, { title: 'Composer ticket after the change' });

  // The change reaches this very screen mid-composition: the title on the page behind
  // the composer is rewritten from the server's answer.
  await waitForFlush(page, before);
  await page
    .locator('.ticket-title', { hasText: 'Composer ticket after the change' })
    .waitFor({ state: 'visible', timeout: WAIT_MS });

  await page.keyboard.type(SECOND_HALF);

  const state = await page.evaluate(() => {
    const { editor, scroller } = (window as unknown as TestWindow).__composer;
    return {
      focused: document.activeElement === editor,
      stillAttached: editor.isConnected,
      text: editor.textContent,
      scrollTop: scroller.scrollTop,
    };
  });
  expect(state.stillAttached).toBe(true);
  expect(state.focused).toBe(true);
  expect(state.text).toBe(RECAP_SEED + FIRST_HALF + SECOND_HALF);
  expect(state.scrollTop).toBe(scrollTop);
  await assertSameDocument(page);
});

// Distinct risk: the page's one socket decides "Can't reach Panels". Only a real socket, held open by a
// real server and cut by the browser's own route, shows the card at once and shows the screen
// follow the server again after the page reopens the socket.
test('test_a_dropped_live_socket_shows_the_card_at_once_and_the_screen_follows_again', async ({ server, newContext }) => {
  const ticketId = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: 'Socket drop before',
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id as string;
  const context = await newContext();
  const sockets: WebSocketRoute[] = [];
  await context.routeWebSocket(/\/api\/live$/, (socket) => {
    socket.connectToServer();
    sockets.push(socket);
  });
  const page = await openPage(
    context,
    server,
    `#/workspace/${ticketId}`,
    `section[data-screen="ticket"][data-ticket-id="${ticketId}"]`,
  );
  expect(sockets).toHaveLength(1);
  const card = page.locator('.status-card[data-connection-status]');
  await sockets[0]!.close({ code: 1012, reason: 'cut by the test' });
  await expect(card).toHaveAttribute('data-status-state', 'unreachable', { timeout: 2_000 });
  await expect(card).toContainText("Can't reach Panels");
  // The page opens a new socket on its own, and the card goes.
  await expect(card).not.toHaveAttribute('data-status-state', 'unreachable', { timeout: WAIT_MS });
  expect(sockets).toHaveLength(2);
  await api.patch(server, `/api/tickets/${ticketId}`, { title: 'Socket drop after' });
  await page.locator('.ticket-title', { hasText: 'Socket drop after' }).waitFor({ state: 'visible', timeout: WAIT_MS });
});
