# Notifications

A notification is a phone or desktop push for a kind of attention the owner selected, sent at the moment that attention newly becomes true. Notifications exist so the owner can see when something needs attention while Headroom is closed.

## What it is made of

- **Subjects.** Tickets, the Chief, Managers, and Panels itself (for the stall alarm). Preferences are per subject class: all Managers share one switch per kind,  A Chief preference never changes the matching Ticket preference.
- **Attention flags.** Each subject has separate flags for a message waiting for a reply, an ask waiting for an answer, and a failure. Tickets also have a proposal waiting for approval and a stage assigned to the owner.
- **Edges.** A flag flipping from false to true is an edge. Apart from the Panels alarms below, an edge is the only thing that can notify.
- **The policy.** One decision door that turns an edge into a push or drops it, according to the owner's preferences.
- **Devices.** Each browser or installed app that enabled notifications is a Web Push subscription. A service worker on the device shows the push and opens the link. The iPhone app uses APNs. A running desktop app holds a live stream instead; see [Desktop app](#desktop-app).
- **Panels alarms.** Panels itself is a subject with two switches, Stalled work and Backup failed. The stall watchdog unit sends the first when work stops moving or the server's stall checker stops. A backup sends the second when its copy of the database fails SQLite's integrity check, in the nightly backup or the update snapshot. Both run outside the server, so they have no edge and do not pass the policy, but each reads its switch. Each also messages the Chief, when the server is up. See [Executor](executor.md) for the watchdog and [Backups](backups.md).
- **The settings section.** Notifications is a section of Settings. It renders the server's catalogue of choices and does not keep its own list.

```
 Ticket, conversation, and proposal writes
                 │
                 ▼
 attention flag rises ──► edge (same transaction)
                                │
                                ▼
                     policy (preferences) ──► drop
                                │
                                ▼
                  one delivery per enabled device ──► Web Push ──► service worker
                                │                  └──► APNs ──► iPhone app
                                └──► each open desktop stream ──► Mac app
```

## How it works

The writer records the flag and the edge in the same transaction as the change that caused them. A background pass later turns edges into deliveries, so several commits stay distinct even when one pass sends them. Each device gets its own delivery, retried with backoff. An expired subscription disables that device. Before each send, Headroom checks that the attention is still current and fresh. Stale deliveries expire without clearing the attention that they describe.

Backlog suppresses Ticket approval, assignment, and pending-answer alerts, including queued delivery. Resume restores eligibility for the current obligation. Unread-message and failure alerts still describe real events.

The flags are separate because the events are separate. Held as one flag, a message and an ask that arrived second would raise no edge and reach nobody. An ask awaits an answer only while its turn is open. When the turn ends, its unanswered asks clear, so the next ask can raise a new edge.

A message push names its sender and shows a short plain-text preview. An approval push names the Ticket and the stage that waits. Both native and browser push use these words. Text limits preserve complete visible characters. The source write saves this context with its edge, so later messages or stage changes do not replace it. If context creation fails, the core write still commits and the alert uses generic text. A push also carries a link and one replacement tag per subject. A newer push replaces the older one on the device. The link opens the Ticket, Chief, or Manager it is about.

The browser and iPhone Settings change the same preferences. A failed save keeps the confirmed value and shows an error. A preference change affects future edges and does not replay the past.

Headroom can be installed from the browser. Pushes need HTTPS outside localhost, and on iPhone or iPad they work from the installed Home Screen app. The service worker only handles pushes. It does not cache the app, and there is no offline copy.

## Decisions that shape it

- Only edges notify, apart from the Panels alarms. A flag that stays true does not keep pushing, and a correction never pushes.
- Every push passes the one policy door. Sources never call delivery. Delivery checks the retained attention flags.
- Push providers and the lock screen receive the bounded message preview. The full transcript, prompt, attachment contents, and text of asks stay in Panels. System notification preview controls determine what the locked device displays.
- Preferences are by class, not by item, so a new Manager needs no setup.

## Code

- `server/notifications/`: flags and edges (`attention.ts`), the policy (`logic/policy.ts`), deliveries, preferences, Web Push, APNs, and the desktop streams (`desktop_streams.ts`).
- `static/service-worker.js`: the service worker.
- `web/src/components/settings/NotificationSettings.svelte`: the settings section.

_Last verified: 2026-11-22._


## Desktop app

The desktop app holds one live stream, `GET /api/notifications/desktop`, while it runs. Only the owner can open it. After the delivery pass decides an edge, every open stream gets the same title, preview, link and tag that the other paths get, if the attention is still current and fresh. The app's native side shows it through macOS, and a click opens the link in the app. Nothing is stored or retried for the desktop: a notification decided while no app is connected reaches the other devices only. See [The desktop app](desktop.md).

## Native iOS push

The native app uses Apple Push Notification service (APNs). Press and hold a message notification to reply after device authentication. The app saves that reply in its ordinary outbox, addressed to the original conversation. The outbox sends it again with the same identity until the server answers. The action can launch the app in the background without an extension. The system permits one bounded delivery attempt, and pending work resumes when the app next opens. The browser keeps its
existing Web Push path. Each path records delivery separately for the same attention
edge and notification preference. Native retries stop after five minutes or five attempts.
Native push for other homes is deferred. The stock app does not require the owner to provision Apple credentials.

## Publisher configuration for iOS push

The native bundle `com.khushaljagota.panels` has Push Notifications enabled under
Blue Door Labs. Release signing requests production APNs.

The publisher’s live service reads these settings from `~/.config/panels/panels-live.env`.
Its launcher passes them to the server:

- `PLAN_APNS_KEY_PATH`: the private `.p8` key file, outside source control.
- `PLAN_APNS_KEY_ID`: the Apple key ID.
- `PLAN_APNS_TEAM_ID`: the Apple developer team ID.
- `PLAN_APNS_BUNDLE_IDS`: the allowed bundle IDs, separated by commas.

Use a canonical HTTPS origin and the configured Tailscale ingress. Device registration
requires the owner identity from that ingress. A token or client identity header is not
a login. `PUT /api/notifications/apns/devices` registers the device token, bundle ID, and
`development` or `production` environment. `DELETE` revokes the same registration even
after the operator removes the key. Tokens never appear in adapter logs.

For TestFlight, use an Apple developer membership, an App Store Connect app with the
matching bundle ID, push capability, and the production APNs environment. Release builds for physical iPhones enable server registration by default.
Debug and Simulator builds keep it disabled unless explicitly enabled.
The `.p8` key stays on the server. It does not belong in the app or its build inputs.
See [the native setup guide](../ios/NOTIFICATIONS.md) for private key setup, app settings, and the physical delivery check.

For the publisher’s native push check, deploy the server and install the signed TestFlight build separately.
Check native reads, an explicit owner action, foreground refresh, and conversation output.
Enable notifications, then trigger a permitted attention edge while the app is closed.
Check lock-screen delivery and the destination after a tap. A simulator injection does
not prove Apple delivery, signing, provisioning, or trusted access from the phone.
These physical-device checks need the owner and configured Apple credentials.
