/**
 * Prove the approval note takes pictures and files in a real browser and carries them to the server.
 *
 * Gap: unit tests cannot pick, paste or store files in browser storage, or post them through the
 * real decision route. This one test sends a note back with a picked picture and a pasted file.
 * It reads the decision command the server saved, which is what the Worker's next prompt is built from.
 */
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { WAIT_MS, api, expect, openPage, proposeAsWorker, test, withDatabase } from './support/fixtures.ts';

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

test('the approval note sends a picked picture and a pasted file back with Send it back', async ({ railServer, newContext }) => {
  const server = railServer;
  const project = await api.post(server, '/api/projects', { name: 'Approval attachments' });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Send back with a screenshot',
    worker_type: 'coding',
    brief: 'Agreed brief.',
    project_id: project.id,
    manager: { kind: 'chief', id: 'chief' },
  });
  const ticketId = String(ticket.id);
  await api.post(server, `/api/tickets/${ticketId}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  await proposeAsWorker(server, ticketId, 'A proposal that needs a screenshot to explain the change.');

  const page = await openPage(await newContext(), server, `#/workspace/${ticketId}`, '[data-approval-block]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1200, height: 900 });
  const card = page.locator('[data-approval-block]');
  await card.locator('[data-revision-note]').fill('The header is wrong, see the screenshot.');
  await card.locator('input[type=file]').setInputFiles({ name: 'header.png', mimeType: 'image/png', buffer: PNG });
  await expect(card.locator('[data-chat-image-preview], .chat-att.img')).toHaveCount(1);
  await card.locator('[data-revision-note]').evaluate((field) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(['# Notes\n'], 'notes.md', { type: 'text/markdown' }));
    field.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }));
  });
  await expect(card.locator('.chat-att.file')).toContainText('notes.md');

  const evidence = process.env.PANELS_TICKET_EVIDENCE_DIR;
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    await card.screenshot({ path: join(evidence, 'approval-note-with-attachments.png') });
  }
  const sent = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().includes(`/api/tickets/${ticketId}/decision-operations/`),
  );
  await card.locator('[data-send-back]').click();
  const response = await sent;
  expect((await response.json()).receipt.outcome).toBe('applied');
  const attachments = response.request().postDataJSON().attachments;
  expect(attachments.map((piece: { piece: string; file_name: string }) => [piece.piece, piece.file_name])).toEqual([
    ['image', 'header.png'],
    ['file', 'notes.md'],
  ]);

  const saved = withDatabase(server, (db) =>
    db.read((tx) =>
      tx.get<{ command_json: string }>('SELECT command_json FROM ticket_decision_operations WHERE ticket_id=?', [ticketId]),
    ),
  );
  expect(JSON.parse(saved!.command_json).attachments).toHaveLength(2);
  await expect(card.locator('.chat-att')).toHaveCount(0);
});
