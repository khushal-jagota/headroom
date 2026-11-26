/**
 * Prove that a reply sent from a Review message card shows one steady result.
 *
 * A reply used to draw a second copy of itself with "Sending…" and "Sent" under the message,
 * and the page that follows the last card said "1 thing awaits review." for a moment before it
 * said "All clear. Nothing to review.", drawn twice and fading over each other. The copy under
 * the message is a matter of what the live card holds, and the stale count and the double
 * drawing are a matter of the real queue count, layout and animation timing, so only a real
 * browser on a real server can show them. The test reads the page on every frame.
 */
import {
  api,
  bindConversation,
  connectedMessage,
  expect,
  openPage,
  test,
  WAIT_MS,
  ticketPrincipal,
  type ServerHandle,
} from './support/fixtures.ts';
import { post } from './support/conversation.ts';

async function ticketMessage(server: ServerHandle): Promise<void> {
  const project = await api.post(server, '/api/projects', { name: 'Reply proof' });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Reply proof ticket', worker_type: 'coding', brief: 'Agreed brief.',
    project_id: project.id, manager: { kind: 'chief', id: 'chief' },
  });
  await api.post(server, `/api/tickets/${ticket.id}/accept/brief`, {
    next_ceiling: 'needs_what_changes', next_holder: { kind: 'owner', id: 'owner' },
  });
  await post(server, '/api/test/signals/reply-proof/start');
  await post(server, '/api/test/signals/reply-proof/finish');
  await bindConversation(server, 'ticket', String(ticket.id), 'reply-proof');
  await connectedMessage(server, 'reply-proof', ticketPrincipal(String(ticket.id)), 'Which database should the migration use?');
}

/** Every frame, from the page's start: what the card and the page after it hold. */
const WATCH = () => {
  const seen = { outgoing: 0, finalTexts: new Set<string>(), finals: new Set<Element>(), busy: false };
  (window as unknown as { __seen: typeof seen }).__seen = seen;
  const tick = () => {
    if (document.querySelector('[data-review-outgoing]')) seen.outgoing += 1;
    if (document.querySelector('[data-review-send][aria-busy="true"]')) seen.busy = true;
    for (const final of document.querySelectorAll('[data-screen="review"] [data-review-final-status]')) {
      seen.finalTexts.add(final.textContent ?? '');
      seen.finals.add(final.closest('[data-deck-item]')!);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

test('test_review_reply_keeps_one_steady_result_while_a_slow_send_arrives', async ({ signalServer, newContext }) => {
  const server = signalServer;
  await ticketMessage(server);
  const context = await newContext();
  await context.addInitScript(WATCH);
  await context.route(/\/conversation\/send$/, async (route) => {
    await new Promise((done) => setTimeout(done, 1200));
    await route.continue();
  });
  const page = await openPage(context, server, '#/review', '[data-review-card][data-review-loaded="true"]');
  page.setDefaultTimeout(WAIT_MS);
  await page.locator('[data-review-reply]').fill('Use Postgres, please.');
  await page.locator('[data-review-send]').click();
  // The words leave the box at once, and the send button works while the answer is slow.
  await expect(page.locator('[data-review-reply]')).toHaveValue('');
  await expect(page.locator('[data-review-send]')).toHaveAttribute('aria-busy', 'true');
  await expect(page.locator('[data-review-final-status]')).toHaveText('All clear. Nothing to review.');
  // The send answers at acceptance. The card leaves only once the conversation recorded the reply.
  // A text-only prompt is stored as `text`; one with several pieces as `content`.
  const events: { kind: string; payload: { text?: string; content?: { text?: string }[] } }[] =
    (await api.get(server, '/api/conversation/conversations/reply-proof/events')).events;
  expect(events.filter((event) => event.kind === 'prompt'
    && (event.payload.text === 'Use Postgres, please.'
      || (event.payload.content ?? []).some((piece) => piece.text === 'Use Postgres, please.')))).toHaveLength(1);
  await page.waitForTimeout(600);
  const seen = await page.evaluate(() => {
    const value = (window as unknown as { __seen: { outgoing: number; finalTexts: Set<string>; finals: Set<Element>; busy: boolean } }).__seen;
    return { outgoing: value.outgoing, finalTexts: [...value.finalTexts], finals: value.finals.size, busy: value.busy };
  });
  expect(seen.busy, 'the send button spun while the answer was on its way').toBe(true);
  expect(seen.outgoing, 'no second copy of the reply was drawn on any frame').toBe(0);
  expect(seen.finalTexts, 'the page after the card never said that something waits').toEqual(['All clear. Nothing to review.']);
  expect(seen.finals, 'the page after the card was drawn once').toBe(1);
});

test('test_review_reply_that_the_server_refuses_returns_to_the_box_with_a_cross', async ({ signalServer, newContext }) => {
  const server = signalServer;
  await ticketMessage(server);
  const context = await newContext();
  await context.addInitScript(WATCH);
  await context.route(/\/conversation\/send$/, (route) =>
    route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'The agent is not taking messages.' }) }));
  const page = await openPage(context, server, '#/review', '[data-review-card][data-review-loaded="true"]');
  page.setDefaultTimeout(WAIT_MS);
  await page.locator('[data-review-reply]').fill('Use Postgres, please.');
  await page.locator('[data-review-send]').click();
  await expect(page.locator('[data-review-reply]')).toHaveValue('Use Postgres, please.');
  await expect(page.locator('[data-review-send]')).toHaveClass(/failed/);
  // The card stays for another try, and the refused reply was never drawn as a copy under the message.
  await expect(page.locator('[data-review-card]')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __seen: { outgoing: number } }).__seen.outgoing)).toBe(0);
});

test('test_review_chief_box_shrinks_back_to_one_line_after_a_long_message_is_sent', async ({ signalServer, newContext }) => {
  // The Chief box holds its height while a send is in flight. Only a real browser lays out the box,
  // so only it shows that the hold is let go once the send succeeds, and kept when it fails.
  const chiefConversation = 'review-shrink-chief';
  await post(signalServer, `/api/test/signals/${chiefConversation}/start`);
  await bindConversation(signalServer, 'agent', 'chief_of_staff', chiefConversation);
  await post(signalServer, `/api/test/signals/${chiefConversation}/finish`);
  const page = await openPage(await newContext(), signalServer, '#/review', '[data-review-chief-composer]');
  page.setDefaultTimeout(WAIT_MS);
  const chief = page.locator('[data-review-chief-composer]');
  const input = chief.locator('[data-review-reply]');
  const height = async () => (await chief.locator('.composer-field').boundingBox())!.height;
  const oneLine = await height();
  const long = Array.from({ length: 8 }, (_, line) => `Line ${line + 1} of a long message to the Chief.`).join('\n');
  const evidence = process.env.PANELS_SHRINK_CAPTURE_DIR;
  const sendRoute = '**/api/chief/conversation/send';
  await page.route(sendRoute, (route) => route.fulfill({
    status: 409, contentType: 'application/json', body: '{"detail":"The Chief is not available right now."}',
  }));
  await input.fill(long);
  await expect.poll(height, { message: 'a long draft grows the box' }).toBeGreaterThan(oneLine * 2);
  if (evidence) await chief.screenshot({ path: `${evidence}/before-send.png` });
  await chief.locator('[data-review-send]').click();
  await expect(chief.locator('[data-review-send]')).toHaveClass(/failed/);
  await expect(input).toHaveValue(long);
  expect(await height(), 'a failed send keeps the size').toBeGreaterThan(oneLine * 2);
  await page.unroute(sendRoute);
  await chief.locator('[data-review-send]').click();
  await expect(input).toHaveValue('');
  await expect.poll(height, { message: 'a sent message lets the box shrink back' }).toBeLessThan(oneLine + 2);
  if (evidence) await chief.screenshot({ path: `${evidence}/after-send.png` });
});
