# T07 plan review — codex output + orchestrator dispositions

Reviewer: `codex exec` (non-interactive), pointed at plan.md, ticket.md, SPEC.md §12/§18.3(19)/§3/§4.2–4.4,
seed/contracts.py, core DDL + contracts, and the real snapshot files. Asked for concrete violations across
ten named areas (the dispatch-mandated set: five status mappings, four readiness mappings, title-match
linking, R6 latest-day-only, report completeness/silent drops, demo determinism + empty-DB guard, plus
idempotency, test coverage, fields JSON, and parser-vs-snapshot correctness).

## Codex findings (verbatim summary)

1. CLEAN — item status mappings; deferred.md `status='todo'` judged consistent (SPEC only requires
   `sprint_id = NULL`; §3.2 reads NULL sprint as backlog/deferred work).
2. CLEAN — readiness mappings via READINESS_MAP.
3. CLEAN — title-match linking (exact, exactly-one-candidate; parented columns per §3.3; standalone
   sprint assignment).
4. CLEAN — R6 latest-day-only with historical folders enumerated.
5. CLEAN — report completeness: fixture 6-entry and snapshot 7-entry skip lists verified against the
   embedded fixture texts and the actual snapshot files; excerpt rule and Mode-drop match the contract
   comment.
6. CLEAN — demo dataset (db_not_empty guard, 8 tickets over every state, ceilings never dropped,
   one blocks link, §3.3 parented columns, PlanTree shape).
7. CLEAN — idempotency keys, skip-only re-runs, duplicates arithmetic (fixture 17, snapshot 46),
   zero duplicate links.
8. CLEAN — single `test_a19_` test covers every item-19 clause; supporting tests not a19-prefixed;
   no test reads the snapshot.
9. **VIOLATION** — ticket `Body:` mapped to `fields.success.notes` instead of a field **value**;
   cites ticket.md line 11 / SPEC §12 "body/success/approach text into the matching fields' values" /
   ParsedTicket comment. Codex itself notes: "Because tickets have no body column, the exact value
   target needs an explicit spec resolution."
10. CLEAN — parser correctness against the real snapshot shapes; pinned counts
    (1 / 12 split 6-5-1 / 4 / 9 / 20) reproduced by the planned tokenizer/parsers.

Full raw output: preserved by the orchestrator in the session transcript; the ten-point list above is a
faithful condensation (no finding omitted).

## Orchestrator dispositions

Findings 1–8, 10: **accepted as CLEAN** — no action.

Finding 9: **accepted as a real wording tension; plan retained; resolution recorded as binding.**
The literal instruction has no implementable target for `Body:`:

- §4.2 fixes `tickets.fields` to **exactly four keys** (success, approach, plan, result). There is no
  `body` key, and the tickets table has no body column (core/db.py DDL). "The matching field" for
  `Body:` does not exist — the phrase resolves cleanly only for Success: and Approach:.
- Every candidate *value* home is wrong on §4.2/§4.4 semantics: `success.value`/`approach.value`
  collide with real data (snapshot mic-publish carries both `Body:` and `Success:`); `plan.value` on a
  needs_plan ticket would present the pending gate as passed; `result.value` likewise for in_progress.
  `value` is the canonical accepted-proposal slot — §12 sanctions seed writing it only where a source
  field *matches* one.
- The contract (law, unmodifiable by this ticket) keeps `ParsedTicket.body` as its own attribute,
  separate from `success`/`approach` — the parser side is faithful. Only the import destination needed
  resolving.
- Chosen home `fields.success.notes` is the only slot that is legal in every imported state
  (§4.2: notes are free-guidance text writable at any time, no state effect), uniform (one rule, no
  state-dependent hybrid), non-silent (queryable in the fields JSON), and does not pollute
  `result.notes`, which §4.2 reserves as review notes. `recap` was rejected because §3.3 forbids recap
  content at needs_success and the fixture deliberately imports a Concepts ticket.
- No test fence pins Body's destination (item 19 spot-checks states, Chat ID, deferred NULL sprint,
  idea project), so this resolution weakens nothing.

Action taken: binding amendment appended to plan.md restating the resolution; the implementation report
must carry it forward as a concern for the integrator (possible future contract amendment if the owner
wants ticket descriptions in a first-class column). No re-plan needed — the finding is a destination
choice inside an otherwise-clean structure, not a structural defect.
