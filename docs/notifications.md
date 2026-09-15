# Notifications

Panels can be installed from the browser as a small home-screen or desktop app. It
uses Web Push, so a selected event can reach an enabled phone or Mac while the Panels
page is closed. The service worker does not cache pages or provide an offline copy of
Panels.

## What becomes a notification

Notification sources do not send messages. They first write a small, normalized fact:
the notification type, time, and a subject principal. The principal uses the same kind
and ID contract as requests and messages. One policy function is the only door
from that fact to a notification intent. It reads the saved choice for that type and
either suppresses the fact or creates the privacy-safe title, body, exact subject link,
and replacement tag.

This chokepoint is intentional. The source adapters, user choices, wording, and delivery
provider do not decide independently what counts. The server owns one catalogue, and
the Notifications screen renders its subject groups and choices directly. It offers
four choices for Tickets:

- an unread addressed message, input request, or permission request awaits a reply;
- an owner-held Ticket proposal awaits approval;
- a Ticket stage becomes assigned to Khushal; and
- a worker turn fails or a Ticket errors.

Chief of Staff and Sprint Item supervisors each offer awaiting reply and errored.
Approval and assignment are Ticket-only facts. Each saved choice uses its subject and
notification type as one key, so a Chief choice never changes the matching Ticket choice.

Sprint Item supervisors share one subject between all of them, because a sprint holds
twenty or thirty Items and they are replaced each sprint. Upgrades preserve the old
disabled supervisor-error preference while adding the reply choice.

The notification contains no transcript, prompt, permission detail, or worker output.
Opening a Ticket notification goes to `/#/workspace/<ticket-id>`. A Sprint Item
notification goes to `/#/workspace/item/<item-id>`. A notification stored
before Tickets moved to the Workspace still carries the old `/#/ticket/<ticket-id>`
address, which the app redirects. A Chief notification
uses the retained `/#/agents/chief-of-staff` address, which the app redirects to
`/#/workspace/chief-of-staff`. The service worker accepts only those subject links. A
malformed payload or an unsupported link opens the safe Workspace fallback. All
notifications for one subject use one stable replacement tag, so overlapping facts
coalesce at the operating system.

## Durable delivery

Panels derives four current attention flags for each subject. It stores each flag state
and emits one fact only when that flag changes from false to true. Several messages before
one read therefore cause one reply notification. A later message causes another notification
only after the first reply state clears. The migration seeds all current flag states and
advances legacy-help cursors, so an upgrade does not replay old work as new.
Every fact gets one durable policy decision. An allowed fact creates one
delivery row per device that was registered at that time.

Notification reads never change attention state. Reads and owner replies do not clear a
failed agent state; only a successful start or explicit restart does.

The single-machine runtime loop owns projection, policy, and delivery. A database
change wakes it promptly, and a periodic pass covers missed wake-ups and retries.
Successful deliveries are recorded. Temporary failures back off and retry. A push
provider's expired-subscription response disables that device.

The Web Push signing identity, subscriptions, preferences, facts, decisions, intents,
and delivery results all live in the main SQLite database. Normal database backup and
restore therefore preserve the identity that existing devices trust.

## Enabling a device

Open **More → Notifications**. Choose the kinds of event that count for Tickets and the
Chief of Staff, then press
**Enable on this device**. Panels asks the browser for notification permission only at
that point. On iPhone and iPad, first add Panels to the Home Screen and open that
installed app. Web Push also requires Panels to be served over HTTPS in production
(`localhost` is the browser's development exception).

Disabling a device removes its subscription from Panels and asks the browser to
unsubscribe. Changing a “What counts” switch affects later facts; it does not resurrect
events that were suppressed earlier.

Code paths: `src/planner/notifications/`, the notification database migrations,
`static/service-worker.js`, and
`web/src/routes/NotificationsRoute.svelte`.

_Last verified: 2026-09-15._
