# Live screens

An open Headroom screen moves when the record moves, without a manual refresh. Lists, counts, the Review badge, rail order, and an open conversation catch up on their own while the tab is visible. The server never pushes a copy of the changed data inside the wake-up. It says only that something was written, and each screen re-reads what it shows.

## What it is made of

- **The change signal.** Every ordinary database commit announces that something was written. The announcement names no record and carries no data.
- **The change stream.** The browser hears the signal over one stream. It asks which of the resources mounted on screen have a new revision, and refetches only those. A body and its revision enter the browser's cache together, from one read.
- **The navigation index.** The rail, Review membership and order, attention marks, and masthead signals are a derived index with its own feed. The browser requests the mounted scope and follows its snapshots and changes. The signal speeds that feed up, and polling backs it.
- **The conversation tail.** Each open conversation pane has its own stream of the running turn's text and new rows. It does not wait for the shared signal.

```
 commit ──► change signal ──► change stream ──► revision check ──► refetch changed bodies
                  │
                  └──────────► navigation feed ──► rail, Review, marks

 running turn ──► conversation tail ──► open pane
```

## How it behaves

After the person acts, the reads that action touched refresh at once. Passive wakes are gathered into short bursts, so a busy agent's stream of commits does not stampede the browser. A hidden tab waits until it is visible again. A dropped connection shows Reconnecting on the shell status, and the reconnect reconciles. Drafts and outgoing messages live outside the cache, so a refetch never overwrites what the person is typing.

A few writers commit quietly on purpose: skill history, and conversation rows that only an open pane reads, which that pane already receives through its tail.

Deployment status and the "this app is older than the server" prompt ride the same wake, then make a small status read. See [Deployment](deployment.md).

## What does not move by itself

A screen that is not mounted keeps its last body until it opens. A file written straight into a Ticket's artifact folder has no database revision, so the artifact list refreshes when that Ticket is selected, not because a commit named the file.

## Decisions that shape it

- The signal carries no payload. A screen always shows what a fresh read returned, never a guess built from an event.
- No domain code calls a wake. The database connection announces its own commits, so a new writer cannot forget to.
- The signal is best-effort. The database and periodic loops remain the answer, so a missed announcement delays an update and never loses a write.
- Message text comes from the conversation when a card or pane opens. The index stays small enough to keep honest without loading every conversation.
- The browser uses server messages directly. TypeScript describes those messages during development without runtime description tables.
- The Ticket screen displays loaded detail and Worker configuration. The server decides whether saves and approvals are valid.

## Code

- `src/planner/core/change_signal.py`: the signal. `src/planner/core/db.py`: the connection that announces commits, and the quiet commit. `src/planner/core/sse.py`: the change stream.
- `src/planner/navigation/`: the navigation index and its feed.
- `web/src/lib/changeStream.ts` and `web/src/lib/resourceVersions/`: the browser's stream and revision check.
- `web/src/lib/conversation/view/`: the conversation tail reader.

_Last verified: 2026-11-04._
