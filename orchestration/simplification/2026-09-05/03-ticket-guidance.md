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

## Implementation and focused evidence

Implemented on `codex/panels-simplification-guidance` against root's contract commit
`6b2b8f1a`. The migration follows `planning_day_direction` in this isolated branch;
root will sequence it after `sprint_documents` during serial integration.

The replacement removes six note-writer names, both field-specific HTTP routes,
field/dual-key selection, stage Notes editors, and the base Worker's manual
specialist list. Ticket guidance is exposed by detail/worker reads, supervisor
context (through its existing Ticket detail producer), copy text, search, CLI
explicit parts, and the current automatic step prompt. The generic context service
and every conversation/backend/send implementation are unchanged.

Migration evidence covers two nonempty sources with equal content, null-masked
legacy notes, whitespace-only content, unknown historical fields/metadata, values,
proposals, unchanged Ticket columns/timestamps, populated Project/Sprint/Item/Day
placement, and conversation links. Corrupt content in a later row leaves the parent
schema and every earlier row intact. Startup after migration is unchanged.

Focused Python cohorts:

- `test_ticket_guidance_migration`, `test_tickets_engine`, `test_worker_context`,
  `test_worker_step_readiness_loop`, `test_type_driven_ingress`,
  `test_cli_entrypoints`, `test_cli_record_projection`, `test_cli_verbs_in_process`,
  `test_copy_text_type_driven`, `test_value_edit_logic`, `test_value_edit_api`,
  `test_bounded_list_reads`, and `test_worker_type_persistence`: 178 cases exercised.
  176 passed initially; two CLI test expectations were corrected (projection helper
  returns header/parts; Click's option-error punctuation), and both passed in the
  targeted repair run.
- Direct shape fallout: `test_db_ticket_status_changed_at`,
  `test_engine_parameterization`, `test_generic_field_storage`,
  `test_readiness_wake_actions`, `test_worker_cli_identity`, and
  `test_worker_type_stage_contracts`, plus the two CLI repairs: 58 cases exercised.
  57 passed initially; the current-schema HEAD assertion was updated and passed in
  the targeted repair run. Unrelated historical migration HEAD assertions are left
  for the integration/pruning owner.
- Final changed-proof run: both populated/corrupt migration cases, approval and
  return-for-revision preserving guidance, and the repaired HEAD assertion: all
  five passed. Existing refused-send proof retains pending-context receipts, while
  the amended opener proof asserts exact guidance in actual backend-bound text.

`node web/tests/ticket-guidance-browser.test.mjs` passed. It mounts the actual
Ticket route and Review card against HTTP fixtures: one guidance editor, no stage
Notes disclosures, one body-only PUT, and the saved guidance rendered once in
Review. This is frontend testing with mocked HTTP, not a live-backend E2E. Its
script is included in the existing frontend legacy test gate. Initial fixture
omissions were repaired before the passing run; production behavior was not
changed to accommodate the fixture.

`npm run check` passed with zero Svelte errors or warnings. Frontend test fixture
`tsc --project web/tests/tsconfig.json --noEmit` passed after adding required
`guidance` to the lifecycle fixture. The voice-everywhere fixture received only the
same Ticket JSON shape correction. Ruff on changed Python and new migration/tests
passed, `mypy src/planner` passed across 227 source files, and `git diff --check`
passed. No `./verify`, production build, or `web/dist` write was performed.

Additional direct-consumer scope: current field JSON fixtures in the named suites;
`docs/frontend.md`, `docs/worker-types.md`; new-worker and planning-sprint skill
references to the removed manual catalogue/field notes; frontend lifecycle and
voice fixture shapes; one frontend test script registration. No unrelated tests
were pruned. The former live codec legacy-note assertion is replaced by the
populated historical migration proof, because there is no live legacy-read alias.

Status: implementation and focused gates complete; independent diff review and
root integration pending. Repository completeness remains the program's one final
canonical `./verify`.
