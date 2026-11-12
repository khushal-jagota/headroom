# Notifications

A notification is a phone or desktop push for a kind of attention Khushal opted into, sent at the moment that attention newly becomes true. Notifications exist so Khushal does not have to keep Headroom open to learn that something is waiting on him.

## What it is made of

- **Subjects.** Tickets, the Chief, Managers, and Panels itself (for the stall alarm). Preferences are per subject class: all Managers share one switch per kind,  A Chief preference never changes the matching Ticket preference.
- **Attention flags.** Each subject has separate flags for a message waiting for a reply, an ask waiting for an answer, and a failure. Tickets also have a proposal waiting for approval and a stage assigned to Khushal.
- **Edges.** A flag flipping from false to true is an edge. Apart from the stall alarm below, an edge is the only thing that can notify.
- **The policy.** One decision door that turns an edge into a push or drops it, according to Khushal's preferences.
- **Devices.** Each browser or installed app that enabled notifications is a Web Push subscription. A service worker on the device shows the push and opens the link.
- **The stall alarm.** Panels itself is a subject with one switch, Stalled work. The stall watchdog unit sends it when work stops moving or the server's stall checker stops; it has no edge and does not pass the policy, but it reads that switch. See [Executor](executor.md) for the watchdog.
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
```

## How it works

The writer records the flag and the edge in the same transaction as the change that caused them. A background pass later turns edges into deliveries, so several commits stay distinct even when one pass sends them. Each device gets its own delivery, retried with backoff. An expired subscription disables that device. Before each send, Headroom checks that the attention is still current and fresh. Stale deliveries expire without clearing the attention that they describe.

The flags are separate because the events are separate. Held as one flag, a message and an ask that arrived second would raise no edge and reach nobody. An ask awaits an answer only while its turn is open. When the turn ends, its unanswered asks clear, so the next ask can raise a new edge.

A push carries a short title, a body such as "has a message for you", a link, and one replacement tag per subject, so a newer push replaces the older one on the device. It never carries the transcript, the prompt, or the text of an ask. The link opens the Ticket, Chief, or Manager it is about.

Changing a preference affects future edges and does not replay the past.

Headroom can be installed from the browser. Pushes need HTTPS outside localhost, and on iPhone or iPad they work from the installed Home Screen app. The service worker only handles pushes. It does not cache the app, and there is no offline copy.

## Decisions that shape it

- Only edges notify. A flag that stays true does not keep pushing, and a correction never pushes.
- Every push passes the one policy door. Sources never call delivery. Delivery checks the retained attention flags.
- The push payload is privacy-safe. What was said stays in Headroom.
- Preferences are by class, not by item, so a new Manager needs no setup.

## Code

- `src/planner/notifications/`: flags and edges (`attention.py`), the policy (`logic/policy.py`), deliveries, preferences, and Web Push.
- `static/service-worker.js`: the service worker.
- `web/src/routes/NotificationsRoute.svelte`: the settings section.

_Last verified: 2026-11-11._


## Native iOS push

The native app uses Apple Push Notification service (APNs). The browser keeps its
existing Web Push path. Each path records delivery separately for the same attention
edge and notification preference. Native retries stop after five minutes or five attempts.
No Apple credentials means no native network calls and no claim of successful delivery.
The native bundle `com.khushaljagota.panels` has Push Notifications enabled under
Blue Door Labs. Release signing requests production APNs.

The live service reads these settings from `~/.config/panels/panels-live.env`.
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

The owner must deploy the server and install the new signed TestFlight build separately.
Check native reads, an explicit owner action, foreground refresh, and conversation output.
Enable notifications, then trigger a permitted attention edge while the app is closed.
Check lock-screen delivery and the destination after a tap. A simulator injection does
not prove Apple delivery, signing, provisioning, or trusted access from the phone.
These physical-device checks need the owner and configured Apple credentials.
