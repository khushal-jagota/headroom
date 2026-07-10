# t_fe04 — One approval surface

Depends on t_fe01 (Disclosure) and t_fe03 (Button).

## Outcome

The proposal/approval machine exists once. Today it exists three-and-a-half times:
`ApprovalBlock` (which internally repeats its actions block four times across
mode × layout), `ProposalCard` (the same machine minus the review layout), and
TicketStageSection's hand-rolled read-only proposal display for dropped tickets.
Both `ApprovalBlock` and `ProposalCard` are used only by TicketStageSection (and
ReviewRoute through it), so this consolidation is fully contained.

## Contract

One component, `web/src/components/ApprovalBlock.svelte` (keep the name and the
`data-approval-block` hook):

- Props (superset of today's two components):
  - `mode: "gating-pending" | "needs_review" | "proposal" | "readonly"` —
    `proposal` is today's ProposalCard case (editable draft, optional scope, Accept);
    `readonly` is today's dropped-ticket display (proposed-by line + body, no actions).
  - `layout: "default" | "review"` as today.
  - The existing field/label/body/scope/note/actions props, unchanged where possible.
- Internal dedupe: exactly one actions-group snippet (error line + primary Button +
  optional ScopePairPicker) rendered in the right place per layout — replacing the four
  near-identical copies. Exactly one draft/scope/inFlight/resolved/error state machine
  and one reset-on-new-proposal `$effect` — shared by all modes.
- Buttons come from t_fe03's Button (`data-accept` / `data-approve` preserved per mode).
- Disclosure (t_fe01) replaces the internal ContentDisclosure usages if t_fe01 has not
  already done so.

## Migrations

1. Delete `web/src/components/ProposalCard.svelte`; TicketStageSection's non-gating
   proposal branch uses `ApprovalBlock mode="proposal"` (same payload semantics:
   `edited_body` only when the draft changed; scope only when required — the non-gating
   ticket-page case passes `requireScope=false` today, keep that behavior).
2. TicketStageSection's dropped-state block (`.ticket-field-proposal` + `.proposal-meta`
   + MarkdownBlock) → `ApprovalBlock mode="readonly"`.
3. Collapse the four internal action groups; the rendered DOM for existing modes keeps
   `data-approval-block`, `data-mode`, `data-field`, `data-accept`, `data-approve`
   exactly as today.
4. CSS: merge `.proposal-card*` into the `.approval*` family (or one shared name);
   delete leftovers. `.proposal-meta` is shared — keep one definition.

## Tests / acceptance

- e2e: `test_flows_a.py`, `test_flows_b.py` (approval flows on ticket page and review
  screen), plus any test touching `data-approval-block` / `data-accept` / `data-approve`.
- The unit-level behavior that matters and must be preserved exactly:
  - gating accept payload: `next_ceiling` + `at_cap` (+ `edited_body` iff changed);
    button disabled until scope chosen.
  - needs_review approve payload: `{}`.
  - proposal accept payload: `edited_body` iff changed.
  - resolved/in-flight disabling; error shown via ErrorLine; state resets when a new
    proposal body arrives.
- `cd web && npm run build && npm run check && npm test` clean.
- `grep -rn "ProposalCard\|ticket-field-proposal" web/src assets/` returns nothing.
