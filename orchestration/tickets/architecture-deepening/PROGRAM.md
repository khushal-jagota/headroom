# Architecture deepening program

Branch: `codex/architecture-deepening`

The program replaces six shallow areas without changing product behavior except where the owner made an
explicit deletion or naming decision. Every ticket is independently landable, receives a delegated plan,
an independent plan review, delegated implementation, an independent diff review, and one canonical
`./verify` before serial integration.

## Landing order

1. **AD01 — Worker type and stored Stage contracts**
   Rename the persisted and public Ticket vocabulary. Worker type is required and immutable; Stage is the
   direct stored value. No compatibility aliases.
2. **AD02 — Deep Worker workflow interpretation**
   Rename the internal type domain, make one Worker-type definition authoritative, remove parallel lifecycle
   tables, shallow registry forwarders, `coding_bridge`, and implicit coding defaults.
3. **AD03 — Automatic Employee-step eligibility**
   Replace readiness/runnable naming and make discovery plus final claim consume one complete decision.
4. **AD04 — Ticket-only Review**
   Delete Sprint-item and overdue branches; rename Queue routes, resources, contracts, and views to Review.
5. **AD05 — Canonical human Chat ingress**
   Delete `/send`, `/stream`, `/command`, and their parallel service/adapter paths while preserving `/new`.
6. **AD06 — Deep canonical Chat turn**
   Concentrate admission, session ownership, transcript writes, observation handling, and settlement behind
   the server-owned human-turn interface without crossing the worker-delivery boundary.
7. **AD07 — Managed Markdown**
   Consolidate render/edit/preview lifecycle ownership with exact visible and serialization parity.
8. **AD08 — Frontend resource catalogue**
   Catalogue cached UI reads only; retain canonical events plus immediate targeted refresh.
9. **AD09 — Panels Chat / Employee session history separation**
   Remove silent history merging while retaining explicit employee history. This lands last in its own
   isolated commit because restart, rotation, and recovery semantics are the most finicky.

## Fixed constraints

- `CONTEXT.md` is the canonical domain language.
- Worker type cannot change after Ticket creation.
- Reading a Ticket's Stage never resolves Worker type.
- Ticket control status remains separate from Stage.
- `TicketReadinessLoop` and `EmployeeStepRunner` responsibilities remain separate even when renamed.
- Panels Chat rows never substitute for delivery through the employee's Hermes session.
- Managed Markdown and the resource catalogue do not redesign the UI.
- No compatibility adapter preserves a deleted interface or rejected term.
- `./verify` is the sole completeness gate.
