# T04 plan review — codex output + orchestrator dispositions

Reviewer: `codex exec` against plan.md, ticket.md, SPEC.md §3.3/§4.1–4.5/§7.6/§18.3,
the contracts (tickets/contracts.py, core/contracts.py, core/errors.py), db.py, events.py,
and the shared conftest. Codex reported four findings (three marked blocking, one note).

## Codex findings (verbatim summary)

1. **blocking** — Recap gate incorrectly allows `dropped`. Plan §2.4/§7.2 and test_a08's
   final step allow recap writes at `dropped`; SPEC.md:44 says recap is "writable only when
   state is past `needs_success`", and `dropped` is outside the linear order
   (tickets/contracts.py:21,24), so it is not "past `needs_success`".

2. **blocking** — The plan excludes a day-assignment writer that ticket.md:12 lists
   ("set priority/deadline/day/sprint assignment"); the orchestrator ruling cited is not in
   the reviewed sources, so against them this is a scope deletion.

3. **blocking** — One-canonical-writer-per-edge not truly satisfied: edges 1–5 are produced
   by both `file_proposal` (auto branch) and `accept_proposal` (human branch); SPEC.md:99
   requires "One canonical function per transition edge in the code; no second writer".

4. **note** — `resolve_grant` return description internally inconsistent: plan.md:178 writes
   `GrantPair(new_state_as_ceiling)` with one argument while GrantPair requires both members.

## Dispositions (orchestrator)

1. **ACCEPTED.** The SPEC phrase is a positive permission ("writable only when X"); where X
   is undecidable — `dropped` is outside `STATE_ORDER`, so "past `needs_success`" is not true
   of it — writing would violate the "only". The plan's counter-argument (errors.py's comment
   "recap write at needs_success" implies a single rejected state) reads an exhaustive rule
   into a shorthand comment; the conservative direction is rejection. No capability is lost:
   the recap can be written in any working state past `needs_success` and survives the drop.
   **Amendment A1**: `check_recap_writable` rejects `needs_success` AND `dropped`, both with
   `ErrorCode.recap_too_early`, detail `{"state": <str>}`; `done` still allows. test_a08's
   final step inverts: after `drop_ticket`, `write_recap` raises `recap_too_early` and the
   recap is unchanged.

2. **REFUTED.** The ruling is real and binding: orchestration/tickets/T03-days/ticket.md
   explicitly owns "day-ticket list add/remove with contiguous positions from 0 …
   logs `day_ticket_removed`" in `src/planner/days/data.py`, and T03 is being built
   concurrently. A second day-ticket writer in T04 would itself violate the one-writer rule
   (SPEC §4.4.6 discipline extended by PRINCIPLES) and collide with a sibling ticket's owned
   behavior. ticket.md:12's "day" is superseded by the cross-ticket file architecture; codex
   was not shown T03's ticket. Recorded as a deviation-from-ticket-text in the T04 report for
   the integrator.

3. **ACCEPTED (strengthened form).** The dispatch brief enumerates "auto-advance" and
   "human accept" as distinct edges, under which the plan already complied; but the strict
   graph reading (edge = state pair) is the audit-safe one, and both readings can be
   satisfied at once. **Amendment A2**: a single internal function
   `resolution._accept_gating_proposal` becomes the sole builder of every gating-acceptance
   transition (edges 1–5); `decide_file_proposal`'s auto branch and `decide_accept`'s gating
   branch both delegate to it, differing only in data (resolved_by / edited / grant / cause).
   No transition Decision for edges 1–5 is constructed anywhere else.

4. **ACCEPTED.** Typo fixed by **Amendment A3**: the `"none"` branch returns
   `GrantPair(next_ceiling=new_state, at_cap=at_cap)` — both members always present.

Amendments A1–A3 are appended to plan.md as a binding section; the plan body is otherwise
unchanged.
