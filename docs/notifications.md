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
the Notifications screen renders its subject groups and choices directly. Today it
offers five choices for Tickets:

- a Ticket needs approval;
- a Ticket worker needs input;
- a worker requests permission;
- a worker turn completes; and
- a worker turn fails or a Ticket errors.

Chief of Staff has four separate choices: needs input, permission request, worker
completed, and worker failed. Needs approval is Ticket-only because it comes from a
Ticket status. Each saved choice uses its subject and notification type as one key, so a
Chief choice never changes the matching Ticket choice.

Sprint Item supervisors share one subject between all of them, because a sprint holds
twenty or thirty Items and they are replaced each sprint. It offers one choice: worker
failed, and it arrives switched off. Nothing an Item conversation does reaches the user
by itself, and the user is there for the turns it takes, because a message of theirs is
the only thing that starts one.

The notification contains no transcript, prompt, permission detail, or worker output.
Opening a Ticket notification goes to `/#/workspace/<ticket-id>`. A notification stored
before Tickets moved to the Workspace still carries the old `/#/ticket/<ticket-id>`
address, which the app redirects. A Chief notification
uses the retained `/#/agents/chief-of-staff` address, which the app redirects to
`/#/workspace/chief-of-staff`. The service worker accepts only those subject links. A
malformed payload or an unsupported link opens the safe Workspace fallback. All
notifications for one subject use one stable replacement tag, so overlapping facts
coalesce at the operating system.

## Durable delivery

Panels projects new Ticket status revisions and events from Ticket, Chief, and supervisor
conversations into facts. Permission asks, requests for user input,
and completed or failed turns all use the same projection and policy path. A cursor per
source makes that projection restart-safe and prevents old history from being treated as new after
an upgrade. Every fact gets one durable policy decision. An allowed fact creates one
delivery row per device that was registered at that time.

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
