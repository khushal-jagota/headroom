/**
 * Prove the shared compact card reaches the real atomic approval writer.
 *
 * Mock browser tests cannot establish that edited text, reviewer and proposal revision
 * survive Ticket/Review's separate request adapters. This isolated real-server proof
 * checks both adapters and a replacement racing the request, without live state or agents.
 */
import type { Route } from '@playwright/test';
import {
  WAIT_MS,
  agentHeaders,
  api,
  expect,
  openPage,
  test,
  type JsonObject,
  type ServerHandle,
} from './support/fixtures.ts';

const BODY = 'The compact approval card preserves the visible proposal.';

async function propose(server: ServerHandle, ticketId: string, body: string): Promise<void> {
  const response = await fetch(server.base + `/api/tickets/${ticketId}/propose`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(await agentHeaders(server, 'worker', ticketId)) },
    body: JSON.stringify({ body, recap: 'Review the same isolated proposal on both hosts.' }),
    signal: AbortSignal.timeout(10_000),
  });
  const text = await response.text();
  expect(response.ok, text).toBe(true);
}

// The Python gate is parametrized over the host; this gate fixes host=ticket.
test('test_shared_compact_card_real_transaction_and_stale_revision', async ({ railServer, newContext }) => {
  const server = railServer;
  const project = await api.post(server, '/api/projects', { name: 'Approval proof' });
  const item = await api.post(server, '/api/managers', {
    title: 'Named Manager must not appear',
    project_id: project.id,
  });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Restore the compact approval card',
    worker_type: 'coding',
    project_id: project.id,
    brief: 'The brief is agreed.',
    manager: { kind: 'manager', id: item.id },
  });
  const ticketId = String(ticket.id);
  const owner = { kind: 'owner', id: 'owner' };
  const manager = { kind: 'manager', id: item.id };
  await api.post(server, `/api/tickets/${ticketId}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: owner,
  });
  await propose(server, ticketId, BODY);
  // host == "ticket": the Ticket screen inside its Manager's workspace.
  const path = `#/workspace/item/${item.id}/${ticketId}`;
  const page = await openPage(await newContext(), server, path, '[data-approval-block]');
  page.setDefaultTimeout(WAIT_MS);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  const card = page.locator('[data-approval-block]');
  const panel = page.locator('[data-ceiling-picker-panel]:visible');
  await page.setViewportSize({ width: 1440, height: 1100 });
  const original = await api.get(server, `/api/tickets?detail=full&id=${ticketId}`);
  // The heading's reviewer gesture performs only a reroute, leaving the proposal parked.
  await card.locator('[data-reviewer-heading]').click();
  const reroutePromise = page.waitForResponse(
    (response) => response.request().method() === 'PATCH' && response.url().endsWith(`/tickets/${ticketId}`),
  );
  await panel.locator(`[data-scope-holder="${item.id}"]`).click();
  const reroute = await reroutePromise;
  expect(reroute.ok(), await reroute.text()).toBe(true);
  const rerouted = await api.get(server, `/api/tickets?detail=full&id=${ticketId}`);
  expect(rerouted.ceiling_holder).toEqual(manager);
  expect(rerouted.stage).toEqual(original.stage);
  expect(rerouted.ceiling).toEqual(original.ceiling);
  expect(rerouted.pending_proposal.body).toBe(BODY);
  expect(rerouted.field_values.what_changes).toBeFalsy();
  // Put the same ask back in the owner's Review queue through the canonical writer.
  await api.patch(server, `/api/tickets/${ticketId}`, { ceiling_holder: owner });
  await page.reload();
  await expect(card).toBeVisible();
  const visible = await api.get(server, `/api/tickets?detail=full&id=${ticketId}`);
  const stalePayloads: JsonObject[] = [];
  const replacement = 'A newer Worker proposal must survive the stale browser request.';

  const replaceBeforeAccept = async (route: Route): Promise<void> => {
    if (route.request().method() === 'POST') {
      const payload = route.request().postDataJSON();
      expect(typeof payload === 'object' && payload !== null && !Array.isArray(payload)).toBe(true);
      stalePayloads.push(payload as JsonObject);
      await propose(server, ticketId, replacement);
    }
    await route.continue();
  };

  const decisionUrl = `${server.base}/api/tickets/${ticketId}/decision-operations/`;
  await page.route(`${decisionUrl}*`, replaceBeforeAccept);
  await card.locator('[data-accept]').click();
  await panel.locator('[data-ceiling-picker-choice="needs_implementation"]').click();
  const stalePromise = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().startsWith(decisionUrl),
  );
  await panel.locator('[data-scope-holder="owner"]').click();
  const stale = await stalePromise;
  await page.unroute(`${decisionUrl}*`, replaceBeforeAccept);
  expect(stale.ok(), await stale.text()).toBe(true);
  const staleResult = await stale.json();
  expect(staleResult.receipt_contract).toBe(1);
  const staleReceipt = staleResult.receipt;
  expect(staleReceipt.operation_id).toBe(stale.url().slice(decisionUrl.length));
  expect(staleReceipt.operation_id).toBeTruthy();
  expect(staleReceipt.ticket_id).toBe(ticketId);
  expect(staleReceipt.decision).toBe('approve');
  expect(staleReceipt.field).toBe('what_changes');
  expect(staleReceipt.expected_proposal_revision).toEqual(visible.pending_proposal.revision);
  expect(staleReceipt.outcome).toBe('refused');
  expect(staleReceipt.transition).toBeNull();
  expect(staleReceipt.error.code).toBe('validation');
  expect(staleReceipt.error.detail).toEqual({ ticket_id: ticketId });
  expect(staleReceipt.error.message).toContain('visible proposal changed');
  expect(stalePayloads[0].decision).toBe('approve');
  expect(stalePayloads[0].field).toBe('what_changes');
  expect(stalePayloads[0].expected_proposal_revision).toEqual(visible.pending_proposal.revision);
  const afterStale = await api.get(server, `/api/tickets?detail=full&id=${ticketId}`);
  expect(afterStale.pending_proposal.body).toBe(replacement);
  for (const field of ['stage', 'ceiling', 'ceiling_holder', 'field_values'])
    expect(afterStale[field], field).toEqual(visible[field]);
  await expect(card.locator('[data-edit]')).toContainText(replacement);
  const edited = 'The owner edited and atomically accepted this exact replacement.';
  await card.locator('[data-edit]').fill(edited);
  await card.locator('[data-edit]').press('Control+Enter');
  await card.locator('[data-accept]').click();
  await panel.locator('[data-ceiling-picker-choice="needs_implementation"]').click();
  const acceptedPromise = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().startsWith(decisionUrl),
  );
  await panel.locator('[data-scope-holder="owner"]').click();
  const accepted = await acceptedPromise;
  expect(accepted.ok(), await accepted.text()).toBe(true);
  const acceptedPayload = accepted.request().postDataJSON();
  expect(
    typeof acceptedPayload === 'object' && acceptedPayload !== null && !Array.isArray(acceptedPayload),
  ).toBe(true);
  expect(acceptedPayload.decision).toBe('approve');
  expect(acceptedPayload.field).toBe('what_changes');
  expect(acceptedPayload.next_ceiling).toBe('needs_implementation');
  expect(acceptedPayload.next_holder).toEqual(owner);
  expect(acceptedPayload.edited_body).toBe(edited);
  const acceptedResult = await accepted.json();
  expect(acceptedResult.receipt_contract).toBe(1);
  const acceptedReceipt = acceptedResult.receipt;
  expect(acceptedReceipt.operation_id).toBe(accepted.url().slice(decisionUrl.length));
  expect(acceptedReceipt.operation_id).toBeTruthy();
  expect(acceptedReceipt.operation_id).not.toBe(staleReceipt.operation_id);
  expect(acceptedReceipt.ticket_id).toBe(ticketId);
  expect(acceptedReceipt.decision).toBe('approve');
  expect(acceptedReceipt.field).toBe('what_changes');
  expect(acceptedReceipt.expected_proposal_revision).toEqual(afterStale.pending_proposal.revision);
  expect(acceptedReceipt.outcome).toBe('applied');
  expect(acceptedReceipt.error).toBeNull();
  expect(acceptedReceipt.transition.pending_proposal_revision).toEqual(
    acceptedReceipt.expected_proposal_revision,
  );
  expect(acceptedPayload.expected_proposal_revision).toEqual(afterStale.pending_proposal.revision);
  const final = await api.get(server, `/api/tickets?detail=full&id=${ticketId}`);
  for (const field of ['stage', 'ceiling', 'ceiling_holder', 'ticket_status'])
    expect(acceptedReceipt.transition[field], field).toEqual(final[field]);
  expect(final.pending_proposal).toBeNull();
  expect(final.field_values.what_changes).toBe(edited);
  expect(final.stage).toBe('needs_implementation');
  expect(final.ceiling).toBe('needs_implementation');
  expect(final.ceiling_holder).toEqual(owner);
  expect(errors).toEqual([]);
});
