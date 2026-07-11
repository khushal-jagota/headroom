# Implementation brief — t_hvnv9gyc

Implement the approved correction on branch `ticket/t_hvnv9gyc-kickoff-normal-stage`, based at merge `9ab2111`.

## Intent

Kickoff is the first ordinary Ticket stage. Its canonical value is the kickoff note. Ticket title remains separate editable metadata. Creation leaves every new ordinary Ticket at `needs_kickoff`, with an ordinary pending `fields.kickoff.proposal` and `awaiting_approval`, until a human approves it into Success. Kickoff uses the same field/proposal/approval/scope/event/visual-state machinery as Success through Closeout.

Remove the just-added custom model: compound `KickoffProposal {title,kickoff_note}`, top-level `kickoff_note`, dedicated accept route/body/writer, and `KickoffSection` pseudo-stage UI. Existing special kickoff events may remain readable as history; all new writes use ordinary proposal events.

## Binding decision (D108)

- Add `kickoff` as first `FieldName`; map `needs_kickoff` in ordinary `GATING_FIELD`, `ADVANCE_TARGET`, stage ordering, resolution, scope, queue, copy/read models, and UI state logic.
- Do not add a title-proposal type. `tickets.title` is already canonical at creation. Keep title editing independent through the ordinary title PATCH path, including while Kickoff is current and from Review. Approval must not carry title payload.
- Migration preserves `tickets.title`; moves settled top-level `kickoff_note` to `fields.kickoff.value`; moves pending compound `kickoff_proposal.kickoff_note` to an ordinary `fields.kickoff.proposal`; discards only the redundant proposal-title copy.
- Every `needs_kickoff` ordinary Ticket must have a pending Kickoff proposal and `awaiting_approval`; prove the employee runner cannot dispatch it.
- Preserve links and atomic rollback. Migration must be safe on pre-kickoff schemas and on the current 9ab2111 schema, including retry/idempotence/partial-startup behavior supported by the repository's migration model.
- Replace `KickoffSection` with the shared `TicketStageSection` in Ticket and Review. Put separate title editing in the title/header/review metadata surface; preserve current interactions and do not redesign the page.
- Seed import and Chief external-work paths should produce settled `fields.kickoff.value` when importing work already beyond Kickoff. Update CLI flags/contracts only as needed to keep kickoff note as input while representing it canonically as the field.
- Update live docs and source worker/Chief skills so they describe Kickoff as the first normal stage and do not treat title as stage content.

## Required tests

Adapt and add focused tests for:

1. Current 9ab2111 schema settled row -> kickoff value; pending compound row -> ordinary kickoff proposal; title preservation; old columns removed/ignored as designed; links preserved; rollback; retry/idempotence.
2. Creation atomically yields `needs_kickoff` + ordinary proposal + `awaiting_approval`.
3. Ordinary propose/accept route, scope, and event semantics advance Kickoff to Success.
4. Real employee runner/readiness regression: pending Kickoff never dispatches.
5. CLI creation/approval and external-work/seed paths.
6. Review queue and browser lifecycle: title is independently editable; Kickoff displays through the shared stage component; approval yields completed green Kickoff and current Success; no dedicated component remains.
7. Provisioned skill/docs expectations that prevent title from drifting back into Kickoff content.

Follow TDD where practical: capture failing regressions first, then implement. Run focused unit/e2e/frontend checks, not the canonical `./verify`; the orchestrator owns the single final verifier run after independent review. Do not commit. Do not touch files outside this ticket's implementation, tests, docs, generated frontend bundle, and the supplied memory/brief records. Do not modify contracts beyond the approved ordinary-field shape. Report changed files, focused test commands/results, and any unresolved concern.