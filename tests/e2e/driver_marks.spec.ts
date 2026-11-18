/**
 * Prove that Chromium draws the backend marks the live server serves.
 *
 * The marks come from the server's driver list and icon route, which answer with
 * `nosniff`, a sandbox CSP on SVG and a year of caching. The picker draws them through
 * `<img>` and the composer chip through a CSS `mask`, and Chromium refuses a wrong type or
 * header for either without a word, so every mark would simply vanish. The boundary is the
 * server icon route and Chromium's image and mask loading. vitest runs in Node with no
 * image decoding or CSS, and Fastify `inject` proves the bytes and headers but not that
 * Chromium accepts them.
 */
import { WAIT_MS, api, expect, expectResponse, test } from './support/fixtures.ts';
import { createTicketConversation, openTicketConversation } from './support/conversation.ts';

const PICKER = '[data-conversation-picker-model]';
const TRIGGER = `${PICKER} [data-conversation-picker-trigger]`;

interface Driver {
  driver_kind: string;
  icon: { url: string; media_type: string };
}

test('test_driver_marks_load_from_the_live_server_routes', async ({ signalServer, newContext }) => {
  const server = signalServer;
  const drivers: Driver[] = (await api.get(server, '/api/drivers')).drivers;
  // The seeded conversation runs on codex, so the composer chip draws codex's mark.
  const codex = drivers.find((driver) => driver.driver_kind === 'codex');
  expect(codex).toBeDefined();
  const ticketId = await createTicketConversation(server, {
    conversationId: 'e2e-driver-marks',
    title: 'Draw the driver marks',
  });
  const context = await newContext();
  // The helper opens its own page, so the listener sits on the context, before navigation.
  // Until the picker opens, the chip is the only mark on the page.
  const chipIconUrl = new URL(codex!.icon.url, server.base).href;
  const chipMaskResponse = context.waitForEvent('response', (response) => response.url() === chipIconUrl);
  const page = await openTicketConversation(server, ticketId, context, TRIGGER);
  const chipMask = await chipMaskResponse;
  await expectResponse(chipMask.status(), 200, chipMask);
  expect(chipMask.headers()['content-type']).toBe(codex!.icon.media_type);

  await page.locator(TRIGGER).click();
  const rail = page.locator(`${PICKER} [data-conversation-backend-rail]`);
  for (const driver of drivers)
    await expect(rail.locator(`[data-conversation-backend="${driver.driver_kind}"]`)).toHaveCount(1);
  const rows = rail.locator('[data-conversation-backend]');
  const marks = rows.locator('img');
  await expect(marks).toHaveCount(await rows.count());
  await expect
    .poll(() => marks.evaluateAll((images: HTMLImageElement[]) => images.every((image) => image.complete)), {
      timeout: WAIT_MS,
    })
    .toBe(true);
  const undrawn = await marks.evaluateAll((images: HTMLImageElement[]) =>
    images.filter((image) => image.naturalWidth === 0).map((image) => image.src),
  );
  expect(undrawn).toEqual([]);
});
