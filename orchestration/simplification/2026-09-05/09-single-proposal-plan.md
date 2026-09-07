# One pending result per Ticket

Status: candidate contract and implementation plan, for root contract ownership and independent review. No implementation is authorized by this document alone. Base: `d55260b3`. The root owns the final contract; implementation agents work only against its reviewed shapes and assigned file groups.

## Product decision and evidence

A Ticket has one current result awaiting agreement. Its passed fields are saved documents. Older unapproved text remains readable history, without retaining approval machinery.

Today every field contains a saved value and a separate proposal. The worker CLI already proposes the current gate, but the arbitrary-field HTTP route permits other fields below the ceiling: `admission.check_agent_proposal` returns before checking the field there. Review, Board and readiness inspect only the current gated field. The broader storage model therefore supports pending items the ordinary approval walk cannot represent.

The private live snapshot `data/simplification-deeper/live-tickets.json` contains 778 Tickets. A read-only aggregate against the configured definitions found 39 Tickets with proposals, none with multiple proposals, 33 at `awaiting_approval`, and zero awaiting approval without a current-gate proposal. Three proposals are off the current gate: `t_vbk63kc1` is general/dropped, `t_d1prmbs8` is planning-day/needs_review, and `t_uj554kys` is planning-day/done; all three have status `empty` and an old Kickoff proposal. This API export contains the declared-field projection; it cannot establish the absence of additional unknown historical slots in raw SQLite. No proposal text or user content is copied into this tracked document. These examples establish that archival preservation is required. They do not justify retaining arbitrary-field approval.

The normal worker workflow remains: submit the current result with a recap; scope and ownership either accept it or park it; the human edits, approves, or sends it back. The material change is that there is exactly one live proposal and one approval surface. A passed or future field cannot carry a second approval. The human can open Historical record to read removed unapproved text, without mistaking it for a settled result or current request.

Coding stages, Worker definitions, ownership semantics, Stop/Propose, exact external-work reconciliation, and real delivery behavior are unchanged. Collapsing coding planning stages and replacing external-work prefix reconstruction are separate possible programs. The claim-release identity change to status revision is also separate and is not included here.

## Contract owned by the root

Change `src/planner/tickets/contracts.py` first. Use these names in storage, Python and frontend contracts:

```python
TicketFieldValues = Mapping[str, str]

@dataclass(frozen=True)
class PendingTicketProposal:
    field: str
    body: str
    proposed_by: str
    created_at: int

# Relevant Ticket members; all unrelated members retain their current contract.
field_values: TicketFieldValues
pending_proposal: PendingTicketProposal | None
archived_field_content: str
```

`field_values` contains only fields with a saved string; an absent key means unset. A saved empty string remains a string, and whitespace is never stripped. There is no null-filled map. The Worker definition supplies field order and labels. Central field access validates that the requested field is declared before reading `field_values.get(field)`; an unknown field is an error and a declared missing field is unset. Copy mappings when producing pure decisions; do not mutate the incoming Ticket. A simple read-only mapping helper may enforce the canonical-writer boundary without a new wrapper class. Delete `FieldSlot`, `TicketFields`, their copy-on-write slot vocabulary and the old `Proposal` type after all consumers use the new contract. The explicit proposal field records what is under review and supports a stale-field check; it is not permission to propose arbitrary fields.

Persist `field_values` as a JSON text column replacing `fields`, `pending_proposal` as nullable JSON text, and `archived_field_content` as non-null text with empty default. Do not keep a nested-field compatibility projection. Ticket JSON exposes these same members. Frontend `TicketField` and nested-slot shapes are replaced by the corresponding flat value map and `PendingTicketProposal`; Board's existing `has_pending_proposal` projection remains a boolean.

`archived_field_content` is one Markdown document. It is not Guidance, an agent context queue, or a field to approve. Only migration and explicit archival on Drop write it. There is no public editor, archive state machine, old-JSON reader, restore action, or background export process.

### Invariants

1. A non-null pending proposal belongs to the current gate of a nonterminal Ticket. The field must be declared, the body and author must be strings, and the timestamp an integer rather than a bool. Stored bodies may be empty: ordinary creation already seeds an empty Kickoff proposal when no Kickoff text was supplied. The storage codec and migration preserve that behavior and historical strings exactly; they do not add a nonempty guard. Existing worker-submission and pending-edit writers retain their `validate_body` checks, which reject an empty newly submitted or edited body.
2. A worker submission always targets the current gate. It cannot edit a saved value or choose another gate, even below the ceiling.
3. Worker ownership and sufficient scope accept immediately; paired/user ownership retain their existing parking semantics. Stop continues to prohibit proposals at the ceiling.
4. Acceptance writes exactly the pending field, clears that proposal and advances exactly one stage. Onward scope remains required.
5. Proposal edits preserve field identity, author, timestamp, saved values, stage, status, scope and ownership. Saved-value edits never resolve to a proposal.
6. `awaiting_approval` requires a pending proposal. Do not enforce the reverse: an actual human reply can change status to `paired` while the pending proposal remains. Existing takeover, error and reply behavior must not be flattened into proposal presence.
7. Ordinary stage mutation has no public route or writer after this cut. Stage changes occur through proposal resolution, Drop, or the existing explicit external-work operation.
8. Drop preserves any pending proposal as explicitly unapproved historical text and clears it atomically. A terminal Ticket has no pending proposal.
9. Archive content never affects readiness, scope, proposal acceptance or worker-context delivery.

### Concrete decisions, without discarded events

Change `src/planner/tickets/logic/decisions.py` and `src/planner/core/contracts.py`. Root's production call-site audit found that `Decision.events` is consumed only by the two proposal writers searching for `proposal_filed`; `_apply_decision` does not persist any events. The other EventSpecs are discarded. There is no current Ticket event-history path to preserve.

Remove `EventSpec`, `Decision.events`, `EventKind`, event-cause constants used only to construct discarded payloads, and fake event construction from Ticket resolution and external-work logic. Preserve concrete state behavior, not discarded test payloads.

The replacement Decision contains the complete prospective state owned by resolution, with six required members:

```python
@dataclass(frozen=True)
class Decision:
    field_values: TicketFieldValues
    pending_proposal: PendingTicketProposal | None
    archived_field_content: str
    stage: str
    ceiling: str
    at_cap: AtCap

    @classmethod
    def from_ticket(cls, ticket: Ticket) -> Decision:
        ...
```

`Decision.from_ticket(ticket)` copies these six members from the current Ticket. Pure rules use `dataclasses.replace` to change the members they own. The proposed `pending_proposal=None` always means no pending proposal in the resulting state; keeping the current proposal means carrying that object forward. There is no replacement boolean, unset sentinel, nullable patch member or null-means-untouched vocabulary. Acceptance returns the updated saved values, null pending proposal and next stage together. Parking carries forward saved values and stage and supplies the new pending proposal. All unrelated Ticket metadata, control status and ownership remain outside Decision.

The single `_apply_decision(conn, ticket, decision, now) -> Ticket` validates and persists the full prospective six-member state in its caller's transaction, without fallback expressions that substitute old Ticket members for nulls. Existing rules for captured ownership on stage entry, blocker changes and control status stay with their current canonical writers. Proposal writers derive parked status from the resulting pending proposal, not an event label. Tests assert resulting values/status/stage and transaction outcomes.

External-work rules remain exact-prefix rules. `decide_external_work(ticket, target_stage, provided_values, *, worker_type_definition) -> Decision` checks the one pending proposal, computes the flat values and position, and returns one complete Decision. Remove its former two-decision return and the two applications/associated loads from create and reconciliation writers. Apply the complete result once, retaining the existing transaction that includes recap, Kickoff, scope, ownership-derived status and placement. This removes a discarded-event interface; it does not loosen prefix validation or change external-work authority or semantics.

## Routes and canonical writer signatures

Keep these current-purpose interfaces, with explicit guards:

```python
file_current_proposal_with_recap(
    conn, ticket_id: str, *, body: str, recap: str, actor: str, now: int
) -> Ticket

accept_proposal(
    conn, ticket_id: str, *, field: str, actor: str, now: int,
    edited_body: str | None = None,
    next_ceiling: NextCeiling | None = None,
    at_cap: AtCap | None = None,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket

edit_pending_proposal(
    conn, ticket_id: str, *, field: str, new_body: str, actor: str, now: int
) -> Ticket

edit_field_value(
    conn, ticket_id: str, *, field: str, new_body: str, actor: str, now: int
) -> Ticket

return_for_revision(
    conn, ticket_id: str, *, message: str, actor: str, now: int,
    expected_proposal: PendingTicketProposal | None = None,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket
```

- Keep `POST /tickets/{id}/propose` with `{body, recap}`; infer the current gate inside the write transaction. Remove `POST /tickets/{id}/propose/{field}`, `ProposeBody` and the duplicate `file_proposal` writer. Preserve the existing worker identity/claim checks at the boundary.
- Keep `POST /tickets/{id}/accept/{field}` and existing `AcceptBody`; require the path field to equal both pending proposal field and current gate. Delete non-gating acceptance and its optional-scope behavior. The path field is a stale-request guard, not a second selector of work.
- Add `PUT /tickets/{id}/proposal`, parsed as `PendingProposalEditBody {field: str, body: str}`. Its field must match the current pending proposal. All actor modes currently allowed to edit pending text remain allowed through their existing admission boundary.
- Keep `PUT /tickets/{id}/value/{field}` for direct saved-value edits only, with its passed-field guard. Remove the branch that edits pending text instead.
- Keep return-for-revision and supervisor approval/revision routes. Preserve their send-before-clear ordering and expected-proposal comparison. Replace slot lookup at their Ticket boundary only; do not modify protected delivery code.
- Remove `POST /tickets/{id}/stage`, `StageBody`, `data.set_stage`, `resolution.decide_stage_jump` and its synthetic cause. Production source search found no caller beyond that route/writer pair, and no CLI or frontend stage-jump control. Existing tests use it as setup; that is not a product use. Replace setup with appropriate valid creation/reconciliation/progression fixtures. Do not replace the route with another arbitrary bypass.
- Keep Drop and external-work routes with the invariant-preserving changes above. Do not alter their authority model, worker exclusion or all-or-nothing behavior.
- Keep Kickoff creation special only as needed to seed the same new state: an immediate saved Kickoff or the Ticket pending proposal, never a field slot.

Pure resolution functions are the counterparts of these canonical writers: current proposal, accept, pending edit, saved-value edit, return, Drop and scope change. Each receives the Ticket and explicit Worker definition where needed. No rule searches global configuration or relies on Coding names.

## Historical record and migration

There is no current Ticket event-history reader to reuse. Supervisor history reads protected conversation events. Existing managed files can render an export, but SQLite and filesystem writes cannot form one atomic cutover, and Ticket files have no general inventory for discovering a migration export. Use the small SQLite archival document and existing Markdown renderer. Do not write fake conversation messages to make historical data visible.

Add a migration after the current head, using the normal `create_schema` entry. Its migration engine takes `BEGIN IMMEDIATE` and controls DDL transaction boundaries. Do not open another database connection, manually commit, or write files from the migration.

Freeze the mapping of Worker type/stage to gate and declared field order in this migration. Include the complete supported source definitions at the cutover, not only Coding. Do not import the mutable current registry from historical migration code and do not infer gates by stripping `needs_`. Unknown historical field ids are ordinary data to preserve. An unknown Worker/stage combination is a pre-write validation failure rather than permission to guess. Tests may use a supported frozen definition to exercise migration; generic `probe` behavior remains tested at the runtime contract level.

Read raw `tickets.fields` JSON directly, not `declared_fields_from_json`, Ticket JSON or any API export: those readers filter unknown historical keys. Read and validate every Ticket, every raw slot, every proposal and all old metadata before the first schema or record write. Proposal bodies are stored strings, including empty seeded Kickoff strings; migration must not reapply worker-submission nonempty validation. Build the conversion deterministically in Ticket-id and stored-field order. For each Ticket:

1. Copy currently declared saved strings into `field_values`, preserving exact string content including empty and whitespace-only strings. Omit keys whose saved value is null or whose old slot is absent; absence now means unset. Corrupt present slots or known scalar types fail before writes. The new runtime codec rejects undeclared saved-value keys; migration preserves historical unknown keys in the archive rather than silently filtering them on every future read.
2. On a nonterminal Ticket, lift only its current gate's proposal into `pending_proposal`, retaining field, body, author and timestamp. Keep any saved value of that same field separately; do not claim that a pending draft replaces it.
3. Archive every off-stage proposal and every terminal proposal under **Unapproved proposal**, including exact field id, author, timestamp and body. Do not select the newest proposal or give another proposal current authority.
4. Archive unknown historical fields' saved strings separately under **Previously stored value**. Do not call them approved. Empty and whitespace-only text must not disappear.
5. Preserve every unknown metadata key and recursively equal JSON value, on known or unknown slots and on proposals. Attach a safely fenced raw JSON section for metadata or full removed source slots. The surviving new proposal has only its explicit contract members; unknown proposal metadata belongs in this archive, not an extension bag. Do not silently strip metadata just because the runtime never consumed it.
6. Archive formatting must preserve body whitespace and safely handle backticks, Unicode and field names containing Markdown syntax. A fence longer than any matching run in embedded raw JSON is sufficient for the metadata block. Runtime only renders the resulting Markdown; it never decodes old field objects. JSON serialization whitespace is not the preservation contract; original string content and JSON key/value data are.
7. Drop the old nested-field storage after the complete new values and archive are ready. Verify every removed text and metadata value is represented before removing its source.

Preserve all unrelated columns, associations, placements, conversation pointers, scope and ownership. The measured live rows need no status correction: all 33 awaiting-approval Tickets have current proposals. If a different database contains `awaiting_approval` with no current proposal, fail the all-row validation with Ticket ids before any writes; do not silently invent a help request or start a worker. Such a database requires a deliberate repair decision before rerunning migration. This avoids speculative control changes in a representation migration.

Migration must roll back both schema and all records after a late conversion/write failure. Confirm the Alembic revision remains at the parent and original fields remain exact. A repeated successful startup must be a no-op. Run `foreign_key_check` in the migration route as normal. Preserve terminal and historical content without depending on an existing conversation.

For future Drop, append the current pending proposal to `archived_field_content` with its unapproved label before clearing it; write archive, stage, pending clear and affected blocker changes in one transaction. No archival is newly required for ordinary proposal replacement, whose previous text was not durably retained by the current implementation; do not silently expand this into a general event journal.

## Reading and editing

- `tickets/views.py` serializes the flat values and single pending proposal. Search includes current values, pending body, Guidance and historical record so migrated text remains findable. Review reads the Ticket proposal directly; Board and Sprint views derive their boolean from that same fact.
- Ticket displays saved values in passed-stage sections, the one pending approval on the current stage, and a collapsed **Historical record** disclosure only when archival text exists. Reuse `MarkdownBlock`; no editor, approval control or new elevated surface appears in history.
- `ReviewProposalCard` and `ApprovalBlock` share one current-proposal mode. Remove generic nongating proposal acceptance, its alternate Accept label and optional onward-scope branch. Keep read-only rendering only where it has an actual consumer.
- Pending editing calls the explicit proposal route; passed-value editing calls the saved-value route. Ticket and Review must show the same saved proposal body after editing before acceptance.
- CLI `my-ticket`/`ticket show` continue to expose the current field content and proposal; their projection reads the new Ticket shape rather than reconstructing backend slots. Add an `archive` part with the same read-only document and include it in copy output. Update supervisor Ticket context shape as needed; do not change supervisor conversation history.
- Guidance and automatic worker prompts keep their current delivery intent. Archive content is not automatically promoted into guidance or injected as a pending user instruction.

## Work groups and frozen boundaries

Root writes/reviews contracts first: `tickets/contracts.py`, `tickets/logic/decisions.py`, `core/contracts.py`, and frontend `lib/types.ts` or the repository's source-of-truth contract generation input. Root freezes signatures before dispatch. The EventKind removal belongs to this contract group; no agent invents replacement event shapes.

Implementation can then use isolated assignments with explicit ownership:

1. Migration: one new migration module and focused migration tests; frozen conversion mapping, all-row validation, archival and rollback. No runtime edits.
2. Ticket engine/API: `tickets/data.py`, `tickets/logic/{fields_codec,admission,machine,resolution,external_work}.py`, `tickets/api.py`, `tickets/actions.py` where required, plus focused engine/API tests. This group owns `_apply_decision`, Kickoff, Drop and removal of stage jump and arbitrary proposal routes.
3. Readers/CLI: `tickets/views.py`, affected `sprints/views.py` and supervisor Ticket-context code, `cli/main.py`, `cli/record_projection.py`, runtime readiness's Ticket proposal read, their focused tests and fixture adaptations. This group must coordinate edits to engine-owned files instead of touching them.
4. Frontend/docs: `TicketRoute`, `ReviewProposalCard`, `ApprovalBlock`, `TicketStageSection`, Ticket lifecycle/UI helpers and non-conversation consumers; frontend tests, relevant Ticket/CLI documentation and worker skill wording. Do not alter scope picker behavior or Worker definitions.

Root decides which non-overlapping assignments can share a worktree; overlapping changes require isolated worktrees and serial integration. Test fixture changes outside these groups require an explicit file assignment, not blanket permission to rewrite every test. Each Ticket records its plan/review/implementation/review sequence; collapse steps only for a truly trivial contract adaptation and record why.

Absolute protected implementation: `src/planner/conversation/**`, `src/planner/runtime/conversation_start.py`, `src/planner/message_delivery/**`, `src/planner/worker_context/**`, `agent_backends/**`, `web/src/components/conversation/**`, `web/src/lib/conversation/**`, and other conversation frontend modules. No edits to their implementation, transcript semantics, start/send/reset ordering, model configuration, acknowledgments or adapter contracts. At unprotected Ticket boundaries only, adapt how Ticket proposal state is read. Do not modify claim/release identity, polling concurrency, readiness ownership, closeout lanes, actual-send acknowledgment or human-reply status intent.

## Minimal gates and review

A focused gate follows a complete assigned piece of work. Reuse existing behavioral tests before adding overlapping ones. No tests are run while this candidate plan is being written. No `./verify` until the root's final settled program tree; one saved clean final run supports integration.

Migration gate: extend the pattern in `tests/unit/test_ticket_guidance_migration.py` and `test_one_approval_gate_migration.py` in one new `test_single_ticket_proposal_migration.py`. Cover current proposal plus off-stage drafts, an empty seeded Kickoff proposal, terminal drafts, unknown historical values/metadata, Unicode/whitespace/fence content, all-row prevalidation, a late write failure rollback, preserved unrelated columns/FKs and idempotent startup. Use synthetic content, not the private live JSON.

Engine/API gate: adapt `test_tickets_engine.py`, `test_value_edit_logic.py`, `test_value_edit_api.py`, `test_generic_field_storage.py`, `test_type_driven_ingress.py`, `test_chief_external_work_cli.py`, and typing seam cases. Preserve the existing tests for Kickoff, one-stage acceptance, scope, paired behavior, edit-accept, blocker release and failed transaction rollback. Change supersession to one pending proposal per Ticket. Assert current-field-only submission, distinct pending/saved edits, stale-field rejection, removed arbitrary routes, Drop archival and atomicity. Delete assertions of discarded EventSpecs or replace them with concrete state assertions; do not recreate them as a new log.

Handoff regression gate: reuse the existing narrow tests in `test_worker_step_readiness_loop.py` for refused and queued sends, acknowledged context only after send, actual opener content and paired departure; Ticket conversation-route tests already exercise revision delivery and human reply. Assert that revision snapshots the complete pending proposal before sending, compares it before clearing after a successful send, and neither clears a proposal nor acknowledges context when delivery is refused. Reuse supervisor action tests to preserve current-child membership authority on both approval and revision. Exercise worker, paired and user ownership separately where those outcomes differ. These tests may need Ticket fixture shape changes, but protected implementation must remain byte-identical. Do not add a new E2E for a shape adaptation.

Reader/frontend gate: reuse bounded-list and Sprint projection tests plus the existing Ticket frontend test setup. Add the smallest frontend proof for exactly one approval, saved-value editing versus pending editing, readonly Historical record, and archive copy/search access. `tests/e2e/test_pending_proposal_edit.py` already proves Ticket/Review edit persistence across a real server; preserve and adapt that existing narrow proof, with no new E2E matrix. The material boundary is persistence across both screens before approval; frontend/API tests own the rest. Run frontend type/build checks once its complete integration is settled.

Independent implementation review must trace every writer of stage/field/proposal and every reader of proposal presence; verify no arbitrary-field or silent stage bypass remains, no unapproved text became Guidance or a saved value, no false history claim was introduced, every frozen boundary is unchanged, and decision clear/untouched behavior is unambiguous. Root spot-checks resolver, migration parser and actual-send boundary call sites. Address or refute every review finding in writing before the final canonical verification and staging integration.
