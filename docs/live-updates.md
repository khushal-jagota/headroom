# Live screens

An open Headroom screen moves when the record moves, without a manual refresh. This page covers how the browser and the phone read, how they write, how they learn about changes, and what a busy mark means. The server never pushes a copy of the changed data inside the wake-up. It says only that something was written, and each screen re-reads what it shows.

## What it is made of

Three channels bring data to the screen, and the live ones share one WebSocket. Each one fits a different shape of data.

- **Ordinary reads.** Ticket pages, settings, goals and Manager pages. The app asks for a body and keeps it in a cache. A body and its revision enter the cache together, from one read.
- **The navigation index.** The rail, Review membership and order, attention marks, and masthead signals are a derived index with its own feed. The app requests the scope that it shows and follows the snapshots and changes for it. The sidebar asks for Ticket rows from every Manager on the rail, so offscreen cards retain their rows. The sidebar briefly keeps an existing Ticket in its previous Agent or approval group during an Empty handoff.
- **The conversation tail.** Each open conversation pane has its own feed of the running turn's text and new rows.

Two things tell the ordinary reads that the record moved.

- **The change signal.** Every ordinary database commit announces that something was written. The announcement names no record and carries no data.
- **The revision check.** The app asks the server for the revisions of the bodies on screen, and reads only the bodies whose revision moved. Each domain registers its revisions. A read with no registered revision is read again on each check.

```
 commit ──► change signal ──► revision check ──► read the changed bodies
   │
   ├──────► navigation feed ──► rail, Review, marks
   │
 running turn ──► conversation tail ──► open pane
```

## The rules

These rules hold for all three channels.

1. **A write ends when the server answers.** A busy mark means that the server did not answer yet, and nothing else. When the answer arrives, the control is free.
2. **After its own write, the app starts the revision check at once and does not wait for it.** It is the same check that any change causes. A control that shows its result from a read waits a short, limited time for it, so that the old state does not flash. A page editor takes the document revision for its next edit from the server's answer to its own write, so that edit does not wait for a read.
3. **Ordinary reads have one refresh path.** An own write, the change signal, a reconnect and a return to the tab all run the revision check. No code refreshes everything.
4. **Every request has a time limit.** A read that passes its limit shows its error line above the data that the screen already has. A write that passes its limit says that the server did not answer. That is not a refusal, because the server can still save the write, and the check that follows shows what happened.
5. **A failure stays where it is.** One slow or failed read affects only the part of the screen that shows it. It cannot hold a control, stop the revision check, or make the page read everything again.

## How it behaves

Passive wakes are gathered into short bursts, so a busy agent's stream of commits does not stampede the browser. A hidden tab reads nothing, and its return runs one revision check. Nothing shows while the first socket opens, because that is a start. After that, a dropped socket shows "Can't reach Panels" and dims the screen at once. The page reopens the socket after one second, or after three seconds if the last socket never became ready. It also asks the server at once, and the card ends when the new socket is ready. If the operating system reports no network, it says "This Mac is offline" instead. The server sends a heartbeat frame every 15 seconds, and a socket that sends no frame for 2.5 times that is dropped and reopened. The reconnect runs the check. Drafts and outgoing messages live outside the cache, so a re-read never overwrites what the person types. A sent message that got no answer says so on its bubble and offers to send the same words again.

Model pickers follow revisions of their saved source facts for the selected machine. A revision check calls no machine. A picker body and its revision come from the same saved state, so its own read cannot cause repeated refreshes. [Usage](usage.md) describes discovery and explicit Refresh.

The server looks for navigation index changes on a short timer, so the rail can follow a write by a moment. The conversation tail does not wait for the change signal.

The server counts open live streams for each declared client identity, and counts open live sockets the same way, one for each socket. An unusually high count produces one warning with totals by route. The warning resets after the count falls. This diagnostic does not refuse or end streams.

Each live stream also leaves one record when it closes: the route, the client kind and device, when it opened and closed, the bytes written, the time since the last write, and who closed it. The closer is the peer (including a reset), the server's own shutdown, a write error, or the route itself when it ended the stream. A live socket writes the same record. It adds the WebSocket close code and the last sequence number that the server sent. The server prints one `stream_close` journal line and stores the record in the metrics store for 30 days. `panels connection records` reads them back with the events that clients report to `POST /api/client-events`, so a drop at a given minute has a recorded cause. See [Diagnostics](vps.md).

The page keeps a small log of why its live socket ended: the time, the reason (a close, silence or a skipped sequence number), the WebSocket close code, the last sequence number that arrived, how long it was open, whether the browser was online, whether the page was visible, and how late a 1 s timer ran (a late timer shows sleep). The desktop app cannot see why Chromium closed a page socket, for example `ERR_NETWORK_CHANGED`, so the close code, the online flag and the timer drift carry the explanation. The probe that a lost stream starts adds its round-trip time. The log lives in local storage and holds the newest 200 events. When the socket is ready, the page sends the log to the server in one background batch at `POST /api/client-events`, and nothing on screen waits for it. A refused or failed send keeps the events for the next return.

A few writers commit quietly on purpose: skill history, conversation-only rows, and machine receipts for transient output. The conversation tail delivers that output after acceptance. Shared conversation effects retain the global change signal.

Deployment status and the "this app is older than the server" prompt ride the same wake, then make a small status read. See [Deployment](deployment.md).

The phone follows the same rules, with one known gap: a Ticket action there still waits for a few shell reads before its busy mark ends. It publishes successful reads before replaceable disk writes, and a cache failure does not replace a durable-save error. It saves an outgoing message on the device first and sends it from there, so a send never waits for the network.

## The live socket

One authenticated WebSocket, `/api/live`, carries the four live feeds: the change signal, the navigation feed, the conversation view tail and the conversation tail. A socket frame wraps each payload in an envelope with its subscription, its kind and a sequence number. The browser and the desktop app use the socket. The server also keeps the old event streams, which the phone still uses until it moves. The frame names, the close codes and the limits are in `server/live_updates/contracts.ts`.

Why: macOS Chromium closes every HTTP/2 connection when a network interface address changes, and four open streams then die together. A WebSocket runs on its own HTTP/1.1 connection through `tailscale serve` and survived the same event in the Mac 1 proof.

- **One owner in the page.** `web/src/lib/liveSocket.ts` opens the socket, says hello, checks the sequence, watches for silence and routes each frame to its subscriber. In a browser the page uses its own origin. In the desktop app the page asks the native side for the server address and the app's identity (bridge 3), because a socket cannot pass through the `panels://` relay. Each consumer keeps its own recovery: after a drop it is told, and it subscribes again.
- **Admission.** The Tailscale login at the upgrade is the gate. A present Origin must be the web origin or `panels://app`. A socket with no Origin is a native client. The first frame declares the client's identity in the `X-Panels-Client` shape, because a browser cannot set that header. The identity is a version declaration and gives no authority. The server answers with a ready frame, or closes the socket.
- **Three named closes.** "Update needed" (the existing minimum desktop bridge or iPhone build), "server restarting" and "bad first frame". Every other close is a drop. On "update needed" the page shows the existing update prompt and does not reopen the socket. Every other close shows the card and reopens it.
- **Feeds.** Every socket receives the change signal after its ready frame. A client opens a navigation view or a conversation tail with a subscribe frame that carries today's query parameters, and ends it with an unsubscribe frame. A subscription that fails ends by itself with the status that the old route would send, and the socket stays open.
- **Sequence.** One counter for each socket, starting at 1. A skipped number means a drop. It is not a resume cursor.
- **After a drop.** Each subscription returns as its stream does today. The conversation tails resume from their own cursors. The change signal and navigation start fresh, and the client runs its revision check. The server adds no replay.
- **Silence.** The server sends a heartbeat frame every 15 seconds and pings clients that it cannot hear. Clients send no pings. A socket that falls too far behind is closed as a drop.
- **The desktop notification stream stays outside the socket on purpose.** It runs in the desktop main process over Node's own `fetch`, which did not drop in any test. Moving it would change the native side for no measured problem.

## What does not move by itself

Inactive ordinary responses remain only within the cache budget. A screen reconciles its resource when it opens. A file written straight into a Ticket's artifact folder has no database revision, so the artifact list refreshes when that Ticket is selected, not because a commit named the file. Each artifact list request reads one folder, so its cost follows that folder and not the whole Ticket folder. A folder sorts by its own modified time. The folder's revision changes when an entry in that folder changes.

## Decisions that shape it

- The signal carries no payload. A screen always shows what a fresh read returned, never a guess built from an event.
- No domain code calls a wake. The database connection announces its own commits, so a new writer cannot forget to.
- The signal is best-effort. The database and periodic loops remain the answer, so a missed announcement delays an update and never loses a write.
- No write lists the reads that it affects. A hand-kept list of that kind was removed because it could not be kept correct. The revision check finds what changed.
- All ordinary browser writes go through one door, so the rules for a write are stated once.
- Message text comes from the conversation when a card or pane opens. The index stays small enough to keep honest without loading every conversation.
- The browser uses server messages directly. TypeScript describes those messages during development without runtime description tables.
- The Ticket screen displays loaded detail and Worker configuration. The server decides whether saves and approvals are valid.

## Code

- `server/core/changes/`: the signal. `server/core/database/`: the writer that announces commits, and the silent commit. `server/core/routes.ts`: the change stream.
- `server/resource_versions/`: the revisions that the check reads.
- `server/navigation/`: the navigation index and its feed.
- `server/live_updates/`: the socket, its contract, and the frame types that the feeds share with the old streams.
- `web/src/lib/mutate.ts`: the browser's write door.
- `web/src/lib/liveSocket.ts`: the browser's one socket. `web/src/lib/changeStream.ts` and `web/src/lib/resourceVersions/`: the change signal's status card and the revision check.
- `web/src/lib/clientIdentity.ts` and `web/src/lib/requestLimit/`: the browser's fetch boundary and its time limits.
- `ios/Panels/Core/Networking/ResourceOwner.swift`: the phone's ordinary resources, active demand, and revision admission.
- `web/src/lib/conversation/view/`: the conversation tail reader.


An owner update pauses the whole app before the server closes send admission. The pause remains until the server admits writes and compatible screens open. A write that the server explicitly refuses before admission retains its input through screen replacement. A lost reply never triggers an automatic retry. Conversation drafts keep their own storage. Other edited text remains readable after replacement for review, rather than a synthetic edit that can trigger an unintended save. Desktop recovery belongs to the native shell, so failed screens cannot hide its retry action.

_Last verified: 2026-11-28._
