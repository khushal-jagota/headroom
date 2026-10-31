# Live screens

An open Headroom screen is supposed to move when the record moves, without a manual refresh and without the server pushing a copy of the new data inside the wake-up. The person sees lists, counts, the Review badge, rail order, and conversation tails catch up while the tab is visible and the connection is up. After the person themselves acts, the affected reads refresh immediately. Passive wakes are coalesced, so a burst of commits from a running agent does not stampede the browser.

## How a wake becomes a new body

Every normal database commit announces that something was written. The announcement has no entity name and no payload. The browser listens on `GET /api/changes`. It does not apply the event as data. It asks which mounted resources have a new revision, and it refetches only those. A body and its version enter the cache together from one read.

Navigation is the other half, for the surfaces that are derived indexes rather than raw records: the Workspace rail, Review membership and order, attention marks, masthead signals. Those indexes update so a list can be honest without loading every conversation body into it. Message text still comes from the conversation when a card or a pane opens. Skip and return on Review are visit state on top of that index.

Drafts, outgoing messages, and the text the person is currently composing sit outside the bounded cache. A hidden tab defers its refetch until it is visible again. A dropped connection shows Reconnecting on the shell status, and a reconnect reconciles.

A few writers stay silent on purpose. Managed skill history, and the conversation rows that only an open conversation is reading, do not need to wake every screen. The open pane is handed its tail directly.

## What does not move by itself

A screen that is not mounted keeps its last body until it is opened. A file written straight to the Ticket artifact tree has no database revision, so the artifact inventory refreshes when that Ticket is the one selected, not because a commit named the file. Unknown resources and failed version probes keep an explicit refresh rather than pretending the cache is current.

The change signal is best-effort. The database and the periodic loops remain canonical. A missed announcement delays an update. It does not lose the write.

## Where it shows up

The shell status pill is the visible connection. Review’s count, the rail’s grouping, and a Ticket masthead’s working or unread mark are the derived surfaces. The conversation pane is the direct tail. Deployment status and an “the app you loaded is older than the server” refresh prompt travel the same wake, then a small status read. They are described in [Updates](updates.md).

## Pointers

`src/planner/core/change_signal.py` announces commits. `src/planner/core/db.py` is the connection door, including the quiet commit used where a screen is not waiting. `src/planner/core/sse.py` is the browser stream. The client probe lives under `web/src/lib/resourceVersions/`. The navigation index is `src/planner/navigation/`.

_Last verified: 2026-10-30._
