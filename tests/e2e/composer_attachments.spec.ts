/**
 * Browser file events must reach the real submit route from every composer placement.
 *
 * SSR and unit tests cannot prove event bubbling through controls and artifact overlays,
 * browser File reads, or the final connected send. Three cases reuse the existing hosts:
 * conversation placements, Review replies, and approval placements. Structured answers
 * use the existing question cases below, without a new server host or provider process.
 */
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Locator, Page, Response } from '@playwright/test';
import type { UploadDestination } from '../../server/conversation/video_uploads/contracts.ts';
import {
  api, bindConversation, captureSuccess, connectedMessage, expect, openPage, proposeAsWorker, test,
  ticketPrincipal, WAIT_MS, withDatabase, type JsonObject, type ServerHandle,
} from './support/fixtures.ts';
import { createTicketConversation, getJson, openConversationLayer, post } from './support/conversation.ts';
import { createVideoFixture } from './support/video.ts';

type Attached = { name: string; bytes: Buffer; mediaType: string; uploadId?: string };
type UploadResponse = { ok: boolean; status: number; url: string; declaration?: JsonObject };
const UPLOAD_ROOT = '/api/conversation/video-uploads';
const BINARY_PATTERN = [0, 255, 128, 1, 13, 10, 65];

/** Dispatch native browser file events on a control and the textarea, rather than a picker. */
async function dropAndPaste(scope: Locator, input: Locator, label: string, video: Buffer, destination: UploadDestination, largeBinary = false): Promise<Attached[]> {
  const picked: Attached[] = [
    { name: `${label}-drop.mp4`, bytes: video, mediaType: 'video/mp4' },
    { name: `${label}-paste.bin`, bytes: Buffer.alloc(largeBinary ? 20 * 1024 * 1024 + 1 : 257, Buffer.from(BINARY_PATTERN)), mediaType: 'application/octet-stream' },
  ];
  const page = input.page();
  const reserved: UploadResponse[] = [];
  const transferred: UploadResponse[] = [];
  // Keep transport metadata only. Large request bodies can evict even the PUT's own CDP body.
  const track = (response: Response) => {
    const request = response.request();
    const transport = { ok: response.ok(), status: response.status(), url: response.url() };
    if (request.method() === 'POST' && response.url().endsWith(UPLOAD_ROOT))
      reserved.push({ ...transport, declaration: request.postDataJSON() });
    if (request.method() === 'PUT' && response.url().includes(UPLOAD_ROOT) && response.url().endsWith('/bytes'))
      transferred.push(transport);
  };
  const readyStatus = async (transfer: UploadResponse): Promise<JsonObject> => {
    expect(transfer.ok, JSON.stringify(transfer)).toBe(true);
    const url = new URL(transfer.url);
    url.pathname = url.pathname.replace(/\/bytes$/, '');
    // This public GET uses Node's HTTP transport, independent of Chromium's inspector cache.
    const response = await fetch(url);
    const body = await response.text();
    expect(response.status, body).toBe(200);
    const status = JSON.parse(body) as JsonObject;
    expect(status.state).toBe('ready');
    expect(status.upload_id).toBe(decodeURIComponent(url.pathname.split('/').at(-1)!));
    return status;
  };
  page.on('response', track);
  const surface = input.locator('xpath=ancestor::*[@data-composer-attachment-surface][1]');
  await expect(surface).toHaveCount(1);
  // All placements use the same unrestricted picker policy as drop and paste.
  const picker = surface.locator('input[type=file]');
  await expect(picker).toHaveCount(1);
  expect([null, '']).toContain(await picker.getAttribute('accept'));
  // The control is inside the composer: its bubbled event must reach the shared surface once.
  const control = surface.locator('button').first();
  await control.evaluate((node, attachment) => {
    const transfer = new DataTransfer();
    const bytes = Uint8Array.from(atob(attachment.base64), (character) => character.charCodeAt(0));
    transfer.items.add(new File([bytes], attachment.name, { type: 'video/mp4' }));
    for (const name of ['dragenter', 'dragover', 'drop'])
      node.dispatchEvent(new DragEvent(name, { dataTransfer: transfer, bubbles: true, cancelable: true }));
  }, { name: picked[0].name, base64: video.toString('base64') });
  await expect(scope.locator('.chat-att')).toHaveCount(1);
  await expect.poll(() => transferred.length, { timeout: WAIT_MS }).toBe(1);
  await readyStatus(transferred[0]);
  await expect(input).toBeEnabled();
  await input.evaluate((node, attachment) => {
    const transfer = new DataTransfer();
    // Construct the large binary locally in Chromium, without a large test-protocol JSON body.
    const bytes = new Uint8Array(attachment.size);
    for (let index = 0; index < bytes.length; index++) bytes[index] = attachment.pattern[index % attachment.pattern.length];
    transfer.items.add(new File([bytes], attachment.name, { type: 'application/octet-stream' }));
    node.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, { name: picked[1].name, size: picked[1].bytes.length, pattern: BINARY_PATTERN });
  const previews = scope.locator('.chat-att');
  await expect(previews).toHaveCount(2);
  for (const [index, file] of picked.entries())
    await expect.poll(async () => (await previews.nth(index).getAttribute('title'))?.split(' · ')[0]).toBe(file.name);
  await expect.poll(() => transferred.length, { timeout: WAIT_MS }).toBe(2);
  expect(reserved).toHaveLength(2);
  for (const [index, file] of picked.entries()) {
    const status = await readyStatus(transferred[index]);
    expect(status.filename).toBe(file.name);
    expect(status.media_type).toBe(file.mediaType);
    expect(status.byte_count).toBe(file.bytes.length);
    expect(status.received_bytes).toBe(file.bytes.length);
    expect(status.destination).toEqual(destination);
    const reservation = reserved[index];
    expect(reservation.ok, JSON.stringify(reservation)).toBe(true);
    expect(reservation.declaration).toEqual({ destination, filename: file.name, byte_count: file.bytes.length });
    file.uploadId = status.upload_id;
  }
  expect(new Set(picked.map((file) => file.uploadId)).size).toBe(2);
  page.off('response', track);
  await expect(input).toBeEnabled();
  return picked;
}

function artifact(server: ServerHandle, ticketId: string, label: string): string {
  const folder = join(dirname(server.dbPath), 'files', 'tickets', ticketId, 'artifacts');
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, `${label}.md`), `# ${label}\n\nThe composer stays under this artifact.\n`);
  return `[${label}](/files/tickets/${ticketId}/artifacts/${label}.md)`;
}

/** The submit carries only immutable upload identities, never file bytes in JSON. */
function expectUploadPieces(pieces: JsonObject[], attached: Attached[]): void {
  const files = pieces.filter((piece) => piece.piece !== 'text');
  expect(files).toEqual(attached.map((file) => ({ piece: 'attachment_upload', upload_id: file.uploadId })));
}

/** The real core must keep each file once and serve the exact accepted bytes. */
async function expectStoredPieces(server: ServerHandle, conversation: string, pieces: JsonObject[], attached: Attached[]): Promise<void> {
  const files = pieces.filter((piece) => piece.piece === 'file' || piece.file_name);
  expect(files.map((file) => file.file_name)).toEqual(attached.map((file) => file.name));
  for (let index = 0; index < attached.length; index++) {
    const response = await fetch(`${server.base}/api/conversation/conversations/${conversation}/files/${files[index].stored_file_id}`);
    expect(response.status).toBe(200);
    expect(Buffer.from(await response.arrayBuffer())).toEqual(attached[index].bytes);
  }
}

async function expectPromptFiles(server: ServerHandle, conversation: string, attached: Attached[]): Promise<void> {
  const events: JsonObject[] = (await getJson(server, `/api/conversation/conversations/${conversation}/events`)).events;
  const matching = events.filter((event) => event.kind === 'prompt' &&
    (event.payload.content ?? []).some((piece: JsonObject) => piece.file_name === attached[0].name));
  expect(matching).toHaveLength(1);
  await expectStoredPieces(server, conversation, matching[0].payload.content, attached);
}

/** A decision pins ready uploads until the next Worker receives their managed references. */
async function expectPinnedDecisionFiles(server: ServerHandle, attachments: Attached[]): Promise<void> {
  for (const file of attachments) {
    const status = await getJson(server, `${UPLOAD_ROOT}/${file.uploadId}`);
    expect(status.state).toBe('admitted');
    expect(status.filename).toBe(file.name);
    expect(status.media_type).toBe(file.mediaType);
    expect(readFileSync(join(dirname(server.dbPath), 'files', 'video-uploads', file.uploadId!))).toEqual(file.bytes);
  }
}

async function sent(page: Page, path: string, action: () => Promise<void>): Promise<Response> {
  const written = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(path));
  await action();
  const response = await written;
  expect(response.ok(), await response.text()).toBe(true);
  return response;
}

/** A file in the DOM can still be clipped by the composer's finite height morph. */
async function settleComposerGeometry(composer: Locator): Promise<void> {
  await composer.evaluate(async (box, timeout) => {
    const deadline = performance.now() + timeout;
    const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    let previous = '';
    let stableFrames = 0;
    while (performance.now() < deadline) {
      const moving = box.getAnimations({ subtree: true }).filter((animation) =>
        Number.isFinite(animation.effect?.getComputedTiming().endTime) && animation.playState !== 'finished');
      await Promise.all(moving.map((animation) => animation.finished.catch(() => undefined)));
      await frame();
      const geometry = JSON.stringify([box, ...box.querySelectorAll('[data-conversation-input], .chat-att, [data-conversation-send]')]
        .map((node) => {
          const rect = node.getBoundingClientRect();
          return [rect.x, rect.y, rect.width, rect.height];
        }));
      stableFrames = moving.length === 0 && geometry === previous ? stableFrames + 1 : 0;
      previous = geometry;
      if (stableFrames >= 2) return;
    }
    throw new Error('The structured answer composer did not settle for its visible proof.');
  }, WAIT_MS);
}

/** Preserve the observed UI state instead of fast-forwarding its animations for a capture. */
async function captureStructuredState(page: Page, name: string): Promise<void> {
  const output = process.env.PANELS_UI_PROOF_DIR;
  if (!output) return;
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, name), fullPage: true });
}

async function expectStructuredAnswer(server: ServerHandle, conversation: string, response: Response, attachments: Attached[], ordinarySends: string[]): Promise<JsonObject[]> {
  const submitted = response.request().postDataJSON();
  expect(submitted.answers.scope.answers).toEqual(['Composer', 'Transcript']);
  expectUploadPieces(submitted.answers.proof.attachments, attachments);
  const received = await getJson(server, `/api/test/signals/${conversation}/answers`);
  expect(received['input-1'].proof.answers).toEqual(['The files answer the question.']);
  await expectStoredPieces(server, conversation, received['input-1'].proof.attachments, attachments);
  expect(ordinarySends).toEqual([]);
  const view = await getJson(server, `/api/conversation/conversations/${conversation}`);
  expect(view.held_prompts).toEqual([]);
  const events: JsonObject[] = (await getJson(server, `/api/conversation/conversations/${conversation}/events`)).events;
  const answered = events.filter((event) => event.kind === 'user_input_answered');
  expect(answered).toHaveLength(1);
  await expectStoredPieces(server, conversation, answered[0].payload.answers.proof.attachments, attachments);
  return answered[0].payload.answers.proof.attachments;
}

/** A new document must render the recorded answer, including the exact managed identities. */
async function expectRecordedStructuredAnswer(page: Page, conversation: string, attached: Attached[], managed: JsonObject[]): Promise<void> {
  await page.reload();
  await page.waitForSelector('[data-conversation-rest-bar]', { timeout: WAIT_MS });
  if (await page.locator('[data-conversation-pane]').getAttribute('data-conversation-state') !== 'opened')
    await openConversationLayer(page);
  await page.locator('[data-conversation-lens-choice="full"]').click();
  const recorded = page.locator('[data-conversation-user-input-state="answered"]');
  await expect(recorded).toHaveCount(1);
  const rows = recorded.locator('[data-conversation-user-input-answers] > div');
  const scope = rows.filter({ has: page.getByText('Scope', { exact: true }) });
  const proof = rows.filter({ has: page.getByText('Proof', { exact: true }) });
  await expect(scope).toContainText('Composer, Transcript');
  await expect(scope.locator('[data-conversation-file-name]')).toHaveCount(0);
  await expect(proof).toContainText('The files answer the question.');
  await expect(proof.locator('[data-conversation-file-name]')).toHaveCount(2);
  for (const file of attached) {
    const kept = managed.find((piece) => piece.file_name === file.name);
    expect(kept).toBeDefined();
    const href = `/api/conversation/conversations/${conversation}/files/${kept!.stored_file_id}`;
    const card = proof.locator(`[data-conversation-file-name="${file.name}"]`);
    await card.scrollIntoViewIfNeeded();
    if (file.mediaType.startsWith('video/')) {
      const video = card.locator('video');
      await expect(video).toHaveAttribute('src', `${href}#t=0.1`);
      await expect.poll(() => video.evaluate((node: HTMLVideoElement) => node.readyState >= 1 && node.videoWidth > 0), { timeout: WAIT_MS }).toBe(true);
    } else {
      await expect(card.locator('[data-conversation-file-download]')).toHaveAttribute('href', href);
    }
  }
  await recorded.scrollIntoViewIfNeeded();
  await expect(recorded).toBeInViewport({ ratio: 1, timeout: WAIT_MS });
  for (const card of await proof.locator('[data-conversation-file-name]').all())
    await expect(card).toBeInViewport({ ratio: 1, timeout: WAIT_MS });
  await captureStructuredState(page, 'attachments-conversation-structured-sent.png');
}

test('drop and paste reach conversation sends in every placement and structured answers', async ({ signalServer, newContext }) => {
  const started = performance.now();
  const server = signalServer;
  const video = readFileSync(createVideoFixture(join(server.root, 'composer-video')));
  const context = await newContext();
  for (const placement of ['dock', 'expanded', 'artifact', 'structured'] as const) {
    const conversation = `attachment-conversation-${placement}`;
    const ticket = await createTicketConversation(server, { conversationId: conversation, title: `Attach in ${placement}` });
    if (placement === 'structured') {
      await post(server, `/api/test/signals/${conversation}/questions`);
      const waiting = await getJson(server, `/api/conversation/conversations/${conversation}`);
      expect(waiting.pending_user_input.questions.find((question: JsonObject) => question.question_id === 'proof').allow_other).toBe(true);
    }
    else await post(server, `/api/test/signals/${conversation}/finish`);
    const link = placement === 'artifact' ? artifact(server, ticket, 'conversation-evidence') : '';
    if (link) await connectedMessage(server, conversation, ticketPrincipal(ticket), link);
    const page = await openPage(context, server, `#/workspace/${ticket}`, '[data-conversation-input]');
    page.setDefaultTimeout(WAIT_MS);
    const ordinarySends: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/conversation/send')) ordinarySends.push(request.url());
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    if (placement !== 'dock') await openConversationLayer(page);
    if (placement === 'structured') {
      await page.locator('[data-user-input-option="Composer"]').click();
      await page.locator('[data-user-input-option="Transcript"]').click();
      await page.locator('[data-conversation-send]').click();
    }
    if (placement === 'artifact') {
      await page.locator('[data-conversation-lens-choice="full"]').click();
      await page.locator('a.file-preview-artifact-link:visible[href*="conversation-evidence.md"]').first().click();
      await expect(page.locator('[data-ticket-artifact]')).toBeVisible();
    }
    const composer = page.locator('[data-conversation-box]');
    const input = composer.locator('[data-conversation-input]');
    await input.fill(placement === 'structured' ? 'The files answer the question.' : `Files from ${placement}`);
    const attachments = await dropAndPaste(composer, input, `conversation-${placement}`, video,
      { kind: 'ticket', id: ticket, expected_conversation_id: conversation });
    if (placement === 'dock') await expect(page.locator('[data-conversation-pane]')).toHaveAttribute('data-conversation-state', 'rest');
    if (placement === 'expanded') await expect(page.locator('[data-conversation-pane]')).toHaveAttribute('data-conversation-state', 'opened');
    if (placement === 'artifact') await expect(page.locator('[data-ticket-artifact]')).toBeVisible();
    if (placement === 'structured') {
      // The later question owns these ready upload identities across a new document.
      const reloadStarted = performance.now();
      await page.reload();
      await page.waitForSelector('[data-conversation-rest-bar]', { timeout: WAIT_MS });
      if (await page.locator('[data-conversation-pane]').getAttribute('data-conversation-state') !== 'opened')
        await openConversationLayer(page);
      await expect(page.locator('[data-user-input-panel]')).toContainText('What proof should be required?');
      await expect(input).toHaveValue('The files answer the question.');
      const restored = composer.locator('.chat-att');
      await expect(restored).toHaveCount(2);
      for (const [index, file] of attachments.entries())
        await expect.poll(async () => (await restored.nth(index).getAttribute('title'))?.split(' · ')[0]).toBe(file.name);
      await composer.scrollIntoViewIfNeeded();
      await settleComposerGeometry(composer);
      await input.scrollIntoViewIfNeeded();
      await settleComposerGeometry(composer);
      await expect(composer.getByText('What proof should be required?', { exact: true })).toBeInViewport({ ratio: 1, timeout: WAIT_MS });
      await expect(input).toBeInViewport({ ratio: 1, timeout: WAIT_MS });
      for (const tile of await restored.all()) await expect(tile).toBeInViewport({ ratio: 1, timeout: WAIT_MS });
      await expect(composer.locator('[data-conversation-send]')).toBeInViewport({ ratio: 1, timeout: WAIT_MS });
      console.log(`Structured answer reload boundary: ${((performance.now() - reloadStarted) / 1000).toFixed(2)}s`);
    }
    if (placement === 'structured') await captureStructuredState(page, 'attachments-conversation-structured.png');
    else await captureSuccess(page, `attachments-conversation-${placement}.png`);
    const response = await sent(page, placement === 'structured' ? '/user-input-answers' : `/api/tickets/${ticket}/conversation/send`, () => composer.locator('[data-conversation-send]').click());
    if (placement === 'structured') {
      const managed = await expectStructuredAnswer(server, conversation, response, attachments, ordinarySends);
      await expect(composer.locator('.chat-att')).toHaveCount(0);
      await expectRecordedStructuredAnswer(page, conversation, attachments, managed);
      expect(ordinarySends).toEqual([]);
    } else {
      expectUploadPieces(response.request().postDataJSON().content, attachments);
      await expectPromptFiles(server, conversation, attachments);
    }
    await page.close();
  }
  console.log(`Conversation attachment placements: ${((performance.now() - started) / 1000).toFixed(2)}s, one existing signal host`);
});

test('drop and paste reach Review replies, artifact bars, structured answers, and the final Chief composer', async ({ signalServer, newContext }) => {
  const started = performance.now();
  const server = signalServer;
  const video = readFileSync(createVideoFixture(join(server.root, 'composer-video')));
  const context = await newContext();
  for (const placement of ['card', 'artifact', 'structured'] as const) {
    const conversation = `attachment-review-${placement}`;
    const ticket = await createTicketConversation(server, { conversationId: conversation, title: `Review reply ${placement}` });
    if (placement === 'structured') await post(server, `/api/test/signals/${conversation}/questions`);
    else await post(server, `/api/test/signals/${conversation}/finish`);
    const text = placement === 'artifact' ? artifact(server, ticket, 'reply-evidence') : 'Please reply with the files.';
    if (placement !== 'structured') await connectedMessage(server, conversation, ticketPrincipal(ticket), text);
    const page = await openPage(context, server, '#/review', placement === 'structured' ? '[data-review-ask-own-words]' : '[data-review-reply]');
    page.setDefaultTimeout(WAIT_MS);
    if (placement === 'artifact') {
      await page.locator('a.file-preview-artifact-link[href*="reply-evidence.md"]').click();
      await expect(page.locator('[data-review-artifact]')).toBeVisible();
    }
    if (placement === 'structured') {
      await page.locator('[data-review-ask-choice="Composer"]').click();
      await page.locator('[data-review-ask-choice="Transcript"]').click();
    }
    const scope = placement === 'artifact' ? page.locator('[data-review-artifact] [data-artifact-bar]')
      : placement === 'structured' ? page.locator('[data-review-ask="question"]') : page.locator('[data-review-item-type="message"]');
    const input = placement === 'structured' ? scope.locator('[data-review-ask-own-words]').last() : scope.locator('[data-review-reply]');
    await input.fill(placement === 'structured' ? 'The files answer the question.' : `Reply from ${placement}`);
    const attachments = await dropAndPaste(scope, input, `review-${placement}`, video,
      { kind: placement === 'structured' ? 'conversation' : 'ticket', id: placement === 'structured' ? conversation : ticket, expected_conversation_id: conversation },
      placement === 'artifact');
    await captureSuccess(page, `attachments-review-${placement}.png`);
    const ordinarySends: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/conversation/send')) ordinarySends.push(request.url());
    });
    const response = await sent(page, placement === 'structured' ? '/user-input-answers' : `/api/tickets/${ticket}/conversation/send`,
      () => scope.locator(placement === 'structured' ? '[data-review-ask-send]' : '[data-review-send]').click());
    if (placement === 'structured') {
      await expectStructuredAnswer(server, conversation, response, attachments, ordinarySends);
      await expect(page.locator('[data-review-ask="question"]')).toHaveCount(0);
    } else {
      expectUploadPieces(response.request().postDataJSON().content, attachments);
      await expectPromptFiles(server, conversation, attachments);
      await expect(page.locator('[data-review-item-type="message"]')).toHaveCount(0);
    }
    await page.close();
  }
  // The queue's final form is another real placement of the shared reply composer.
  const chiefConversation = 'attachment-review-final-chief';
  await post(server, `/api/test/signals/${chiefConversation}/start`);
  await bindConversation(server, 'agent', 'chief_of_staff', chiefConversation);
  await post(server, `/api/test/signals/${chiefConversation}/finish`);
  const page = await openPage(context, server, '#/review', '[data-review-chief-composer]');
  page.setDefaultTimeout(WAIT_MS);
  await expect(page.locator('[data-review-empty]')).toBeVisible();
  const chief = page.locator('[data-review-chief-composer]');
  const input = chief.locator('[data-review-reply]');
  const chiefText = 'Chief, review the attached recording and binary file.';
  await input.fill(chiefText);
  const attachments = await dropAndPaste(chief, input, 'review-final-chief', video,
    { kind: 'chief', id: 'chief', expected_conversation_id: chiefConversation });
  await captureSuccess(page, 'attachments-review-final-chief.png');
  const chiefSends: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/api/chief/conversation/send')) chiefSends.push(request.url());
  });
  const response = await sent(page, '/api/chief/conversation/send', () => chief.locator('[data-review-send]').click());
  expect((await response.json()).fate).toBe('started');
  expectUploadPieces(response.request().postDataJSON().content, attachments);
  await expectPromptFiles(server, chiefConversation, attachments);
  const received: string[] = (await getJson(server, `/api/test/signals/${chiefConversation}/delivered`)).texts;
  expect(received.filter((text) => text.includes(chiefText))).toHaveLength(1);
  expect(chiefSends).toEqual([`${server.base}/api/chief/conversation/send`]);
  await expect(chief.locator('.chat-att')).toHaveCount(0);
  await expect(chief.locator('[role="status"]')).toContainText('Sent to Chief');
  await page.close();
  console.log(`Review reply attachment placements: ${((performance.now() - started) / 1000).toFixed(2)}s, one existing signal host`);
});

test('drop and paste reach approval notes on Ticket and Review, including artifact bars', async ({ railServer, newContext }) => {
  const started = performance.now();
  const server = railServer;
  const video = readFileSync(createVideoFixture(join(server.root, 'composer-video')));
  const context = await newContext();
  const project = await api.post(server, '/api/projects', { name: 'All approval attachment placements' });
  for (const placement of ['ticket', 'ticket-artifact', 'review', 'review-artifact'] as const) {
    const ticket = await api.post(server, '/api/tickets', {
      title: `Approval ${placement}`, worker_type: 'coding', brief: 'Agreed brief.',
      project_id: project.id, manager: { kind: 'chief', id: 'chief' },
    });
    const id = String(ticket.id);
    await api.post(server, `/api/tickets/${id}/accept/brief`, {
      next_ceiling: 'needs_what_changes', next_holder: { kind: 'owner', id: 'owner' },
    });
    const link = placement.endsWith('artifact') ? artifact(server, id, 'approval-evidence') : '';
    await proposeAsWorker(server, id, link || 'A proposal that needs the attached files.');
    const review = placement.startsWith('review');
    const page = await openPage(context, server, review ? '#/review' : `#/workspace/${id}`, '[data-approval-block]');
    page.setDefaultTimeout(WAIT_MS);
    if (link) {
      await page.locator('a.file-preview-artifact-link[href*="approval-evidence.md"]').click();
      await expect(page.locator('[data-ticket-artifact]')).toBeVisible();
    }
    const scope = link ? page.locator('[data-artifact-bar]') : page.locator('[data-approval-block]');
    const input = scope.locator('[data-revision-note], [data-review-revision-input]');
    await input.fill(`Please use the files from ${placement}.`);
    const detail = await api.get(server, `/api/tickets?id=${id}&detail=full`);
    const attachments = await dropAndPaste(scope, input, `approval-${placement}`, video,
      { kind: 'ticket', id, expected_conversation_id: detail.conversation_id });
    await captureSuccess(page, `attachments-approval-${placement}.png`);
    const response = await sent(page, `/api/tickets/${id}/decision-operations/`, () => scope.locator('[data-send-back]').click());
    expect((await response.json()).receipt.outcome).toBe('applied');
    expectUploadPieces(response.request().postDataJSON().attachments, attachments);
    const saved = withDatabase(server, (database) => database.read((tx) => tx.get<{ command_json: string }>(
      'SELECT command_json FROM ticket_decision_operations WHERE ticket_id=?', [id],
    )));
    expectUploadPieces(JSON.parse(saved!.command_json).attachments, attachments);
    await expectPinnedDecisionFiles(server, attachments);
    await page.close();
  }
  console.log(`Approval attachment placements: ${((performance.now() - started) / 1000).toFixed(2)}s, one existing rail host`);
});
