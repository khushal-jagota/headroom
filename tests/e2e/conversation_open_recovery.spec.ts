/**
 * A conversation that could not open at first says nothing once it has.
 *
 * The risk is the note "Conversation disconnected" staying in the composer after the reader's
 * own retry has drawn the transcript, which makes a healthy conversation look broken. Only a
 * real live socket against a refusing server shows it: while the server upgrades a stored view it refuses
 * the tail subscription. A note that came from something else, such as
 * a refused stop, is not the open's to clear.
 */
import { expect, test, WAIT_MS } from './support/fixtures.ts';
import { createTicketConversation, openConversationLayer, post } from './support/conversation.ts';

for (const laterNoteDuringOutage of [false, true]) {
  test(`the disconnected note leaves when the open recovers${laterNoteDuringOutage ? ', but a later refusal stays' : ''}`, async ({ signalServer, newContext }) => {
    const context = await newContext({ viewport: { width: 1280, height: 800 } });
    const id = `open-recovery-${laterNoteDuringOutage}`;
    const ticket = await createTicketConversation(signalServer, { conversationId: id, title: 'Open recovery' });
    await post(signalServer, `/api/test/signals/${id}/message`, { kind: 'ticket', id: ticket, label: 'Open recovery', text: 'A message from the record.' });
    // The turn stays running, so the composer offers Stop; the other case lets it finish.
    if (!laterNoteDuringOutage) await post(signalServer, `/api/test/signals/${id}/finish`);

    const page = await context.newPage();
    let upgrading = true;
    // The page's socket carries the view tail. While `upgrading`, answer its subscribe with a refusal
    // instead of passing it on, and number the frames that follow so that the sequence stays whole.
    await page.routeWebSocket(/\/api\/live$/, (socket) => {
      const server = socket.connectToServer();
      let injected = 0;
      let last = 0;
      server.onMessage((message) => {
        const frame = JSON.parse(String(message));
        frame.seq += injected;
        last = frame.seq;
        socket.send(JSON.stringify(frame));
      });
      socket.onMessage((message) => {
        const frame = JSON.parse(String(message));
        if (upgrading && frame.op === 'subscribe' && frame.kind === 'conversation_view') {
          injected += 1;
          socket.send(JSON.stringify({ seq: ++last, sub: frame.id, kind: 'ended', data: { status: 503, detail: 'The tail was refused.' } }));
          return;
        }
        server.send(message);
      });
    });
    await page.route('**/interrupt', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"The stop was refused."}' }));
    await page.goto(`${signalServer.base}/#/workspace/${ticket}`);
    await page.waitForSelector(`[data-screen="ticket"][data-ticket-id="${ticket}"]`, { timeout: WAIT_MS });
    await openConversationLayer(page);

    const note = page.locator('[data-conversation-error]');
    await expect(note).toContainText('Conversation disconnected', { timeout: WAIT_MS });
    await expect(page.locator('[data-conversation-connection]')).toHaveCount(1);
    if (laterNoteDuringOutage) {
      // Stop is refused while the tail is still down: a note the open did not leave.
      await page.locator('[data-conversation-stop]').click({ timeout: WAIT_MS });
      await expect(note).toContainText('The stop was refused.', { timeout: WAIT_MS });
    }

    upgrading = false;
    await expect(page.getByText('A message from the record.')).toBeVisible({ timeout: WAIT_MS });
    await expect(page.locator('[data-conversation-connection]')).toHaveCount(0);
    if (laterNoteDuringOutage) await expect(note).toContainText('The stop was refused.');
    else await expect(note).toHaveCount(0);
  });
}
