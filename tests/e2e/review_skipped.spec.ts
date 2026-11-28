/**
 * Prove that a skip survives a reload and sorts out into its own pass.
 *
 * The device keeps its skips, and the server orders the queue with them. Only a real browser
 * (its storage and its reload) against a real server (its queue order) shows both together.
 */
import { api, bindConversation, connectedMessage, expect, openPage, test, ticketPrincipal, WAIT_MS, type ServerHandle } from './support/fixtures.ts';
import { post } from './support/conversation.ts';

const FRONT = '[data-deck-item]:not([data-deck-leaving])';
const CARD = `${FRONT} [data-review-ticket-card]`;
const SHOTS = process.env.REVIEW_SKIPPED_SHOTS;

async function waitingMessages(server: ServerHandle, count: number): Promise<string[]> {
  const project = await api.post(server, '/api/projects', { name: 'Skipped' });
  const principals: string[] = [];
  for (let index = 1; index <= count; index += 1) {
    const ticket = await api.post(server, '/api/tickets', {
      title: `Ticket ${index}`, worker_type: 'coding', brief: 'Approved task.', project_id: project.id,
      manager: { kind: 'chief', id: 'chief' },
    });
    const conversation = `skipped-${index}`;
    await api.post(server, `/api/tickets/${ticket.id}/accept/brief`, { next_ceiling: 'needs_what_changes', next_holder: { kind: 'owner', id: 'owner' } });
    await post(server, `/api/test/signals/${conversation}/start`);
    await post(server, `/api/test/signals/${conversation}/finish`);
    await bindConversation(server, 'ticket', String(ticket.id), conversation);
    await connectedMessage(server, conversation, ticketPrincipal(String(ticket.id)), `Question ${index}?`);
    principals.push(String(ticket.id));
  }
  return principals;
}

test('test_a_skip_survives_a_reload_and_is_reviewed_on_purpose', async ({ signalServer, newContext }) => {
  const server = signalServer;
  const tickets = await waitingMessages(server, 3);
  const context = await newContext();
  const page = await openPage(context, server, '#/review', '[data-review-card][data-review-loaded="true"]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1280, height: 800 });
  const title = () => page.locator(`${CARD} .review-ticket-title`).first();
  const shot = async (name: string) => { if (SHOTS) await page.waitForTimeout(1200); if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` }); };
  const skip = async () => { const was = await title().textContent(); await page.keyboard.press('s'); await expect(title()).not.toHaveText(was ?? ''); };

  const text = async () => ((await title().textContent()) ?? '').trim();
  const first = await text();
  await expect(page.locator('[data-review-pass]')).toHaveCount(0);
  await skip();
  const second = await text();
  expect(second).not.toBe(first);
  await expect(page.locator(`${FRONT} [data-review-pass]`).first()).toHaveText('Review skipped (1)');
  await shot('1-skipped-one-band-button');

  // Reload: the skipped Ticket stays out of the main pass.
  await page.reload();
  await expect(title()).toHaveText(second);
  await skip();
  const third = await text();
  expect([first, second]).not.toContain(third);
  await page.keyboard.press('s');
  await expect(page.locator('[data-review-return="skipped"]')).toBeVisible();
  await expect(page.locator('[data-review-final-status]')).toContainText('3 skipped');
  await expect(page.locator('[data-review-return="new"]')).toHaveCount(0);
  await shot('2-final-only-skipped');

  // The skipped pass holds the three, and "Review new" is not offered while nothing is new.
  await page.reload();
  await expect(page.locator('[data-review-return="skipped"]')).toBeVisible();
  await page.locator('[data-review-return="skipped"]').click();
  await expect(title()).toBeVisible();
  await expect(page.locator('[data-review-pass]')).toHaveCount(0);
  await shot('3-skipped-pass');

  // A new message on a skipped Ticket makes it new again, and the other skips stay.
  const changed = Number(first.replace('Ticket ', ''));
  await connectedMessage(server, `skipped-${changed}`, ticketPrincipal(tickets[changed - 1]), 'A second question?');
  await page.goto(`${server.base}/#/review`);
  await page.reload();
  await expect(title()).toHaveText(first);
  await expect(page.locator(`${FRONT} [data-review-pass]`).first()).toHaveText('Review skipped (2)');
  await shot('4-changed-item-is-new-again');

  // The skipped pass offers the way back to the new card, and ends on a page with both buttons.
  await page.locator(`${FRONT} [data-review-pass]`).first().click();
  await expect(page.locator(`${FRONT} [data-review-pass]`).first()).toHaveText('Review new');
  await expect(title()).not.toHaveText(first);
  await shot('5-skipped-pass-band-button');
  await page.keyboard.press('s');
  await expect(title()).not.toHaveText(first);
  await page.keyboard.press('s');
  await expect(page.locator('[data-review-final-status]')).toContainText('1 new, 2 skipped');
  await expect(page.locator('[data-review-return="new"]')).toHaveText('Review new');
  await expect(page.locator('[data-review-return="skipped"]')).toHaveText('Review skipped');
  await shot('6-final-both-buttons');
  await page.locator('[data-review-return="new"]').click();
  await expect(title()).toHaveText(first);
});
