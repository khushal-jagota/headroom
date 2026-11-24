/**
 * The conversation composer must grow from where its parts stood.
 *
 * A jump (the draft or the controls drawn at their new place on the first frame) comes from
 * real layout, so only a browser can see it. The test pauses every animation the composer
 * starts, reads the parts at the start of the move, and compares them with where they stood
 * one keystroke earlier. It reuses the Ticket conversation of the existing signal host.
 */
import type { Page } from '@playwright/test';
import { expect, openPage, test, WAIT_MS } from './support/fixtures.ts';
import { createTicketConversation, openConversationLayer } from './support/conversation.ts';

const PARTS = '[data-conversation-input], .chat-foot > *';

type Place = { left: number; top: number };

async function places(page: Page): Promise<Place[]> {
  return page.evaluate((selector) => Array.from(document.querySelectorAll(selector))
    .filter((element) => element.getClientRects().length > 0)
    .map((element) => { const box = element.getBoundingClientRect(); return { left: box.left, top: box.top }; }), PARTS);
}

/** Type until the composer starts a move, and return where its parts stood just before it. */
async function typeUntilItMoves(page: Page, text: string): Promise<Place[]> {
  let before = await places(page);
  for (const character of text) {
    before = await places(page);
    await page.keyboard.type(character);
    if (await page.evaluate(() => (window as any).__moves.length) > 0) return before;
  }
  throw new Error('The composer never started a move');
}

for (const [name, width] of [['desktop', 1280], ['390 px', 390]] as const) {
  test(`the composer grows and shrinks from where its parts stood (${name})`, async ({ signalServer, newContext }) => {
    const context = await newContext({ viewport: { width, height: 800 } });
    await context.addInitScript(() => {
      const moves: Animation[] = [];
      (window as any).__moves = moves;
      (window as any).__recording = false;
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (this: Element, ...input: Parameters<Element['animate']>) {
        const animation = animate.apply(this, input);
        if ((window as any).__recording && this.closest('.chat-box')) { animation.pause(); animation.currentTime = 0; moves.push(animation); }
        return animation;
      };
    });
    const ticket = await createTicketConversation(signalServer, { conversationId: `grow-motion-${width}`, title: 'Grow' });
    const page = await openPage(context, signalServer, `#/workspace/${ticket}`, `[data-screen="ticket"][data-ticket-id="${ticket}"]`);
    await openConversationLayer(page);
    await page.locator('[data-conversation-input]').click({ timeout: WAIT_MS });
    const move = async (act: () => Promise<Place[] | void>) => {
      await page.evaluate(() => { (window as any).__moves.length = 0; (window as any).__recording = true; });
      const before = (await act()) ?? (await places(page));
      const started = await places(page);
      const moves = await page.evaluate(() => (window as any).__moves.length);
      await page.evaluate(() => { (window as any).__recording = false; for (const animation of (window as any).__moves as Animation[]) animation.finish(); });
      return { before, started, moves };
    };
    const standsWhereItStood = ({ before, started, moves }: Awaited<ReturnType<typeof move>>) => {
      expect(moves).toBeGreaterThan(0);
      expect(started).toHaveLength(before.length);
      started.forEach((place, index) => {
        expect(Math.abs(place.left - before[index].left), `part ${index} left`).toBeLessThan(2);
        expect(Math.abs(place.top - before[index].top), `part ${index} top`).toBeLessThan(2);
      });
    };

    // Grow: words that no longer fit beside the controls.
    standsWhereItStood(await move(() => typeUntilItMoves(page, 'create but block on the review. yeah probably but not yet as that seems more like a thing we should check with the whole group first '.repeat(2))));

    // Shrink: the draft is cleared.
    standsWhereItStood(await move(async () => {
      const before = await places(page);
      await page.keyboard.press('Control+a');
      await page.keyboard.press('Backspace');
      return before;
    }));
  });
}
