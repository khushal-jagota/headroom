/**
 * Browser file events must reach the real submit route from every composer placement.
 *
 * SSR and unit tests cannot prove event bubbling through controls and artifact overlays,
 * browser File reads, or the final connected send. Three cases reuse the existing hosts:
 * conversation placements, Review replies, and approval placements. Structured answers
 * use the existing question cases below, without a new server host or provider process.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Locator, Page, Response } from '@playwright/test';
import {
  api, captureSuccess, connectedMessage, expect, openPage, proposeAsWorker, test,
  ticketPrincipal, WAIT_MS, withDatabase, type JsonObject, type ServerHandle,
} from './support/fixtures.ts';
import { createTicketConversation, getJson, openConversationLayer, post } from './support/conversation.ts';

type Attached = { name: string; text: string };

/** Dispatch native browser file events on a control and the textarea, rather than a picker. */
async function dropAndPaste(scope: Locator, input: Locator, label: string): Promise<Attached[]> {
  const picked = [
    { name: `${label}-drop.md`, text: `# Dropped bytes for ${label}\n` },
    { name: `${label}-paste.txt`, text: `Pasted bytes for ${label}\n` },
  ];
  const surface = input.locator('xpath=ancestor::*[@data-composer-attachment-surface][1]');
  await expect(surface).toHaveCount(1);
  // The control is inside the composer: its bubbled event must reach the shared surface once.
  const control = surface.locator('button').first();
  await control.evaluate((node, attachment) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([attachment.text], attachment.name, { type: 'text/markdown' }));
    for (const name of ['dragenter', 'dragover', 'drop'])
      node.dispatchEvent(new DragEvent(name, { dataTransfer: transfer, bubbles: true, cancelable: true }));
  }, picked[0]);
  await expect(scope.locator('.chat-att.file')).toContainText([picked[0].name]);
  await input.evaluate((node, attachment) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([attachment.text], attachment.name, { type: 'text/plain' }));
    node.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  }, picked[1]);
  await expect(scope.locator('.chat-att.file')).toContainText(picked.map((file) => file.name));
  await expect(input).toBeEnabled();
  return picked;
}

function artifact(server: ServerHandle, ticketId: string, label: string): string {
  const folder = join(dirname(server.dbPath), 'files', 'tickets', ticketId, 'artifacts');
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, `${label}.md`), `# ${label}\n\nThe composer stays under this artifact.\n`);
  return `[${label}](/files/tickets/${ticketId}/artifacts/${label}.md)`;
}

/** Check uploaded bytes, and not just the preview labels. */
function expectUploadPieces(pieces: JsonObject[], attached: Attached[]): void {
  const files = pieces.filter((piece) => piece.piece === 'file');
  expect(files.map((file) => file.file_name)).toEqual(attached.map((file) => file.name));
  for (let index = 0; index < attached.length; index++)
    expect(Buffer.from(files[index].data, 'base64').toString('utf8')).toBe(attached[index].text);
}

/** The real core must keep each file once and serve the exact accepted bytes. */
async function expectStoredPieces(server: ServerHandle, conversation: string, pieces: JsonObject[], attached: Attached[]): Promise<void> {
  const files = pieces.filter((piece) => piece.piece === 'file' || piece.file_name);
  expect(files.map((file) => file.file_name)).toEqual(attached.map((file) => file.name));
  for (let index = 0; index < attached.length; index++) {
    const response = await fetch(`${server.base}/api/conversation/conversations/${conversation}/files/${files[index].stored_file_id}`);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe(attached[index].text);
  }
}

async function expectPromptFiles(server: ServerHandle, conversation: string, attached: Attached[]): Promise<void> {
  const events: JsonObject[] = (await getJson(server, `/api/conversation/conversations/${conversation}/events`)).events;
  const matching = events.filter((event) => event.kind === 'prompt' &&
    (event.payload.content ?? []).some((piece: JsonObject) => piece.file_name === attached[0].name));
  expect(matching).toHaveLength(1);
  await expectStoredPieces(server, conversation, matching[0].payload.content, attached);
}

async function sent(page: Page, path: string, action: () => Promise<void>): Promise<Response> {
  const written = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().includes(path));
  await action();
  const response = await written;
  expect(response.ok(), await response.text()).toBe(true);
  return response;
}

async function expectStructuredAnswer(server: ServerHandle, conversation: string, response: Response, attachments: Attached[], ordinarySends: string[]): Promise<void> {
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
}

test('drop and paste reach conversation sends in every placement and structured answers', async ({ signalServer, newContext }) => {
  const started = performance.now();
  const server = signalServer;
  const context = await newContext();
  for (const placement of ['dock', 'expanded', 'artifact', 'structured'] as const) {
    const conversation = `attachment-conversation-${placement}`;
    const ticket = await createTicketConversation(server, { conversationId: conversation, title: `Attach in ${placement}` });
    if (placement === 'structured') await post(server, `/api/test/signals/${conversation}/questions`);
    else await post(server, `/api/test/signals/${conversation}/finish`);
    const link = placement === 'artifact' ? artifact(server, ticket, 'conversation-evidence') : '';
    if (link) await connectedMessage(server, conversation, ticketPrincipal(ticket), link);
    const page = await openPage(context, server, `#/workspace/${ticket}`, '[data-conversation-input]');
    page.setDefaultTimeout(WAIT_MS);
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
    const attachments = await dropAndPaste(composer, input, `conversation-${placement}`);
    if (placement === 'dock') await expect(page.locator('[data-conversation-pane]')).toHaveAttribute('data-conversation-state', 'rest');
    if (placement === 'expanded') await expect(page.locator('[data-conversation-pane]')).toHaveAttribute('data-conversation-state', 'opened');
    if (placement === 'artifact') await expect(page.locator('[data-ticket-artifact]')).toBeVisible();
    await captureSuccess(page, `attachments-conversation-${placement}.png`);
    const ordinarySends: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/conversation/send')) ordinarySends.push(request.url());
    });
    const response = await sent(page, placement === 'structured' ? '/user-input-answers' : `/api/tickets/${ticket}/conversation/send`, () => composer.locator('[data-conversation-send]').click());
    if (placement === 'structured') {
      await expectStructuredAnswer(server, conversation, response, attachments, ordinarySends);
      await expect(composer.locator('.chat-att')).toHaveCount(0);
    } else {
      expectUploadPieces(response.request().postDataJSON().content, attachments);
      await expectPromptFiles(server, conversation, attachments);
    }
    await page.close();
  }
  console.log(`Conversation attachment placements: ${((performance.now() - started) / 1000).toFixed(2)}s, one existing signal host`);
});

test('drop and paste reach Review replies, artifact bars, and structured answers', async ({ signalServer, newContext }) => {
  const started = performance.now();
  const server = signalServer;
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
    const attachments = await dropAndPaste(scope, input, `review-${placement}`);
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
  console.log(`Review reply attachment placements: ${((performance.now() - started) / 1000).toFixed(2)}s, one existing signal host`);
});

test('drop and paste reach approval notes on Ticket and Review, including artifact bars', async ({ railServer, newContext }) => {
  const started = performance.now();
  const server = railServer;
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
    const attachments = await dropAndPaste(scope, input, `approval-${placement}`);
    await captureSuccess(page, `attachments-approval-${placement}.png`);
    const response = await sent(page, `/api/tickets/${id}/decision-operations/`, () => scope.locator('[data-send-back]').click());
    expect((await response.json()).receipt.outcome).toBe('applied');
    expectUploadPieces(response.request().postDataJSON().attachments, attachments);
    const saved = withDatabase(server, (database) => database.read((tx) => tx.get<{ command_json: string }>(
      'SELECT command_json FROM ticket_decision_operations WHERE ticket_id=?', [id],
    )));
    expectUploadPieces(JSON.parse(saved!.command_json).attachments, attachments);
    await page.close();
  }
  console.log(`Approval attachment placements: ${((performance.now() - started) / 1000).toFixed(2)}s, one existing rail host`);
});
