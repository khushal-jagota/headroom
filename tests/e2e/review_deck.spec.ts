/**
 * Prove the review page's deck move keeps the leaving card exactly where it was.
 *
 * The first version of this design copied the card, lost its padding and height, and made
 * every move jump up before it slid across. Layout does not exist outside a real browser,
 * so only a real browser can show that the leaving card keeps its position and size and
 * that nothing moves vertically. The test reads the page on every frame of a move.
 */
import {
  api,
  bindConversation,
  connectedMessage,
  expect,
  openPage,
  proposeAsWorker,
  test,
  ticketPrincipal,
  WAIT_MS,
  type ServerHandle,
} from './support/fixtures.ts';
import { post } from './support/conversation.ts';

const LONG_PROPOSAL = Array.from(
  { length: 40 },
  (_, index) => `Paragraph ${index + 1} of a long proposal that makes the card taller than the page.`,
).join('\n\n');

async function waitingTicket(server: ServerHandle, projectId: string, title: string, body: string): Promise<string> {
  const ticket = await api.post(server, '/api/tickets', {
    title,
    worker_type: 'coding',
    brief: 'Approved task.',
    project_id: projectId,
    manager: { kind: 'chief', id: 'chief' },
  });
  const id = String(ticket.id);
  await api.post(server, `/api/tickets/${id}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  await proposeAsWorker(server, id, body);
  return id;
}

interface Frame {
  leaving: { left: number; top: number; width: number; height: number; opacity: number; titleTop: number; titleLeft: number } | null;
  arriving: { left: number; top: number; opacity: number } | null;
}

test('test_deck_move_keeps_the_leaving_card_where_it_was', async ({ railServer, newContext }) => {
  const server = railServer;
  const project = await api.post(server, '/api/projects', { name: 'Deck proof' });
  // Both cards are taller than the page, so either can be the one that is scrolled.
  await waitingTicket(server, String(project.id), 'Card A', LONG_PROPOSAL);
  await waitingTicket(server, String(project.id), 'Card B', LONG_PROPOSAL);

  const page = await openPage(await newContext(), server, '#/review', '[data-review-ticket-card]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1280, height: 720 });
  const scroller = page.locator('[data-deck-scroll]');
  const first = page.locator('[data-deck-item]:not([data-deck-leaving])');
  const title = first.locator('.review-ticket-title');
  await expect(title).toContainText(/Card [AB]/);
  const shownFirst = (await title.innerText()).trim();

  // Where a card stands when it is at the top of the scroll area.
  const resting = await first.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return { left: box.left, top: box.top };
  });
  // Scroll the first card, so that going back to the top is a move the test can see.
  await scroller.evaluate((node) => node.scrollTo(0, 200));
  expect(await scroller.evaluate((node) => node.scrollTop)).toBeGreaterThan(100);
  const before = await first.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const title = node.querySelector('.review-ticket-title')!.getBoundingClientRect();
    return { left: box.left, top: box.top, width: box.width, height: box.height, titleTop: title.top, titleLeft: title.left };
  });

  // Record every frame of the move, starting before the key is pressed.
  await page.evaluate(() => {
    const frames: Frame[] = [];
    (window as unknown as { __deckFrames: Frame[] }).__deckFrames = frames;
    const started = performance.now();
    const read = (node: Element) => {
      const box = node.getBoundingClientRect();
      // The card's own title: its place inside the leaving item must not change either.
      const title = node.querySelector('.review-ticket-title')?.getBoundingClientRect();
      return {
        left: box.left, top: box.top, width: box.width, height: box.height,
        opacity: Number(getComputedStyle(node).opacity),
        titleTop: title?.top ?? 0, titleLeft: title?.left ?? 0,
      };
    };
    const tick = () => {
      const leaving = document.querySelector('[data-deck-item][data-deck-leaving]');
      const arriving = document.querySelector('[data-deck-item]:not([data-deck-leaving])');
      frames.push({ leaving: leaving ? read(leaving) : null, arriving: arriving ? read(arriving) : null });
      if (performance.now() - started < 900) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await page.keyboard.press('s');
  await expect(title).not.toContainText(shownFirst);
  await page.waitForTimeout(700);
  const recorded = await page.evaluate(() => (window as unknown as { __deckFrames: Frame[] }).__deckFrames);
  // The move starts at the first frame that has a card leaving. Frames before the key press are not part of it.
  const frames = recorded.slice(Math.max(0, recorded.findIndex((frame) => frame.leaving)));

  const leaving = frames.filter((frame) => frame.leaving).map((frame) => frame.leaving!);
  expect(leaving.length, 'the card leaves over several frames').toBeGreaterThan(3);
  for (const frame of leaving) {
    // Same size and same height from the screen's top: nothing moves vertically.
    expect(frame.width).toBeCloseTo(before.width, 1);
    expect(frame.height).toBeCloseTo(before.height, 1);
    expect(frame.top).toBeCloseTo(before.top, 1);
    // The title inside it stands at the same height, and moves with the card across.
    expect(frame.titleTop).toBeCloseTo(before.titleTop, 1);
    expect(frame.titleLeft - frame.left).toBeCloseTo(before.titleLeft - before.left, 1);
    // It only slides left, by at most the item travel (32px).
    expect(frame.left).toBeLessThanOrEqual(before.left + 0.01);
    expect(frame.left).toBeGreaterThanOrEqual(before.left - 32.01);
  }
  // Its first drawn frame is where it stood, before any slide has taken it away.
  expect(Math.abs(leaving[0].left - before.left)).toBeLessThan(4);

  const arriving = frames.filter((frame) => frame.arriving).map((frame) => frame.arriving!);
  for (const frame of arriving) {
    // The next card starts at the top of the scroll area and never moves vertically.
    expect(frame.top).toBeCloseTo(resting.top, 1);
    // It comes from the right, by at most 32px.
    expect(frame.left).toBeGreaterThanOrEqual(resting.left - 0.01);
    expect(frame.left).toBeLessThanOrEqual(resting.left + 32.01);
  }
  expect(await scroller.evaluate((node) => node.scrollTop)).toBe(0);
  // The screen is never blank: something is drawn in every frame of the move.
  for (const frame of frames)
    expect(Math.max(frame.leaving?.opacity ?? 0, frame.arriving?.opacity ?? 0)).toBeGreaterThan(0);
  // Nothing is left behind when the move ends.
  await expect(page.locator('[data-deck-item][data-deck-leaving]')).toHaveCount(0);

  // For a person who prefers reduced motion every change is a fade, and nothing travels.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.evaluate(() => {
    const seen = { leaving: [] as number[], arriving: [] as number[] };
    (window as unknown as { __reducedLefts: typeof seen }).__reducedLefts = seen;
    const started = performance.now();
    const tick = () => {
      const leaving = document.querySelector('[data-deck-item][data-deck-leaving]');
      const arriving = document.querySelector('[data-deck-item]:not([data-deck-leaving])');
      if (leaving) seen.leaving.push(Math.round(leaving.getBoundingClientRect().left * 10) / 10);
      if (arriving) seen.arriving.push(Math.round(arriving.getBoundingClientRect().left * 10) / 10);
      if (performance.now() - started < 600) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await page.keyboard.press('s');
  await expect(page.locator('[data-review-return]')).toBeVisible();
  await page.waitForTimeout(500);
  const lefts = await page.evaluate(() => (window as unknown as { __reducedLefts: { leaving: number[]; arriving: number[] } }).__reducedLefts);
  expect(lefts.leaving.length, 'the last card fades out over several frames').toBeGreaterThan(1);
  expect(new Set(lefts.leaving).size).toBe(1);
  expect(new Set(lefts.arriving.filter((left) => left > 0)).size).toBe(1);
});

test('test_resolved_part_keeps_its_place_while_it_leaves_and_the_gap_closes', async ({ signalServer, newContext }) => {
  // The part of a mixed Ticket that is decided leaves left, in the place it stood. The card
  // below it only moves when the gap closes. Only a real browser lays this out.
  const server = signalServer;
  const project = await api.post(server, '/api/projects', { name: 'Deck part proof' });
  const id = await waitingTicket(server, String(project.id), 'Mixed card', 'A short proposal.');
  await post(server, '/api/test/signals/deck-part/start');
  await bindConversation(server, 'ticket', id, 'deck-part');
  await connectedMessage(server, 'deck-part', ticketPrincipal(id), 'A message that waits for the owner.');

  const page = await openPage(await newContext(), server, '#/review', '[data-review-item-type="message"]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1280, height: 720 });
  const part = page.locator('[data-deck-part]:has([data-review-item-type="message"])');
  await expect(part.locator('[data-review-mark-read]')).toBeEnabled();
  const before = await part.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const card = node.querySelector('.review-message-card')!.getBoundingClientRect();
    const keys = document.querySelector('.review-keys')!.getBoundingClientRect();
    return { left: box.left, top: box.top, height: box.height, cardTop: card.top, cardLeft: card.left, keysTop: keys.top };
  });

  await page.evaluate(() => {
    const frames: unknown[] = [];
    (window as unknown as { __partFrames: unknown[] }).__partFrames = frames;
    const started = performance.now();
    const tick = () => {
      const leaving = document.querySelector('[data-deck-part][data-deck-leaving]:has(.review-message-card)');
      if (leaving) {
        const box = leaving.getBoundingClientRect();
        const card = leaving.querySelector('.review-message-card')!.getBoundingClientRect();
        frames.push({
          left: box.left, top: box.top, height: box.height, cardTop: card.top, cardLeft: card.left,
          opacity: Number(getComputedStyle(leaving).opacity), keysTop: document.querySelector('.review-keys')!.getBoundingClientRect().top,
        });
      }
      if (performance.now() - started < 1200) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await part.locator('[data-review-mark-read]').click();
  await expect(page.locator('[data-deck-part][data-deck-leaving]')).toHaveCount(0);
  await page.waitForTimeout(300);
  const frames = (await page.evaluate(() => (window as unknown as { __partFrames: unknown[] }).__partFrames)) as Array<{
    left: number; top: number; height: number; cardTop: number; cardLeft: number; opacity: number; keysTop: number;
  }>;

  expect(frames.length, 'the part leaves over several frames').toBeGreaterThan(3);
  for (const frame of frames) {
    // The card inside the part stays at its height and only slides left, by at most 32px.
    expect(frame.cardTop).toBeCloseTo(before.cardTop, 1);
    expect(frame.height).toBeCloseTo(before.height, 1);
    expect(frame.left).toBeLessThanOrEqual(before.left + 0.01);
    expect(frame.left).toBeGreaterThanOrEqual(before.left - 32.01);
    // What follows it starts where it was and never goes below that.
    expect(frame.keysTop).toBeLessThanOrEqual(before.keysTop + 0.01);
  }
  // The gap has closed at the end: the keys line sits higher than it did.
  const after = await page.evaluate(() => document.querySelector('.review-keys')!.getBoundingClientRect().top);
  expect(after).toBeLessThan(before.keysTop);
});
