---
name: "panels-worker-initiative-review"
description: "Review a delivered initiative as one combined result, capture user feedback, and create the agreed follow-up work."
---

# Initiative review ticket stages

This Worker gives the user one evidence-backed review of a delivered initiative or another coherent multi-Ticket change. It tests the combined result against its original intent before integration.

Use it selectively. It complements Sprint Item supervisors, Initiative Planning, and Coding Workers. It does not replace their planning, delivery, or Ticket-level review work.

## The stages

The sequence is **Kickoff → Review → Feedback → Follow-ups → Closeout → Done**.

- **needs_brief** — identify the intended outcome, combined change, source context, target base, and integration route.
- **needs_review** — inspect the combined result, run proportionate checks, and create the HTML review for an exact commit.
- **needs_feedback** — review the result with the user and record the agreed meaning and disposition of each comment.
- **needs_followups** — convert agreed feedback into separate Tickets or other scoped work, with clear integration effects.
- **needs_consequences** — integrate the approved result when no blocker remains, or preserve the target and report the handoff.
- **done** — finished.

## Review identity

Judge the combined result, not the quality of its summaries. Recover intent from the Sprint Item, plans, decisions, and Ticket records. Inspect the actual combined tree and treat prior summaries as evidence pointers.

Follow the risks and seams of the initiative. Do not apply a fixed checklist. Separate facts, inferences, recommendations, and unproven claims.

A material source or behavior change invalidates the review and its approval. Return the Ticket to Review before integration. A clean integration does not invalidate approval when it preserves the reviewed tree.

## needs_brief — establish the review target

Name the intended outcome, the combined branch or change set, and the source records. Identify the target base, the integration route, and any explicit authority for deployment or publication.

Confirm that the review target and its source context exist. Do not assume that the result is ready.

A good **kickoff** lets another worker identify the exact review surface without reconstructing the initiative.

## needs_review — review the combined result

Start from the original outcome. Inspect the combined tree, diff, behavior, data effects, and operational boundaries. Test the seams between Tickets where contracts, state, migrations, permissions, failure behavior, or user flows create risk.

Reuse sound upstream evidence. Add final checks when combined risk requires them. Do not repeat every delivery test by default.

Use a worktree and branch for any correction. You can correct a bounded defect when it preserves the agreed intent and remains easy to inspect. Update the reviewed commit and all affected evidence after the correction.

Do not absorb substantial defects or scope changes into Review. Record them in the artifact for the later Feedback and Follow-ups stages.

Create one standalone, ticket-owned HTML artifact. Organize it around the result rather than Ticket chronology. Include:

- the finished result and its value;
- how the parts work together;
- the exact reviewed commit and base;
- evidence for important claims;
- migrations and behavior for current data;
- operational limits, known defects, and unproven claims;
- a clear readiness judgment.

Use real screenshots, demonstrations, diagrams, or reproducible previews when they improve judgment. Do not use decorative evidence.

A good **review** links the artifact and states the reviewed commit, readiness judgment, and material risks. Its approval confirms that the artifact is ready for Feedback. It does not authorize integration.

## needs_feedback — capture the user's judgment

This is a user-owned collaborative stage. Open with the artifact, the readiness judgment, and the decisions that require user attention.

Let the user direct the review. Preserve each concern before interpretation. Ask for clarification until both parties agree on its meaning.

Record each comment with:

- the specific concern or request;
- its agreed interpretation;
- its agreed disposition;
- its integration effect;
- its destination when that destination is already clear.

Also record one explicit integration decision for the exact reviewed result. The user can approve it, approve it with later improvements, or withhold approval.

Do not apply requested changes after Feedback starts without a durable record. Move requested changes to Follow-ups. Do not propose **feedback** until the user explicitly agrees with the record.

## needs_followups — create the agreed work

Use the approved Feedback as the authority. Do not expand its scope.

Load and follow `panels-ticket-creation` before you create any Ticket. Group comments by coherent outcome, not by sentence. Use another clear work record only when a separate Ticket adds no value.

For each destination, preserve the source concern, expected outcome, dependencies, and integration effect. Separate integration blockers from later improvements.

Read every created or updated record back and verify it. Keep a trace from each agreed comment to its destination.

A good **followups** proposal lists the created or updated work and maps every agreed comment to it.

## needs_consequences — integrate or hand off

If explicit approval exists and no blocker remains, integrate the exact reviewed result through the defined route. Confirm that the approved tree remains unchanged.

If integration introduces a material source or behavior change, stop. Return the Ticket to Review and renew the affected evidence and user decision.

Do not deploy or publish without explicit authority. If approval does not exist or a blocker remains, leave the target unchanged.

A good **closeout** states the reviewed result, the target state, and the verification. If integration did not occur, it states the blocker, handoff, and next review point.

## Initiative Review disciplines

- Make the review useful without the user opening every delivery Ticket.
- Prefer direct evidence over inherited confidence.
- Keep the artifact proportionate to the result and its risks.
- Preserve the user's feedback as decisions, not as a loose transcript.
- Follow `panels-worker` for ownership, proposals, scope, and reconciliation rules.
