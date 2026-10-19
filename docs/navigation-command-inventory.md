# Navigation command responses

`navigation/commands.py` lists each command that may change a navigation source
fact. The integration test compares this inventory and its named exclusions with
all registered application write routes. Adding a write route requires a decision
about its navigation effect; the HTTP verb alone does not make that decision.

The covered families are Ticket creation, metadata, proposals, approvals and
lifecycle; collection membership; Projects and orchestrators; Chief, Ticket and
orchestrator conversation sends and resets; addressed messages, including the
owner's request for a fresh recap, which empties the published recap it replaces;
conversation
creation, owner read acknowledgment, interruption, termination, held prompt
removal and promotion, permission and input answers; Worker definitions,
requirements and launch defaults. The current application has no separate HTTP
error acknowledgment route. An owner read or admitted send follows the existing
read and error acknowledgment rules.

Skill text, managed files, feedback, ideas, schedules, notifications, backend
catalogues, login and voice transcription do not change the current navigation
record contract. They have explicit exclusions and reasons. Worker launch defaults
and requirements remain in the command inventory even when a particular update
changes no currently published value. A fully captured operation with no relevant
commit receives an empty witness list.

The browser mutation callers were audited in `TicketRoute`, `ReviewProposalCard`,
`TicketLifecycleControls`, `ChiefConversation`, `SprintItemWorkspace`, `BoardRoute`,
`ConfigRoute`, `IdeasRoute`, `ScheduledTasksRoute`, `NotificationsRoute`,
`FeedbackCapture`, `FeedbackRoute`, `App`, and `lib/conversation/wire.ts` and
`voiceCapture.ts`. They require no changes: existing response bodies, receipts,
authorization and HTTP statuses retain their meanings.

Successful responses add `Panels-Navigation-Fence`, a compact JSON version 1
fence, and `Panels-Navigation-Coverage`. The proof contract defines the fence's
workspace, schema, semantics, epoch, decimal revision, opaque witnesses and
signature. `proof_limits.py` bounds witness count and encoded bytes. A capture or
encoding failure reports unknown coverage and preserves the command result. It
does not ask the client to retry an already accepted action. Error responses have
no successful command fence.

Request capture stays on the ASGI request task. Only explicitly awaited
conversation settlement and storage tasks join it. The storage writer explicitly
carries that authorization into its thread. Detached backend pumps, background
polling and inherited background thread contexts cannot contribute witnesses.
Closing the response headers detaches capture. Every successful source commit
has independent evidence, including quiet conversation commits and separate
connections; a later unrelated write cannot stand in for a missing command commit.

Publication starts after the real conversation runtime acquires and reconciles
its exclusive lease, Ticket and skill audits finish, and the notification loop
reconciles imported state. Prepared polling loops have not started yet. A single
fresh epoch commits before publication and background tasks are admitted. An
injected test conversation facade never supplies that lease. Failed startup
reconciliation fails closed and releases its resources. Shutdown closes publication
before stopping background work and releasing conversation ownership.
