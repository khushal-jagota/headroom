/**
 * A message that goes into a conversation moves the thread once.
 *
 * The risk is a frame in which the page gets shorter and the history slides down before the
 * new message gets its own room at the top. Only real layout in a browser shows it, and the
 * server must answer the send for the message to change from sending to sent. The test
 * reads the thread on every animation frame across one send, with the server's answer held
 * back and not held back, and reuses the Ticket conversation of the signal host.
 *
 * A send from the docked composer also opens the pane, which grows from the dock to the full
 * height in about 250 ms. The placement of the message must wait for that open, or its
 * target comes from a thread that is still small, and the message rides down with the pane
 * and then jumps up. Only real layout and the real dock animation show this.
 */
import { expect, openPage, test, WAIT_MS } from './support/fixtures.ts';
import { createTicketConversation, openConversationLayer, post } from './support/conversation.ts';

type Frame = { count: number; lastTop: number; beforeLastTop: number };

for (const [name, width, answerAfterMs] of [['desktop', 1280, 0], ['390 px, slow answer', 390, 600]] as const) {
  test(`a sent message moves the thread once (${name})`, async ({ signalServer, newContext }, testInfo) => {
    const context = await newContext({ viewport: { width, height: 800 } });
    const id = `send-motion-${width}`;
    const ticket = await createTicketConversation(signalServer, { conversationId: id, title: 'Send motion' });
    const page = await openPage(context, signalServer, `#/workspace/${ticket}`, `[data-screen="ticket"][data-ticket-id="${ticket}"]`);
    await openConversationLayer(page);
    const field = page.locator('[data-conversation-input]');
    const send = async (text: string) => { await field.click(); await field.fill(text); await page.keyboard.press('Enter'); };
    for (let turn = 1; turn <= 5; turn++) {
      await send(`History message number ${turn}`);
      await post(signalServer, `/api/test/signals/${id}/message`, { kind: 'ticket', id: ticket, label: 'Send motion', text: `Reply number ${turn}. `.repeat(8) });
      await post(signalServer, `/api/test/signals/${id}/finish`);
      await page.waitForTimeout(700);
    }
    // The last send leaves its answer room under it. The next send must start from there.
    await page.waitForTimeout(1000);
    if (answerAfterMs > 0) await page.route('**/conversation/send', async (route) => { await new Promise((done) => setTimeout(done, answerAfterMs)); await route.continue(); });
    await page.evaluate(() => {
      const thread = document.querySelector('[data-conversation-thread]') as HTMLElement;
      const frames: Frame[] = [];
      (window as any).__frames = frames;
      const start = performance.now();
      const top = (element: Element | undefined) => (element ? element.getBoundingClientRect().top - thread.getBoundingClientRect().top : NaN);
      const read = () => {
        const prompts = [...thread.querySelectorAll('[data-conversation-row="prompt"], [data-conversation-outgoing]')];
        frames.push({ count: prompts.length, lastTop: top(prompts.at(-1)), beforeLastTop: top(prompts.at(-2)) });
        if (performance.now() - start < 2500) requestAnimationFrame(read);
      };
      requestAnimationFrame(read);
    });
    await send('Okay, I will do that.');
    await page.waitForTimeout(2800 + answerAfterMs);
    const all: Frame[] = await page.evaluate(() => (window as any).__frames);
    // From the last frame before the new message was drawn, follow the message that was last then.
    const drawn = all.findIndex((frame) => frame.count > all[0].count);
    expect(drawn).toBeGreaterThan(0);
    const frames = all.slice(drawn - 1).map((frame) => ({
      previousTop: frame.count > all[0].count ? frame.beforeLastTop : frame.lastTop,
      sentTop: frame.count > all[0].count ? frame.lastTop : NaN,
    }));
    expect(frames.length).toBeGreaterThan(10);
    expect(frames[0].previousTop).toBeCloseTo(frames[1].previousTop, -1);
    // No frame puts the history, or the sent message, lower than the frame before.
    for (let index = 1; index < frames.length; index++) {
      expect(frames[index].previousTop, `history at frame ${index}`).toBeLessThanOrEqual(frames[index - 1].previousTop + 1);
      if (index > 1) expect(frames[index].sentTop, `sent message at frame ${index}`).toBeLessThanOrEqual(frames[index - 1].sentTop + 1);
    }
    // It ends with the sent message near the top, as the thread has always placed it.
    expect(frames.at(-1)!.sentTop).toBeLessThanOrEqual(40);
    await testInfo.attach('frames.json', { body: JSON.stringify(all), contentType: 'application/json' });
  });
}

type DockedFrame = { shown: boolean; top: number; fromThreadTop: number; paneHeight: number };

for (const mode of ['idle', 'steer'] as const) {
  test(`a send from the docked composer slides up once (${mode})`, async ({ signalServer, newContext }, testInfo) => {
    const context = await newContext({ viewport: { width: 1280, height: 800 } });
    const id = `send-motion-docked-${mode}`;
    const ticket = await createTicketConversation(signalServer, { conversationId: id, title: 'Send motion docked' });
    const page = await openPage(context, signalServer, `#/workspace/${ticket}`, `[data-screen="ticket"][data-ticket-id="${ticket}"]`);
    await openConversationLayer(page);
    const field = page.locator('[data-conversation-input]');
    const send = async (text: string) => { await field.click(); await field.fill(text); await page.keyboard.press('Enter'); };
    for (let turn = 1; turn <= 4; turn++) {
      await send(`History message number ${turn}`);
      await post(signalServer, `/api/test/signals/${id}/message`, { kind: 'ticket', id: ticket, label: 'Send motion docked', text: `Reply number ${turn}. `.repeat(8) });
      await post(signalServer, `/api/test/signals/${id}/finish`);
      await page.waitForTimeout(700);
    }
    await page.waitForTimeout(1000);
    // Hold the conversation at rest, so that the send opens the pane.
    await page.locator('[data-conversation-collapse]').click();
    await page.locator('[data-conversation-state="rest"]').waitFor();
    await page.waitForTimeout(800);
    if (mode === 'steer') {
      await post(signalServer, `/api/test/signals/${id}/start`);
      await page.waitForTimeout(1200);
      await field.fill('Add this to the running turn.');
      await page.locator('[data-conversation-send-mode-trigger]').click();
      await page.locator('[data-conversation-send-mode-choice="steer"]').click();
      await expect(page.locator('[data-conversation-state="rest"]')).toBeVisible();
    }
    await page.evaluate(() => {
      const frames: DockedFrame[] = [];
      (window as any).__dockedFrames = frames;
      const start = performance.now();
      const read = () => {
        const thread = document.querySelector('[data-conversation-thread]') as HTMLElement | null;
        const pane = document.querySelector('[data-conversation-pane]') as HTMLElement | null;
        const sent = [...(thread?.querySelectorAll('[data-conversation-outgoing]') ?? [])].at(-1);
        const box = sent?.getBoundingClientRect();
        const threadTop = thread?.getBoundingClientRect().top ?? 0;
        frames.push({
          shown: !!box && box.bottom > 0 && box.top < window.innerHeight,
          top: box ? box.top : NaN,
          fromThreadTop: box ? box.top - threadTop : NaN,
          paneHeight: pane?.getBoundingClientRect().height ?? NaN,
        });
        if (performance.now() - start < 3000) requestAnimationFrame(read);
      };
      requestAnimationFrame(read);
    });
    if (mode === 'idle') await send('Okay, I will do that.');
    else await page.locator('[data-conversation-send]').click();
    await page.waitForTimeout(3200);
    const all: DockedFrame[] = await page.evaluate(() => (window as any).__dockedFrames);
    await testInfo.attach('frames.json', { body: JSON.stringify(all), contentType: 'application/json' });
    const first = all.findIndex((frame) => frame.shown);
    expect(first, 'the sent message shows').toBeGreaterThanOrEqual(0);
    const frames = all.slice(first);
    expect(frames.length).toBeGreaterThan(10);
    // On screen, the message never moves down after it first shows, and no frame moves it far.
    for (let index = 1; index < frames.length; index++) {
      expect(frames[index].top, `sent message at frame ${index}`).toBeLessThanOrEqual(frames[index - 1].top + 1);
      expect(Math.abs(frames[index].top - frames[index - 1].top), `step at frame ${index}`).toBeLessThanOrEqual(100);
    }
    // The pane opened, and the message ends near the top of the thread.
    expect(Math.max(...frames.map((frame) => frame.paneHeight))).toBeGreaterThan(400);
    expect(frames.at(-1)!.fromThreadTop).toBeLessThanOrEqual(40);
  });
}
