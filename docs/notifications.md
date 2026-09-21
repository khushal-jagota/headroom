# Notifications

Panels can be installed from the browser as a small home-screen or desktop app. It
uses Web Push, so a selected event can reach an enabled phone or Mac while the Panels
page is closed. The service worker does not cache pages or provide an offline copy of
Panels.

## What becomes a notification

Panels derives four attention flags for each subject: a reply is waiting, a proposal is
waiting for approval, a stage is assigned to Khushal, and something failed. A flag that
turns from false to true is an edge, and an edge is the only thing that can become a
notification. The writer records the flag and the edge in its own transaction, so several
commits stay distinct even when one wake-up covers them.

One policy function is the only door from an edge to a notification. It reads the saved
choice for that type and either suppresses the edge or creates the privacy-safe title,
body, exact subject link, and replacement tag. The subject uses the same kind and ID
contract as requests and messages.

This chokepoint is intentional. The source adapters, user choices, wording, and delivery
provider do not decide independently what counts. The server owns one catalogue, and
the Notifications screen renders its subject groups and choices directly. It offers
four choices for Tickets:

- an unread addressed message, input request, or permission request awaits a reply;
- an owner-held Ticket proposal awaits approval;
- a Ticket stage becomes assigned to Khushal; and
- a worker turn fails or a Ticket errors.

Chief of Staff and Sprint Item supervisors each offer awaiting reply and errored.
Approval and assignment belong to Tickets only. Each saved choice uses its subject and
notification type as one key, so a Chief choice never changes the matching Ticket choice.

Sprint Item supervisors share one subject between all of them, because a sprint holds
twenty or thirty Items and they are replaced each sprint. Upgrades preserve the old
disabled supervisor-error preference while adding the reply choice.

The notification contains no transcript, prompt, permission detail, or worker output.
Opening a Ticket notification goes to `/#/workspace/<ticket-id>`. A Sprint Item
notification goes to `/#/workspace/item/<item-id>`. A notification sent
before Tickets moved to the Workspace still carries the old `/#/ticket/<ticket-id>`
address, which the app redirects. A Chief notification
uses the retained `/#/agents/chief-of-staff` address, which the app redirects to
`/#/workspace/chief-of-staff`. The service worker accepts only those subject links. A
malformed payload or an unsupported link opens the safe Workspace fallback. All
notifications for one subject use one stable replacement tag, so overlapping
notifications coalesce at the operating system.

## Durable delivery

Several messages before one read cause one reply notification. A later message causes
another notification only after the first reply state clears.

Every edge is decided once. An allowed edge creates one delivery row per device that was
registered at that time, holding the words that were sent. A suppressed edge is decided
too, and leaves nothing behind. The delivery log is therefore the whole record of what
Panels sent, and Panels keeps no notification history besides it.

Notification reads never change attention state. Reads and owner replies do not clear a
failed agent state; only a successful start or explicit restart does.

The single-machine runtime loop owns the queue step and delivery. A database
change wakes it promptly, and a periodic pass covers missed wake-ups and retries.
Successful deliveries are recorded. Temporary failures back off and retry. A push
provider's expired-subscription response disables that device.

The Web Push signing identity, subscriptions, preferences, attention state, edges, and
delivery results all live in the main SQLite database. Normal database backup and
restore therefore preserve the identity that existing devices trust.

## Enabling a device

Open **More → Notifications**. Choose the kinds of event that count for Tickets and the
Chief of Staff, then press
**Enable on this device**. Panels asks the browser for notification permission only at
that point. On iPhone and iPad, first add Panels to the Home Screen and open that
installed app. Web Push also requires Panels to be served over HTTPS in production
(`localhost` is the browser's development exception).

Disabling a device removes its subscription from Panels and asks the browser to
unsubscribe. Changing a “What counts” switch affects later edges; it does not resurrect
events that were suppressed earlier.

Code paths: `src/planner/notifications/`, the notification tables in the baseline revision,
`static/service-worker.js`, and
`web/src/routes/NotificationsRoute.svelte`.

_Last verified: 2026-09-20._
