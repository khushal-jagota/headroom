# T10 plan review — codex findings + orchestrator dispositions

Reviewer: `codex exec` (non-interactive, repo root), pointed at plan.md, ticket.md, the T01 route
table (plan.md 1025–1233), SPEC §3/§4/§5/§6/§7/§8/§9/§10, and every source file the plan claims to
consume. Audit areas per the dispatch brief: (H)-route completeness, grant-pair passthrough, day
`today` resolution, board card fields, one-writer-per-edge, signature fidelity, queues, transactions.

Codex's clean verdicts: **A** (H)-route completeness clean (14 routes confirmed); **B** grant-pair
passthrough clean; **C** literal `today` resolution correct; **F** all section-1 writer signatures
match disk; no route-level event appends found where writers already append.

Orchestrator's independent pre-check (before codex returned): verified on disk `create_ticket` /
`accept_proposal` (None-passthrough) / `resolve_grant` missing-detail shape `{"missing": [...]}` /
sprints `clock:` kwarg vs `now:` elsewhere / `isolation_level=None` autocommit / `_ITEM_PLAIN_FIELDS`
/ `_SPRINT_TEXT_FIELDS` / `AGENT_CLOSE_OUTCOMES` / `close_run` positional signature / `NodeRef` /
`submit_replan` seam / config env names. All matched the plan.

## Findings and dispositions

### 1. BLOCKER — private writers (D1 `_create_idea`, D2 `_set_title`/`_set_project`, D3 `_set_sprint_dates`) live in api modules
**Disposition: ACCEPTED AS RECORDED DEVIATION — plan stands; relocation requested at integration.**
The tension is real: ticket.md line 7 says the canonical writer lives in data layers. But the same
ticket makes the route table binding (PATCH tickets covers title/project; PATCH sprints covers
name/dates; POST /api/ideas exists) while granting T10 no ownership of any `data.py` — codex's
proposed fix (move them into the data layers) is exactly what the orchestrator playbook forbids
(files outside the owned list). Within T10's authority the plan's resolution is the only lawful one,
and it preserves the rule's substance: each edge still has exactly one writer, writer-shaped
(no FastAPI/pydantic types inside), mirroring the data layers' transaction + event patterns.
Amendment A1 hardens this; the report to the integrator carries a contract-change request to
relocate the four functions into their `data.py` homes as integration glue.

### 2. MAJOR — day materialization runs unwrapped on `GET /day/{date}`
**Disposition: ACCEPTED — amendment A2.** `materialize_day` is check + INSERT + `append_event` on an
autocommit connection; a failure between the INSERT and the event leaves a day row with no
`day_created` event. The plan's own discipline (wrap multi-statement non-self-transacting mutations)
applies; the "reads run bare" note wrongly classified a materializing read. `_day_view` now wraps its
`read_day` call in `txn(conn)`.

### 3. MAJOR — board card shape adds `id` beyond §10.3's list
**Disposition: REFUTED.** §10.3 enumerates what cards *show* (display fields). The same SPEC line
mandates "card click opens Ticket" — impossible without the ticket id in the payload; every entity
JSON in the system carries `id`. Omitting it would strand the stage-5 Board screen and e2e items
22/31 (board→ticket navigation). `id` is the navigation key, not a displayed card field. Plan
unchanged; decision recorded here.

### 4. MAJOR — overdue excludes `deferred_next_sprint` items where §4.5 literally excludes only done/dropped
**Disposition: REFUTED AS A PLAN CHANGE — standing concern C5 upheld.** The predicate lives in
`days/logic/carryover.overdue_list` (stage 3, unit-fenced, outside T10 ownership). The two lawful
options are (a) reuse it, (b) fork the overdue definition between the API queue and the boundary
job's overdue list — (b) is strictly worse and touches nothing T10 may change anyway. Semantics also
favor the disk: a deferred item is deliberately punted, not overdue noise. Surfaced in the report as
a SPEC-clarification item for the integrator; not a T10 change.

### 5. MAJOR — `needs_review` approvals age by `updated_at`, so a later edit re-ages the entry
**Disposition: ACCEPTED — amendment A5.** §4.5's oldest-pending-first is the binding contract for
the API queue, and the `updated_at` proxy observably violates it (a result-notes edit would demote a
long-waiting review). The queue now derives `waiting_since` for review entries from the ticket's
latest `state_changed` event with payload `to == "needs_review"` (read-only SQL in
`tickets/views.py`, within ownership), falling back to `updated_at` when no such event exists. The
boundary digest (agent-facing, advisory) keeps its documented proxy — the fork is deliberate and
documented: the spec-fenced surface gets the spec-true definition.

### 6. MAJOR — `resolve_day_id` builds `day_` ids from the raw path segment
**Disposition: ACCEPTED — amendment A6.** `date.fromisoformat` accepts compact forms ("20260704"),
so `f"day_{date_seg}"` could mint `day_20260704`, violating §3.4's `day_YYYY-MM-DD`. The helper now
returns `ids.day_id(parsed_date)` so the id is always built from the parsed date's isoformat.

### 7. MAJOR — sprint-item `deadline` reaches the writers unvalidated
**Disposition: ACCEPTED — amendment A7.** `create_item`/`update_item_field` don't validate deadline
(unlike tickets' `validate_deadline`). Route-side ISO marshalling is already the plan's global
convention (sprint create does exactly this for its dates); item create/PATCH now marshal a present
non-null `deadline` through `date.fromisoformat`, ValueError → `PlannerError(validation)`.

## Outcome

Plan amended (see plan.md §9 "Binding amendments after codex review"): A1, A2, A5, A6, A7 binding;
findings 3 and 4 refuted in writing above. No structural re-plan needed — the blueprint's shape,
auth mapping, serializers, and views strategy survived review intact.

<details>
<summary>Raw codex output (verbatim findings section)</summary>

```
1. BLOCKER — E / private writers in API modules.
Plan: D1/D2/D3 put `_create_idea`, `_set_title`, `_set_project`, `_set_sprint_dates` inside API
modules: plan.md:277, plan.md:200, plan.md:285.
Contradiction: T10 says route handlers never contain domain rules and the canonical writer lives in
data layers: ticket.md:7.
Fix: move these gap-fill writers into the data layers or amend ownership/scope before implementation.

2. MAJOR — H / day materialization left unwrapped.
Plan: says `days_data.read_day` materialization is safe bare and `GET /day/{date}` calls `_day_view`
bare: plan.md:45, plan.md:327.
Contradiction: `materialize_day` is multi-statement: existence check, INSERT, then `append_event`:
days/data.py:26, :32, :37.
Fix: wrap day materialization/read in `txn(conn)` when it can create the day.

3. MAJOR — D / board card shape adds `id`.
Plan: card keys are "exactly" `id, title, priority, deadline, project, has_pending_proposal,
has_running_claim`: plan.md:613.
Contradiction: route table binds card fields to SPEC 10.3, and SPEC lists only title, priority,
deadline, project, pending-proposal marker, running-claim marker: T01 plan.md:1075, SPEC.md:200.
Fix: remove `id` from board cards or amend the route contract/SPEC first.

4. MAJOR — G / overdue excludes `deferred_next_sprint` items.
Plan: reuses `overdue_list` and explicitly excludes item status `deferred_next_sprint`: plan.md:646,
plan.md:908.
Contradiction: SPEC 4.5 says overdue is tickets/items with deadline < current planning date and not
done/dropped; sprint items have no dropped status: SPEC.md:104, SPEC.md:38.
Fix: include overdue `deferred_next_sprint` items or amend SPEC and carryover logic.

5. MAJOR — G / `needs_review` approvals are not truly oldest-pending-first.
Plan: uses ticket `updated_at` as the waiting time proxy and notes it can be refreshed by later
writes: plan.md:631, plan.md:901.
Contradiction: SPEC requires approval queue ordered oldest-pending-first: SPEC.md:104.
Fix: derive `needs_review` waiting time from the `state_changed` event into `needs_review`.

6. MAJOR — I / raw day date segment can violate `day_YYYY-MM-DD`.
Plan: validates non-`today` via `date.fromisoformat(date_seg)` but returns `f"day_{date_seg}"`:
plan.md:304.
Contradiction: SPEC fixes day ids as `day_YYYY-MM-DD`: SPEC.md:48.
Fix: build day ids from the parsed date's `.isoformat()` or `day_id(parsed_date)`.

7. MAJOR — I / sprint-item deadlines are not validated as ISO dates.
Plan: passes `deadline` through create/update item paths: plan.md:220, plan.md:232.
Contradiction: SPEC requires sprint-item `deadline` to be a nullable ISO date, while `create_item`
only validates title and `update_item_field` only special-validates priority/project: SPEC.md:38,
sprints/data.py:316, :360.
Fix: add ISO date validation/canonicalization before calling item writers, or move it into
`sprints/data.py`.

Clean sections: A route completeness is clean; B grant-pair passthrough is clean for
accept/edit-accept; C literal `today` resolution is correct; F explicit section-1 writer signatures
match disk; no extra route-level event appends found where existing writers already append.
```

</details>
