/**
 * Prove that opening Review draws the screen once.
 *
 * Review draws its deck before its data arrives. If the deck shows a placeholder view first,
 * the real view arrives as a move and both copies stand on screen together. That is a matter
 * of timing between data, layout and animation, so it needs a real browser. The test counts
 * the deck items on every animation frame from the route change from another page. A fresh load and the items case also pass on the old
 * code, so only the move from another page is kept: with an empty queue the old code drew three.
 */
import { mkdirSync } from 'node:fs';
import {
  api,
  expect,
  openPage,
  proposeAsWorker,
  test,
  WAIT_MS,
  type ServerHandle,
} from './support/fixtures.ts';

async function waitingTicket(server: ServerHandle, projectId: string, title: string): Promise<void> {
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
  await proposeAsWorker(server, id, 'Proposal body.');
}

type Shown = { items: number; ready: boolean };

const RECORD = () => {
  const frames: Shown[] = [];
  (window as unknown as { __shown: Shown[] }).__shown = frames;
  const tick = () => {
    frames.push({
      items: document.querySelectorAll('[data-screen="review"] [data-deck-item]').length,
      ready: document.querySelector('[data-screen="review"] [data-deck-item]') !== null,
    });
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};

for (const queue of ['empty', 'items'] as const) {
  for (const entry of ['from another page'] as const) {
    test(`test_review_opens_once_${queue}_${entry.replace(/ /g, '_')}`, async ({ railServer, newContext }) => {
      const server = railServer;
      if (queue === 'items') {
        const project = await api.post(server, '/api/projects', { name: 'Open once' });
        await waitingTicket(server, String(project.id), 'Card A');
        await waitingTicket(server, String(project.id), 'Card B');
      }
      const context = await newContext();
      await context.addInitScript(RECORD);
      const page = await openPage(context, server, '#/usage', 'body');
      page.setDefaultTimeout(WAIT_MS);
      await page.evaluate(() => { (window as unknown as { __shown: Shown[] }).__shown.length = 0; });
      await page.evaluate(() => { window.location.hash = '#/review'; });
      await page.waitForSelector('[data-screen="review"] [data-deck-item]');
      const frames = process.env.FRAMES_DIR ? `${process.env.FRAMES_DIR}/${queue}-${entry.replace(/ /g, '-')}` : null;
      if (frames) mkdirSync(frames, { recursive: true });
      for (let step = 0; step < (frames ? 8 : 0); step += 1) {
        await page.screenshot({ path: `${frames}/${step}.png` });
        await page.waitForTimeout(40);
      }
      await page.waitForTimeout(800);
      const recorded = await page.evaluate(() => (window as unknown as { __shown: Shown[] }).__shown);
      expect(recorded.some((frame) => frame.ready), 'the screen is drawn').toBe(true);
      expect(Math.max(...recorded.map((frame) => frame.items)), 'one deck item on every frame').toBe(1);
    });
  }
}
