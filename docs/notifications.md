# Notifications

A notification is a phone or desktop push for a kind of attention Khushal opted into, fired when that attention newly becomes true. Headroom can be installed from the browser. The service worker delivers pushes. It does not cache the app, and there is no offline copy.

## What can become a push

Each subject — a Ticket, Chief, or Managers as one group — has attention flags. A flag that flips from false to true is an edge. An edge is the only thing that can notify. The writer records the flag and the edge in the same transaction as the change that caused them, so several commits stay distinct even when one later poll delivers them.

The flags are split because they are different events. A message waiting for a reply and a Worker waiting on a permission or input ask used to be easy to collapse. Whichever arrived second then changed nothing and reached nobody. They are separate. A proposal waiting on Khushal, a stage becoming his to fill, and a failure are separate again. Approval and stage-assignment exist on Tickets only. Chief and Managers offer reply, answer, and error.

All ordinary Managers share one subject. A preference is about Managers as a class. A Chief preference never changes the matching Ticket preference.

The push itself carries a privacy-safe title, body, link, and a replacement tag. It does not carry the transcript, the prompt, or the permission text. One tag per subject means a newer notification replaces the older one at the operating system. The server owns the catalogue of choices. The Notifications screen renders that catalogue. It does not invent a parallel list.

A permission or input request awaits an answer only while its turn is open. The turn ending clears unanswered requests from that turn, so the next request can raise a new edge. Unread messages and failures stay their own flags.

Changing a preference affects future edges. It does not replay the past. Startup reconciliation can clear a stale “needs an answer” when the notebook shows the turn already ended. That correction does not raise an edge and does not replay missed history.

When a parked review moves to an agent holder, Headroom attempts a courtesy message in that holder’s conversation. Khushal does not get an arrival message when a review moves to him. That courtesy is not a new push type. If the send fails, the courtesy can be lost. The proposal itself remains parked.

## How a device is enabled

Delivery is Web Push. At the moment an edge is accepted, Headroom writes a delivery row per enabled device and retries with backoff. An expired subscription disables that device.

Production pushes need HTTPS. Localhost is the development exception. On iPhone or iPad, add the site to the Home Screen, open that installed app, then enable notifications for this device. The screen still says “Add Panels to the Home Screen,” which is the old product name in the current copy. Permission requested in a browser tab on iOS does not stick the way the installed app does.

## What feeds pushes, and where they land

Ticket, conversation, and proposal writes feed the flags. Preferences feed the policy that turns an edge into a push or drops it. A Ticket notification opens that Ticket in the Workspace. A Manager notification opens that Manager. A Chief notification opens Chief. Older addresses from before those routes moved still redirect. A malformed payload opens the Workspace.

The Notifications section is Settings → Notifications, `#/settings/notifications`. The shell does not have a separate notifications destination anymore. `#/notifications` redirects there.

## Pointers

`src/planner/notifications/` and `web/src/routes/NotificationsRoute.svelte`. The service worker is `static/service-worker.js`.

_Last verified: 2026-10-30._
