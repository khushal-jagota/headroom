# Ticket page

The ticket page reads the full ticket endpoint and worker-type manifest. The
manifest supplies labels, step ordering, completion counts, approval limits and
the next stage after accepting a proposal. Only the conversation's live working
signal animates a spinner when there is no unread message. The project/type line
and its activity mark sit between Back and actions in the native navigation bar,
with unread taking precedence. The full title scrolls into that bar.

The recap shows eight lines with a fade and expands on tap. Long-press it to copy,
edit or request a fresh recap. Long-press the title to rename it. The action menu
contains recap, backlog/resume and copy-link actions. Blocker links open their
tickets and offer removal in a context menu. Backlog/resume carry the record hash.

The stage card shows the current work, a proposal with approval and send-back
actions, or the current field for reading on a Yours step. Yours means the current
Stage is user-owned; it does not ask the reader to fill in a field. A pending
conversation ask is the separate Needs your answer state. Approval opens a native
sheet; accepting sends the displayed proposal revision and defaults to the next
gate, without advancing twice. A reviewer-only scope change omits the ceiling.
Editors remain open on errors. Send-back requires a nonempty note and an existing
conversation, and is unavailable for Brief. Pristine tickets can edit worker setup
using the existing backend/model catalogue. There are no backend changes.

Steps start collapsed behind a count row matching Artifacts. Tapping the row opens
or closes the list inline with the same disclosure behavior. Step rows push dedicated reading pages using the shared artifact
Markdown content renderer. Managed links use the shared artifact target parser. Complex GFM tables
remain plain text. Completed fields offer Edit/Done. Managed artifact links preserve their actual ticket or manager owner.
The artifact count row opens the shared artifact list; it is hidden when empty.
The conversation slot remains the foundation placeholder, with no transcript or
composer. File inventory refreshes on arrival and pull-to-refresh.

The page uses native pushes and sheets. Recap expansion respects Reduce Motion.
Navigation, disclosure and menus are silent; writes acknowledge success or failure
through the shared haptic helper. Simulator captures cannot verify physical haptics.

## Focused checks

`PanelsTests/TicketTests.swift` reuses the foundation's full-ticket fixture and one
URLProtocol stub. Five small tests add no server or browser host:

- Manifest ordering and renamed labels cover completion counts and approval floors
  that the foundation's decoding tests cannot establish.
- Lifecycle/scope payloads cover record hashes, recap targets and omitted ceilings.
- Editor/proposal payloads cover the new write endpoints, displayed proposal revision,
  unchanged versus edited bodies and explicit null worker defaults.
- Stale-record and refused/uncertain recap outcomes must not appear successful.
- Cross-ticket artifact routes must preserve ownership rather than use this ticket.

The external capture harness is task proof, not a maintained test suite. It uses
real data on the locked PanelsLaneB and exercises only read-only navigation,
expansion and cancellation. Every mutation is tested only against the stub.
