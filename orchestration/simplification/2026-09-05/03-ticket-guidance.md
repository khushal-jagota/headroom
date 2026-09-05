# One ticket guidance document

Status: reviewed design accepted; contract preparation before dispatch.

## Intent and evidence

Keep durable user corrections and constraints. Stop making the user or Worker pick
a workflow field merely to save guidance. The live read-only snapshot has 778
Tickets, 642 nonempty notes across 387 Tickets. Samples include specific user
corrections, scope boundaries, links, and some agent bookkeeping. Counts do not prove
value; the direct corrections do. All existing text is preserved, including notes
on historical or terminal stages. Maximum combined note text is about 29k characters.

Kickoff remains the approved request. Stage values remain agreed results. Recap
remains short current orientation. Guidance is the single durable instruction
document. Stage ownership, scope, approval, and all conversation/backend code stay
unchanged. This ticket does not relitigate the proposal representation.

## Contract decision

- `Ticket.guidance: str` (empty by default) replaces `FieldSlot.user_note`.
- `FieldSlot` contains only value and proposal. No legacy read aliases remain.
- `PUT /tickets/{id}/guidance` replaces the document; JSON `{body: string}`.
- `POST /tickets/{id}/guidance/append` appends nonempty text with one blank line;
  read and append are one transaction. Empty append is a no-op. Both use existing
  Worker/direct authority rules and current Ticket ownership checks.
- Delete `/notes/{field}` and `/notes/{field}/append` routes and their aliases.
- `panels worker note <id> [--append]` reads stdin and writes the document directly.
  There is no field argument, worker-type lookup, file-path option, or client field
  validation. Keep `note` as the familiar command; its help names Ticket guidance.
- Ticket JSON, copy text, search, supervisor context, and CLI Ticket record reads
  include guidance. CLI record parts include `guidance` and `recap` explicitly;
  default reads remain manifests; explicit part reads carry content. No change to
  the generic manifest-first interface.
- Frontend `TicketField` loses user_note; `TicketDetail` gains guidance. Ticket view
  has one guidance disclosure/editor near the written record, and each stage loses
  its Notes disclosure. Review shows the Ticket guidance once when present.

## Stored content and delivery

A new migration adds `tickets.guidance TEXT NOT NULL DEFAULT ''`, folds nonempty
notes from the existing fields map in its recorded order under `## <field id>`
headings, and removes user_note and the earlier notes key from each slot. If both
keys contain nonempty text, preserve both under source-labelled subsections in
user_note, then notes order, even if their content is equal. A null user_note must
not mask earlier notes content. Preserve
exact note bytes inside each section; do not strip, deduplicate, reinterpret, or
auto-approve text. Preserve unknown historical field keys as well. Parse/validate
all rows before writing so corrupt content aborts without a partial transformation.
No mutable registry imports in migration. A populated migration test proves content,
field values, proposals, identity, placement and conversation links survive.

A database edit is not model context. The Ticket's worker-step prompt includes its
current guidance, so the next automatic Worker step receives it as real prompt
text. Guidance writes keep the existing generic Ticket-changed notice for direct
edits. There is no new pending-guidance key. Return-for-revision and ordinary chat
retain their existing delivery behavior; saving guidance is not an immediate chat
intervention. Paired/user-led conversations can read the current document through
their ordinary Ticket reads. Do not modify conversation send functions or the
context-service implementation. This preserves the prior per-field-note behavior
outside automatic Worker steps and avoids stale queued copies of guidance.

## Scope and minimum gates

Root changes contracts before implementation. The implementation ticket will own
Ticket contract consumers: tickets data/api/views/logic, pure runtime worker-step
prompt, relevant CLI record projection and worker
note command, Ticket/Review presentation, skill/docs guidance, one migration and
focused behavioral tests. Conversation packages, frontend conversation directories,
agent_backends and generic conversation/context-service implementation are protected.

Focused proof: populated migration; guidance replace/append/clear round trip;
rejection leaves state unchanged; approval/revision leaves guidance intact; capture
the actual sent prompt with exact guidance and receipts retained on refused send;
CLI stdin reaches correct route in one write; frontend single editor and Review
guidance read. Existing generic context delivery proof can be reused where it
already exercises the real send boundary. No E2E is needed: pure prompt, writer,
API, and frontend tests can prove these changes.

## Review

Independent reviewer accepted the revised design in 00-review-first-chunks.md.
Findings addressed: manifest-first CLI reads retained; both legacy note keys are
preserved; no unsupported claim that normal chat sends inject saved guidance; no
new pending-guidance state or stale-guidance race. No unresolved design findings.
