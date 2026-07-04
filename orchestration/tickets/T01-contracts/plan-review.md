# T01 plan review (codex) — dispositions

Nine findings; all accepted. Resolutions appended to plan.md as the "Amendments after codex review" section, binding over the body where they differ.

1. **Ceiling admits `dropped`** — accepted. `ceiling`/`next_ceiling` values must be members of `STATE_ORDER` (the six linear states); write paths validate; DDL CHECK already excludes `dropped`. Contract comment states it.
2. **`Ticket.project` under-typed** — accepted, resolved by rehoming: `Project` and `Priority` move to `core/contracts.py` (genuinely cross-domain vocabulary, like `LinkKind`); tickets/sprints/dispatch/seed import from core. `Ticket.project: Project | None`.
3. **Idea contract missing** — accepted. `Idea` dataclass added to `sprints/contracts.py` (backlog & ideas are the sprints domain's backlog surface).
4. **EventRow outside contracts** — accepted. `EventRow` moves to `core/contracts.py`.
5. **Link dataclass missing** — accepted. `Link` dataclass added to `core/contracts.py`.
6. **CLI inline text channels** — accepted. `item set --note` dropped (item `set` is §3.2 transitions only; note edits are UI/API); body input requires explicit `-` or `--body-file` — no implicit stdin.
7. **Hardcoded tunables** — accepted. New config keys `events_read_limit` (500, PLAN_EVENTS_READ_LIMIT) and `db_busy_timeout_ms` (5000, PLAN_DB_BUSY_TIMEOUT_MS).
8. **tokens.css absent from the skeleton** — accepted. `assets/tokens.css` ships in T01 with the full token category set from PRINCIPLES/SPEC §10 (surfaces, text, accent bright/surface/text, radius scale, motion fast/base/slow, spacing scale, border widths) and starter values; stage 5 tunes values, never adds categories without evidence.
9. **D4 vs click** — accepted as a decisions.md amendment: D4 now reads "imports contracts + click + httpx + stdlib; never domain logic or data layers." Click is a pinned dependency; the point of D4 was layering, not asceticism.

Orchestrator additions folded in with the amendments: `/api/day/{date}` also accepts the literal `today` (CLI default resolution stays server-side); the title-max startup assertion lives in server startup, not `create_schema`.
