/**
 * One browser proof of automatic transfer, early Send, and native Range playback.
 *
 * HTTP tests cannot prove that a browser's media element consumes streamed files.
 * Reuses the signal server and browser context; only the existing backend is fake.
 */
import { mkdirSync, readFileSync, statSync } from 'node:fs';
import { basename, join } from 'node:path';
import type { Response, Route } from '@playwright/test';
import { expectResponse, REPO_ROOT, WAIT_MS, expect, test, type JsonObject } from './support/fixtures.ts';
import { createTicketConversation, getJson, openTicketConversation } from './support/conversation.ts';
import { createVideoFixture } from './support/video.ts';

test('test_video_upload_early_send_and_native_range_playback', async ({
  signalServer,
  newContext,
}, testInfo) => {
  const started = performance.now();
  // The test's own temporary folder, as Python's tmp_path.
  const temporary = testInfo.outputPath('tmp');
  const video = createVideoFixture(temporary);
  const videoName = basename(video);
  const server = signalServer;
  const conversationId = 'e2e-video-upload';
  const ticketId = await createTicketConversation(server, {
    conversationId,
    title: 'Automatic video attachment',
  });
  const response = await fetch(server.base + `/api/test/signals/${conversationId}/finish`, {
    method: 'POST',
  });
  await expectResponse(response.ok, true, response);
  const context = await newContext();
  const page = await openTicketConversation(server, ticketId, context, '[data-conversation-input]');
  await page.locator('[data-conversation-lens-choice="full"]').click();
  const browserRanges: Response[] = [];

  page.on('response', (response) => {
    if (response.url().includes('/files/') && response.request().headers()['range'])
      browserRanges.push(response);
  });
  const heldTransfers: Route[] = [];
  const reservations: Response[] = [];
  page.on('response', (r) => {
    if (r.request().method() === 'POST' && r.url().endsWith('/video-uploads')) reservations.push(r);
  });
  await page.route('**/video-uploads/*/bytes', async (route) => {
    heldTransfers.push(route);
  });
  await page.locator('[data-conversation-file-input]').setInputFiles(video);
  await expect(page.locator('[data-conversation-attachments]')).toContainText(videoName, {
    timeout: WAIT_MS,
  });
  const editor = page.locator('[data-conversation-input]');
  await expect(editor).toBeEnabled({ timeout: WAIT_MS });
  await editor.fill('Show this recording to the agent');
  await expect(page.locator('[data-conversation-send]')).toBeEnabled({ timeout: WAIT_MS });
  await page.locator('[data-conversation-send]').click();
  const pending = page.locator('[data-conversation-content] [data-local-video-send]');
  await expect(pending).toContainText('Show this recording to the agent', { timeout: WAIT_MS });
  await expect(pending).toContainText('Uploading', { timeout: WAIT_MS });
  await expect(editor).toHaveValue('');
  await expect(editor).toBeEditable();
  // The pending row can paint before the upload request reaches its route.
  const transferDeadline = performance.now() + WAIT_MS;
  while (!heldTransfers.length && performance.now() < transferDeadline) {
    const refused = reservations.filter((r) => !r.ok());
    if (refused.length)
      expect(refused, `Reservation refused: ${refused[0].status()} ${await refused[0].text()}`).toEqual([]);
    await page.waitForTimeout(50);
  }
  expect(heldTransfers.length).toBe(1);
  const evidence = process.env.PANELS_UI_PROOF_DIR ?? join(REPO_ROOT, 'data/verification/video-upload');
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({ path: join(evidence, 'video-early-send-pending.png'), fullPage: true });
  const transferResponse = page.waitForResponse(
    (result) => result.url().includes('/video-uploads/') && result.request().method() === 'PUT',
    { timeout: WAIT_MS },
  );
  await heldTransfers.pop()!.continue();
  const transfer = await transferResponse;
  await expectResponse(transfer.status(), 200, transfer);
  await expect(pending).toHaveCount(0, { timeout: WAIT_MS });
  await page.locator('[data-media-open="video"]').last().click();
  const player = page.locator('[data-media-viewer] video');
  await expect(player).toBeVisible({ timeout: WAIT_MS });
  await page.waitForFunction(
    () => {
      const v = document.querySelector<HTMLVideoElement>('[data-media-viewer] video');
      return v && v.readyState >= 1 && v.duration >= 3.9;
    },
    undefined,
    { timeout: WAIT_MS },
  );
  const href = await player.getAttribute('src');
  expect(href).toBeTruthy();
  expect(browserRanges.some((response) => response.status() === 206)).toBe(true);
  const head = await fetch(server.base + href, { method: 'HEAD' });
  expect(head.status).toBe(200);
  expect(Number.parseInt(head.headers.get('content-length')!, 10)).toBe(statSync(video).size);
  const ranged = await fetch(server.base + href, { headers: { Range: 'bytes=0-31' } });
  expect(ranged.status).toBe(206);
  expect(Buffer.from(await ranged.arrayBuffer())).toEqual(readFileSync(video).subarray(0, 32));
  await player.evaluate((video: HTMLVideoElement) => {
    video.currentTime = 2;
  });
  await page.waitForFunction(
    () => {
      const v = document.querySelector<HTMLVideoElement>('[data-media-viewer] video');
      return v && !v.seeking && v.currentTime >= 1.9;
    },
    undefined,
    { timeout: WAIT_MS },
  );
  const events: JsonObject[] = (await getJson(server, `/api/conversation/conversations/${conversationId}/events`))
    .events;
  const prompts = events.filter((event) => event.kind === 'prompt').map((event) => event.payload);
  const files = prompts
    .flatMap((prompt) => prompt.content ?? [])
    .filter((piece: JsonObject) => piece.piece === 'file');
  expect(files.length).toBe(1);
  expect(files[0].file_name).toBe(videoName);
  expect(files[0].media_type).toBe('video/mp4');
  await page.screenshot({ path: join(evidence, 'video-playback.png'), fullPage: true });
  console.log(`Video upload browser boundary: ${((performance.now() - started) / 1000).toFixed(2)}s`);
});
