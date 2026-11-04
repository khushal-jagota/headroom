# Notifications

A notification is a phone or desktop push for a kind of attention Khushal opted into, sent at the moment that attention newly becomes true. Notifications exist so Khushal does not have to keep Headroom open to learn that something is waiting on him.

## What it is made of

- **Subjects.** Tickets, the Chief, Goals, and Managers. Preferences are per subject class: all Managers share one switch per kind, and so do all goals. A Chief preference never changes the matching Ticket preference.
- **Attention flags.** Each subject has separate flags for a message waiting for a reply, an ask waiting for an answer, and a failure. Tickets also have a proposal waiting for approval and a stage assigned to Khushal.
- **Edges.** A flag flipping from false to true is an edge. An edge is the only thing that can notify.
- **The policy.** One decision door that turns an edge into a push or drops it, according to Khushal's preferences.
- **Devices.** Each browser or installed app that enabled notifications is a Web Push subscription. A service worker on the device shows the push and opens the link.
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

The writer records the flag and the edge in the same transaction as the change that caused them. A background pass later turns edges into deliveries, so several commits stay distinct even when one pass sends them. Each device gets its own delivery, retried with backoff. An expired subscription disables that device.

The flags are separate because the events are separate. Held as one flag, a message and an ask that arrived second would raise no edge and reach nobody. An ask awaits an answer only while its turn is open. When the turn ends, its unanswered asks clear, so the next ask can raise a new edge.

A push carries a short title, a body such as "has a message for you", a link, and one replacement tag per subject, so a newer push replaces the older one on the device. It never carries the transcript, the prompt, or the text of an ask. The link opens the Ticket, Chief, goal, or Manager it is about.

Changing a preference affects future edges and does not replay the past.

Headroom can be installed from the browser. Pushes need HTTPS outside localhost, and on iPhone or iPad they work from the installed Home Screen app. The service worker only handles pushes. It does not cache the app, and there is no offline copy.

## Decisions that shape it

- Only edges notify. A flag that stays true does not keep pushing, and a correction never pushes.
- Every push passes the one policy door. Sources never call delivery, and delivery never reads source state.
- The push payload is privacy-safe. What was said stays in Headroom.
- Preferences are by class, not by item, so a new Manager or goal needs no setup.

## Code

- `src/planner/notifications/`: flags and edges (`attention.py`), the policy (`logic/policy.py`), deliveries, preferences, and Web Push.
- `static/service-worker.js`: the service worker.
- `web/src/routes/NotificationsRoute.svelte`: the settings section.

_Last verified: 2026-11-04._
