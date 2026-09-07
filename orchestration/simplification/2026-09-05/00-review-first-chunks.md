# Independent review of the first simplification chunks

Reviewed against `b7ca8e047967e405feeebe058f5cd2ef82b2c2e5` and the current Atlas
diff on 2026-09-05. This is a read-only review except for this artifact. No tests or
`./verify` were run. AGENTS/program edits are outside the implementation review.
Live aggregate counts in the plans establish preservation relevance, not user value;
no private record text is reproduced here.

## 01 Remove Atlas — approved

No actionable findings. The diff removes the alternate board/review projection,
its scene, navigation and panel state, dedicated tests, dependencies and CSS.
The shared Ticket/Sprint Item/Review and click-helper edits are comments only.
The CSS deletion is confined to the Atlas section. The package lock removes three,
its types and their dependency-only entries. No conversation/backend implementation,
contract, or shared conversation styling changes appear in the diff.

Atlas's useful actions remain on the ordinary Ticket, Sprint Item and Review
surfaces. Its removed behavior is the specifically authorized scenery, spatial
selection and alternate review walk. The old route uses existing unknown-route
behavior, as the ticket explicitly decided. The recorded svelte-check result is
the implementer's evidence; final build and retained surface coverage remain the
program gate.

## 02 Sprint documents — approved

No actionable findings. The four-value shape preserves the useful short
`primary_bet` consumer in Sprint tracking and reduces the other prose into three
documents. The migration mapping covers all eleven retired fields and keeps the
summary separately without duplicating it in kickoff. The plan explicitly preserves
whitespace, timestamps, identities and references, freezes old migrations, rejects
retired write keys, and retains compound-write authorization and atomicity.

The primary-bet decision has concrete implementation evidence in SprintRoute's
tracking summary and CLI explicit-part reads. It does not depend on the number of
filled live fields. The named migration, HTTP/CLI and mock-browser gates match the
changed boundaries; no new real-backend E2E is warranted.

## 03 Ticket guidance — revised design approved; apply these corrections before dispatch

The submitted file has the following actionable issues. Root resolved the design
choices during this review as recorded below. The plan must incorporate those
resolutions before the implementation receives its fixed contracts. No additional
review round is needed merely to transcribe these decisions.

### [P1] Remove the proposed full-document pending-context mechanism

Evidence: plan lines 49–55 introduce a separate pending guidance key while allowing
Worker-authored guidance to skip context production. A direct edit can queue G1;
a Worker can then replace the stored document with G2; the next step would contain
G2 in its opener followed by stale G1 in pending context. The generic service appends
its snapshot after the opener (`worker_context/service.py:_prepare_prompt`), so
this is an actual contradictory prompt, not merely stale UI.

Resolution from root: do not introduce that key. The guidance writer uses the
existing `set_ticket_changed` producer exactly as the old note writer does. The
pure `worker_step_prompt` includes the current Ticket guidance. This needs no
generic context-service changes, no stale-document reconciliation and no duplicate
29k-character document in the same step prompt. Add a meaningful actual-send proof
that an automatic step contains the current guidance; preserve the existing refused
send receipt-retention proof.

### [P2] State the preserved delivery limit precisely

Evidence: only `runtime/worker_step_readiness_loop.py:130` and
`tickets/actions.py:296` prepare generic pending context. Ordinary Ticket Chat
(`tickets/api.py:1396`) forwards owner content directly. A paired Stage receives no
further automatic step simply because guidance was saved. A stored document is
therefore not proof it reached that conversation.

Resolution from root: protect all existing conversation send behavior. Only the
automatic worker-step prompt gains the current full guidance. Return-for-revision
keeps its existing revision text and generic reread notice; ordinary Chat keeps its
existing content. Both may read the durable guidance through Ticket detail or an
explicit CLI part. Document this limit and never claim saving guidance sends it.
Do not extend the Ticket Chat route, conversation_start, or conversation contracts.

### [P1] Define preservation when both historical note keys exist

Evidence: `tickets/logic/fields_codec.py:_slot_from_obj` accepts `user_note` and
legacy `notes` in a slot, preferring the first even when it is null. The plan
deletes both keys but initially gives only a per-field heading rule. Choosing the
codec's preferred value would lose distinct historical text, violating the plan's
all-content preservation promise. API aggregates cannot rule out a masked legacy
key because that projection already hides it.

Resolution from root: preserve both nonempty values distinctly under deterministic
source-labelled headings whenever both exist; preserve a nonempty legacy value
even if user_note is null. Preserve whitespace-only values and unknown historical
field keys. A synthetic dual-key fixture must prove both texts survive along with
field values and proposals. No mutable registry import or legacy read fallback.

### [P2] Preserve the existing CLI read grammar

Evidence: plan lines 31–33 promise ordinary default content plus manifest reads,
but `cli/record_projection.py:project_record` makes a default read the manifest;
the optional positional part list is the only expansion mechanism. Inventing an
implicit full-read mode would change every reader's information contract.

Resolution from root: default reads remain header plus manifest. Add recap and
guidance as explicit Ticket parts; requesting them returns their content. Retain
the existing ordered manifest/part grammar and remove the obsolete per-part
user_note/has_user_note shapes rather than adding a second mode. The automatic
step prompt already carries actual guidance where guaranteed delivery is needed.

## Guidance implementation ownership inventory

Root-owned contract changes: `src/planner/tickets/contracts.py` (FieldSlot,
Ticket, and guidance request bodies replacing NoteBody/AppendNoteBody) and
`web/src/lib/types.ts` (TicketField and TicketDetail).

Implementation consumers: `src/planner/tickets/{data,api,views}.py`,
`src/planner/tickets/logic/{fields_codec,resolution,external_work}.py`,
`src/planner/runtime/logic/worker_step_prompt.py`,
`src/planner/cli/{main,record_projection}.py`, and one new frozen migration.
Search lives in tickets/views.py: update both `_ticket_search_text` and the SELECT
that supplies its rows. Copy text is in the same file. Supervisor context already
uses `tickets_views.ticket_detail` in `sprints/supervisor_service.py`; no supervisor
implementation change is required to inherit guidance.

Frontend ownership: `web/src/routes/TicketRoute.svelte`,
`web/src/components/TicketStageSection.svelte`, and
`web/src/components/ReviewProposalCard.svelte`. Keep shared editors and all
conversation files protected. Documentation/skill ownership:
`src/planner/skills/panels-worker/SKILL.md` and
`docs/{tickets-and-gates,cli,frontend,worker-orchestration}.md`.

The existing ticket-specific context producer can remain unchanged under the
settled delivery decision. Do not edit generic context data/service/contracts,
conversation send functions, agent backends or historical migration implementations.
Existing tests that intentionally execute an old migration retain that migration's
old slot shape; only current-schema expectations should change. Contract-driven
test repairs should keep their original behavior purpose, with new consolidated
guidance coverage for migration, round trips, atomic append, current prompt text,
CLI write/read grammar, search/copy, and the single editor.
