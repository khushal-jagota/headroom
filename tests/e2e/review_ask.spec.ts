/**
 * Prove that an agent's question and permission ask are answered from the Review queue.
 *
 * Component and API tests cannot show that the real navigation projection admits a stopped
 * conversation to the queue, that the browser draws the ask on the right card, and that the
 * answer reaches the backend through the conversation's own routes. One Ticket holds a
 * question beside its proposal, the other a permission ask alone. The scripted backend raises
 * the asks and records what it is answered.
 */
import {
  api,
  bindConversation,
  expect,
  openPage,
  proposeAsWorker,
  test,
  WAIT_MS,
  type ServerHandle,
} from './support/fixtures.ts';
import { post } from './support/conversation.ts';

/** A Ticket waiting on its owner for the Brief's next step, with a started scripted conversation. */
async function ticketWithConversation(server: ServerHandle, projectId: string, title: string, conversationId: string): Promise<string> {
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
  await post(server, `/api/test/signals/${conversationId}/start`);
  await bindConversation(server, 'ticket', id, conversationId);
  return id;
}

test('test_review_answers_a_question_and_a_permission_ask', async ({ signalServer, newContext }) => {
  const server = signalServer;
  const project = await api.post(server, '/api/projects', { name: 'Review ask proof' });
  const questionTicket = await ticketWithConversation(server, String(project.id), 'Answer a question', 'review-ask-question');
  await proposeAsWorker(server, questionTicket, 'The plan waits beside the question.');
  await post(server, '/api/test/signals/review-ask-question/questions');
  const permissionTicket = await ticketWithConversation(server, String(project.id), 'Answer a permission ask', 'review-ask-permission');
  await post(server, '/api/test/signals/review-ask-permission/backend-news', {
    events: [
      {
        kind: 'permission_asked',
        ask_id: 'ask-1',
        title: 'Run the fixture clean-up?',
        detail: 'rm -r tests/fixtures',
        options: [
          { option_id: 'once', label: 'Allow once', option_kind: 'allow_once' },
          { option_id: 'deny', label: 'Deny', option_kind: 'reject_once' },
        ],
      },
    ],
  });

  const page = await openPage(await newContext(), server, '#/review', '[data-review-ticket-card]');
  page.setDefaultTimeout(WAIT_MS);
  await expect(page.locator('[data-review-badge]').first()).toHaveText('2');

  const card = (ticketId: string) => page.locator(`[data-review-ticket-card][data-ticket-id="${ticketId}"]`);
  // Whichever waited longer is first. Each card is answered when it comes up.
  const answerQuestion = async () => {
    const shown = card(questionTicket);
    await expect(shown.locator('[data-review-ask="question"]')).toBeVisible();
    // The proposal stays on the card while the question is open.
    await expect(shown.locator('[data-accept]')).toBeVisible();
    await shown.locator('[data-review-ask-choice="Composer"]').click();
    await shown.locator('[data-review-ask-choice="Transcript"]').click();
    await shown.locator('[data-review-ask-own-words]').last().fill('Recorded replay');
    await shown.locator('[data-review-ask-send]').click();
    await expect(shown.locator('[data-review-ask="question"]')).toHaveCount(0);
    // Answering the question does not decide the proposal: the card stays for it.
    await expect(shown.locator('[data-accept]')).toBeVisible();
    await shown.locator('[data-accept]').click();
    await page.locator('[data-ceiling-picker-choice="needs_implementation"]').click();
    await page.locator('[data-scope-holder]').first().click();
  };
  const answerPermission = async () => {
    const shown = card(permissionTicket);
    await expect(shown.locator('[data-review-ask="permission"]')).toBeVisible();
    await shown.locator('[data-review-ask-option="once"]').click();
  };
  const first = await page.locator('[data-review-ticket-card]').first().getAttribute('data-ticket-id');
  if (first === questionTicket) {
    await answerQuestion();
    await answerPermission();
  } else {
    await answerPermission();
    await answerQuestion();
  }
  await expect(page.locator('[data-review-ticket-card]')).toHaveCount(0);

  const received = await (await fetch(server.base + '/api/test/signals/review-ask-question/answers')).json();
  expect(received).toEqual({
    'input-1': {
      scope: { answers: ['Composer', 'Transcript'] },
      proof: { answers: ['Recorded replay'] },
    },
  });
  const events = (await (await fetch(server.base + '/api/conversation/conversations/review-ask-permission/events')).json()).events;
  expect(events.filter((event: { kind: string }) => event.kind === 'permission_answered').map((event: { payload: { option_id: string } }) => event.payload.option_id)).toEqual(['once']);
});
