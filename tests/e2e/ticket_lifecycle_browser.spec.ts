/**
 * Prove lifecycle controls across UI, real writers, SSE, and durable history.
 *
 * Mocked frontend responses cannot establish that approval controls, persisted state,
 * and backlog state agree after a durable write and reload. This bounded synthetic
 * proof uses the existing deterministic conversation adapter and no provider calls.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import {
  expectResponse,
  WAIT_MS,
  api,
  bindConversation,
  captureSuccess,
  connectedMessage,
  expect,
  openPage,
  proposeAsWorker,
  startConversation,
  test,
  ticketPrincipal,
} from './support/fixtures.ts';

test('test_ticket_backlog_resume_and_settled_discovery', async ({ railServer, newContext }) => {
  const server = railServer;
  const project = await api.post(server, '/api/projects', { name: 'Lifecycle proof' });
  const item = await api.post(server, '/api/managers', {
    title: 'Synthetic lifecycle review',
    project_id: project.id,
  });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Synthetic ticket with retained evidence',
    worker_type: 'coding',
    brief: 'A synthetic brief for browser verification.',
    manager: { kind: 'manager', id: item.id },
  });
  const ticketId = String(ticket.id);
  const artifactHref = `/files/tickets/${ticketId}/evidence.md`;
  // The server's ticket files folder (PLAN_TICKET_FILES_DIR) is `${root}/files`.
  const artifact = join(server.root, 'files', 'tickets', ticketId, 'evidence.md');
  mkdirSync(dirname(artifact), { recursive: true });
  writeFileSync(artifact, '# Retained evidence\n\nThis file remains available while work is deferred.');
  await api.post(server, `/api/tickets/${ticketId}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  await api.patch(server, `/api/tickets/${ticketId}`, {
    field_values: { brief: `Approved brief with [retained evidence](${artifactHref}).` },
  });
  const proposal = `Pending success evidence survives deferral. [Original artifact](${artifactHref})`;
  await proposeAsWorker(server, ticketId, proposal);
  await startConversation(server, 'synthetic-lifecycle-conversation');
  await bindConversation(server, 'ticket', ticketId, 'synthetic-lifecycle-conversation');

  const transcript = 'Conversation evidence remains readable after deferral.';
  await connectedMessage(server, 'synthetic-lifecycle-conversation', ticketPrincipal(ticketId), transcript);
  const path = `/api/tickets?detail=full&id=${ticketId}`;
  const before = await api.get(server, path);
  const page = await openPage(
    await newContext(),
    server,
    `#/workspace/item/${item.id}/${ticketId}`,
    '[data-ticket-lifecycle]',
  );
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 560, height: 1100 });
  const worker = page.locator('[data-screen="ticket"]');
  await expect(worker.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  await expect(worker.locator('[data-ticket-retire-action]')).toHaveCount(0);
  await expect(worker.locator('[data-ticket-actions]')).toHaveCount(0);
  const backlogPromise = page.waitForResponse((response) => response.url().endsWith(`/${ticketId}/backlog`));
  await worker.locator('[data-ticket-backlog-action]').click();
  const backlogResponse = await backlogPromise;
  await expectResponse(backlogResponse.ok(), true, backlogResponse);
  const deferred = await api.get(server, path);
  expect(deferred.is_backlogged).toBe(true);
  expect(deferred.stage).toEqual(before.stage);
  expect(deferred.pending_proposal).toEqual(before.pending_proposal);
  expect(deferred.conversation_id).toEqual(before.conversation_id);
  await expect(worker.locator('[data-ticket-backlogged]')).toBeVisible();
  await expect(worker.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  await page.reload();
  await expect(worker.locator('[data-ticket-backlogged]')).toBeVisible();
  await expect(worker.getByRole('button', { name: 'Approve', exact: true })).toBeVisible();
  await page.goto(server.base + '/#/backlog');
  await page.waitForFunction("location.hash === '#/workspace'", undefined, { timeout: WAIT_MS });
  await expect(page.locator('[data-backlog-tickets]')).toHaveCount(0);
  await page.goto(server.base + `/#/workspace/item/${item.id}`);
  const children = page.locator(`[data-manager-children="manager:${item.id}"]`);
  const backlogGroup = children.locator('[data-workspace-group="backlog"]');
  await backlogGroup.locator(':scope > summary').click();
  await expect(backlogGroup).toHaveAttribute('open', '');
  const deferredRow = children.locator(`[data-worker-ticket-id="${ticketId}"]`);
  await expect(deferredRow).toBeVisible();
  await expect(deferredRow).toContainText(ticket.title);
  await page.goto(server.base + `/#/workspace/item/${item.id}/${ticketId}`);
  await expect(worker.locator('[data-ticket-backlogged]')).toBeVisible();
  const resumePromise = page.waitForResponse((response) => response.url().endsWith(`/${ticketId}/resume`));
  await worker.locator('[data-ticket-backlog-action]').click();
  const resumeResponse = await resumePromise;
  await expectResponse(resumeResponse.ok(), true, resumeResponse);
  const resumed = await api.get(server, path);
  expect(resumed.is_backlogged).toBe(false);
  expect(resumed.pending_proposal).toEqual(before.pending_proposal);
  await expect(worker.locator('[data-ticket-backlog-action]')).toHaveText('Backlog ticket');
  await page.reload();
  await expect(worker.locator('[data-ticket-backlog-action]')).toHaveText('Backlog ticket');
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  await page.locator('[data-conversation-lens-choice="full"]').click();
  await expect(page.getByText(transcript, { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Close the conversation' }).click();

  // The final decision crosses the actual protected-write and reactive-read boundary.
  await api.post(server, `/api/tickets/${ticketId}/accept/what_changes`, {
    next_ceiling: 'needs_implementation',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  await proposeAsWorker(server, ticketId, 'The implementation is verified; integrate the reviewed result.');
  const finalCard = worker.locator('[data-approval-block][data-field="implementation"]');
  await expect(finalCard).toBeVisible();
  const approve = finalCard.getByRole('button', { name: 'Approve', exact: true });
  await expect(approve).toBeVisible();
  await approve.click();
  const picker = page.locator('[data-ceiling-picker-panel]:visible');
  await picker.locator('[data-ceiling-picker-choice="needs_consequences"]').click();
  const decisionPromise = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().includes('/decision-operations/'),
  );
  await picker.locator('[data-scope-holder="owner"]').click();
  const decisionResponse = await decisionPromise;
  await expectResponse(decisionResponse.ok(), true, decisionResponse);
  const submitted = decisionResponse.request().postDataJSON();
  expect(typeof submitted === 'object' && submitted !== null && !Array.isArray(submitted)).toBe(true);
  expect(submitted.next_ceiling).toBe('needs_consequences');
  expect(submitted.next_holder).toEqual({ kind: 'owner', id: 'owner' });
  const afterApproval = await api.get(server, path);
  expect(afterApproval.stage).toBe('needs_consequences');
  expect(afterApproval.pending_proposal).toBeNull();
  await expect(worker.locator('[data-section-stage="needs_consequences"]')).toBeVisible();
  await expect(finalCard).toHaveCount(0);
  await expect(worker.locator('[data-stage-fold-open]')).toContainText('3 of 4 done');
  await expect(worker).toContainText('At Consequences');
  await expect(worker.locator('[data-leash]')).toContainText('Approved until Consequences');
  await captureSuccess(page, 'consequences-follow-through.png');
  await proposeAsWorker(server, ticketId, 'The approved integration is complete and verified.');
  const reported = await api.get(server, path);
  expect(reported.stage).toBe('needs_consequences');
  expect(reported.pending_proposal.field).toBe('consequences');
  const reportCard = worker.locator('[data-approval-block][data-field="consequences"]');
  await expect(reportCard).toBeVisible();
  const reportPromise = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().includes('/decision-operations/'),
  );
  await reportCard.getByRole('button', { name: 'Approve', exact: true }).click();
  const reportResponse = await reportPromise;
  await expectResponse(reportResponse.ok(), true, reportResponse);
  const finalSubmitted = reportResponse.request().postDataJSON();
  expect(
    typeof finalSubmitted === 'object' && finalSubmitted !== null && !Array.isArray(finalSubmitted),
  ).toBe(true);
  expect(finalSubmitted.field).toBe('consequences');
  expect('next_ceiling' in finalSubmitted).toBe(false);
  expect('next_holder' in finalSubmitted).toBe(false);
  const completed = await api.get(server, path);
  expect(completed.stage).toBe('done');
  expect(completed.pending_proposal).toBeNull();
  await expect(worker.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0);
  await expect(worker.locator('[data-stage-fold-open]')).toContainText('4 of 4 done');
  await worker.locator('[data-stage-fold-open]').click();
  await worker.locator('[data-field="consequences"] > summary').click();
  await expect(worker).toContainText('The approved integration is complete and verified.');
  await captureSuccess(page, 'consequences-done.png');

  await api.patch(server, `/api/managers/${item.id}/settled`, { settled: true });
  await page.goto(server.base + '/#/workspace');
  const settled = page.locator('.settled-head');
  await expect(settled).toBeVisible();
  await settled.click();
  const row = page.locator(`[data-settled-item="${item.id}"]`);
  await expect(row).toBeVisible();
  await row.locator('.settled-title').click();
  await expect(page.locator(`[data-manager-workspace="${item.id}"]`)).toBeVisible();
});
