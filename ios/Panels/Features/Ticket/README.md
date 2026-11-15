# Ticket page

The Ticket page reads its full detail and saved workflow. Older records use the
worker-type manifest. The workflow supplies stage names, completion counts,
approval limits, and the next stage. The parent name precedes the read-only title.
The recap shows three lines, with Show more and Show less. Its context menu keeps
Copy, Edit, and Ask for a fresh recap. The action menu keeps recap, backlog/resume,
and copy-link actions. Blocker links keep their existing removal action.

Pending proposals use the same recessed card and decision controls as Review.
The note and actions stay above the conversation line. Approve opens How far,
then Who reviews. The final stage approves with one tap. The approver control opens
Who reviews directly and changes only the reviewer. Earlier or current How far
choices change scope and leave the proposal waiting. Scope and reviewer changes
send one online-only PATCH, never an Outbox operation. The choice stays visible
when offline. An uncertain answer triggers a fresh Ticket read, which compares
the absolute ceiling and reviewer. It never triggers an automatic retry. The sheet
states that nothing was approved. A further check reads settings without another
PATCH. These changes have no durable operation receipt. Send it back requires a
nonempty note and an existing conversation, and does not appear for Brief.
An empty note focuses the note field. The Ticket has no microphone in this area.

A note belongs to the exact server, Ticket, proposal field, creation time, and
revision. It survives redraws and relaunch. Approval captures the visible note
before its sheet opens. A newer draft stays separate from that snapshot. Both
decisions use the existing protected Outbox operation. Saved and uncertain decisions
stay locked. Refusals stay visible, and a changed proposal needs a fresh read.
Proposal text is selectable and read-only. The phone never submits edited proposal
text. Existing saved operations and their retention rules stay intact.

Otherwise the stage card shows the stage and its approval limit. The approval-limit
sheet retains its existing behavior. Done shows only Done. User-owned field values
remain readable. Worker setup, Artifacts, and the conversation line keep their
existing actions.

Steps start collapsed behind a completed-count row. An approved step opens its
read-only value inline in a recessed well. Values use the shared Markdown renderer,
including horizontally scrollable code and tables. The phone has no step editor
or Ticket rename action. Managed artifact links keep their actual owner and use
the existing in-app viewer.

## Focused checks

`PanelsTests/TicketTests.swift` uses the existing full-ticket fixture and URLProtocol
stub. It covers workflow ordering, approval limits, retained narrow edit payloads,
record conflicts, recap delivery, and artifact ownership. The note-state case
covers the new split between the scrolling proposal and pinned controls: exact
proposal restoration and a frozen note snapshot with a newer local draft. It
requires no server or browser host. Review decision tests cover protected operation
bytes and receipts. The one-off Simulator review provides appearance and interaction
evidence without a permanent layout test. Simulator captures do not prove physical
haptics or live-server behavior.
