/**
 * Prove that every move into and out of the Review final page stays smooth.
 *
 * Two faults showed on a single Skip of the last card. The leaving card jumped sideways when
 * the final page changed its parent's box, and the final page printed "Unavailable" for the
 * round trip that the new queue view needed. Layout and a live server are both needed to
 * show either fault, so only a real browser against a real server can prove the repair.
 * Each run reads the page on every frame. It runs with no delay and again with a 400 ms
 * delay on `/api/navigation`, which stretches the pending window.
 */
import {
  api,
  bindConversation,
  connectedMessage,
  expect,
  openPage,
  test,
  ticketPrincipal,
  WAIT_MS,
  type ServerHandle,
} from './support/fixtures.ts';
import type { Page } from '@playwright/test';
import { post } from './support/conversation.ts';

interface Frame {
  status: string | null;
  leaving: { left: number; top: number; final: boolean } | null;
}

const FRONT = '[data-deck-item]:not([data-deck-leaving])';

async function waitingMessages(server: ServerHandle, count: number): Promise<void> {
  const project = await api.post(server, '/api/projects', { name: 'Transitions' });
  for (let index = 1; index <= count; index += 1) {
    const ticket = await api.post(server, '/api/tickets', {
      title: `Ticket ${index}`,
      worker_type: 'coding',
      brief: 'Approved task.',
      project_id: project.id,
      manager: { kind: 'chief', id: 'chief' },
    });
    const conversation = `transition-${index}`;
    await api.post(server, `/api/tickets/${ticket.id}/accept/brief`, {
      next_ceiling: 'needs_what_changes',
      next_holder: { kind: 'owner', id: 'owner' },
    });
    await post(server, `/api/test/signals/${conversation}/start`);
    await post(server, `/api/test/signals/${conversation}/finish`);
    await bindConversation(server, 'ticket', String(ticket.id), conversation);
    await connectedMessage(server, conversation, ticketPrincipal(String(ticket.id)), `Question ${index}?`);
  }
}

/** Run one move and read every frame of it. The leaving card must hold still until it fades. */
async function move(page: Page, label: string, act: () => Promise<void>, settled: string): Promise<void> {
  const stood = await page.locator(FRONT).evaluate((node) => {
    const box = node.getBoundingClientRect();
    return { left: box.left, top: box.top, final: node.querySelector('[data-review-empty]') !== null };
  });
  await page.evaluate(() => {
    const w = window as unknown as { __frames: Frame[]; __stop: boolean };
    w.__frames = [];
    w.__stop = false;
    const tick = () => {
      const node = document.querySelector('[data-deck-item][data-deck-leaving]');
      const box = node?.getBoundingClientRect();
      w.__frames.push({
        status: document.querySelector('[data-review-final-status]')?.textContent?.trim() ?? null,
        leaving: box && node ? { left: box.left, top: box.top, final: node.querySelector('[data-review-empty]') !== null } : null,
      });
      if (!w.__stop) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await act();
  await page.locator(settled).first().waitFor();
  await page.waitForTimeout(700);
  const frames = await page.evaluate(() => {
    const w = window as unknown as { __frames: Frame[]; __stop: boolean };
    w.__stop = true;
    return w.__frames;
  });
  expect(frames.length, `${label}: frames were read`).toBeGreaterThan(10);
  for (const frame of frames) expect(frame.status ?? '', `${label}: the final page never says Unavailable while the queue loads`).not.toMatch(/Unavailable/);
  if (stood.final) return;
  const leaving = frames.filter((frame) => frame.leaving).map((frame) => frame.leaving!);
  expect(leaving.length, `${label}: the card leaves over several frames`).toBeGreaterThan(3);
  for (const frame of leaving) {
    // It only slides left, by at most the item travel (32px), and never moves vertically.
    expect(Math.abs(frame.top - stood.top), `${label}: the card stays at its height`).toBeLessThan(1);
    expect(frame.left, `${label}: the card does not jump right`).toBeLessThanOrEqual(stood.left + 1);
    expect(frame.left, `${label}: the card does not jump left`).toBeGreaterThanOrEqual(stood.left - 33);
  }
}

for (const delay of [0, 400]) {
  test(`test_review_moves_stay_smooth_with_${delay}ms_delay`, async ({ signalServer, newContext }) => {
    test.setTimeout(120_000);
    const server = signalServer;
    await waitingMessages(server, 3);
    const context = await newContext();
    if (delay)
      await context.route(/\/api\/navigation/, async (route) => {
        await new Promise((resolve) => setTimeout(resolve, delay));
        await route.continue();
      });
    const page = await openPage(context, server, '#/review', '[data-review-card][data-review-loaded="true"]');
    page.setDefaultTimeout(WAIT_MS);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(800);
    const button = (name: RegExp) => page.locator(`${FRONT} button`, { hasText: name }).first();
    const card = '[data-deck-item]:not([data-deck-leaving]) [data-review-card]';
    const final = '[data-review-return]';

    await move(page, 'skip a card that is not last', () => button(/^Skip$/).click(), card);
    await move(page, 'mark read a card that is not last', () => button(/^Mark read$/).click(), card);
    // The last card goes while one skipped card remains: the queue view is replaced.
    await move(page, 'skip the last card', () => button(/^Skip$/).click(), final);
    await expect(page.locator('[data-review-final-status]')).toContainText('2 skipped');
    await move(page, 'review skipped', () => page.locator(final).click(), card);
    await move(page, 'skip then mark read the last card', async () => {
      await button(/^Skip$/).click();
      await page.locator(card).first().waitFor();
      await page.waitForTimeout(700);
      await button(/^Mark read$/).click();
    }, final);
    await move(page, 'review skipped again', () => page.locator(final).click(), card);
    await move(page, 'reply on the last card', async () => {
      await page.locator(`${FRONT} [data-review-reply]`).fill('ok');
      await page.locator(`${FRONT} [data-review-send]`).click();
    }, '[data-review-final-count="0"]');
    await move(page, 'open Chief', () => page.locator('a', { hasText: 'Open Chief' }).click(), '[data-screen]:not([data-screen="review"])');
  });
}

test('test_review_final_page_still_says_unavailable_when_the_queue_cannot_load', async ({ signalServer, newContext }) => {
  // The pending state must not hide a real failure: with every queue read refused, the page tells the truth.
  const server = signalServer;
  await waitingMessages(server, 1);
  const context = await newContext();
  const page = await openPage(context, server, '#/review', '[data-review-card][data-review-loaded="true"]');
  page.setDefaultTimeout(WAIT_MS);
  await page.waitForTimeout(800);
  await context.route(/\/api\/navigation/, (route) => route.abort());
  await page.locator(`${FRONT} button`, { hasText: /^Skip$/ }).first().click();
  await expect(page.locator('[data-review-final-status]')).toContainText('Unavailable', { timeout: 30_000 });
});
