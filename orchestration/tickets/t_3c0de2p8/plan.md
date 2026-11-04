# Historical attention

Chief reads where owner involvement went over selected dates. The owner explicitly excludes current attention demands.

Base: `99f6d229f`, fetched from `origin/staging` before this worktree was created.

## Shared contract

`src/planner/attention/contracts.py` owns request enums, scope, date range, destination metadata, timestamped events, gate intervals, and evidence coverage. The parent writes this contract. Implementers request any contract change before they use it.

Implementation tasks use one isolated worktree and disjoint file ownership:

- History: `attention/history.py`, one migration, canonical approval/deletion hooks in `tickets/data.py`, gate hook in `notifications/attention.py`, and history unit tests.
- Reads: `attention/data.py`, `attention/actions.py`, `attention/logic/summary.py`, and calculator/data unit tests.
- Transport and retained values: `attention/api.py`, `attention/cli.py`, `attention/collection.py`, registrations, focused documentation, and API/collection integration tests.

The parent owns shared contracts, integration, environment setup, evidence, and final verification. Implementation agents own their slices. A separate agent reviewed the plan before implementation.

## Plan review decisions

The independent review permits implementation with these required safeguards:

- Close gate history before Ticket deletion. That path bypasses ordinary attention capture.
- Deduplicate authenticated sender and message identity before date or scope filters. Preserve row identity for absent message IDs.
- Use server timestamps and authenticated sender metadata. Never infer ownership from sender labels or text.
- Keep coverage separate for messages, approvals, and gates. Unknown approval history cannot become zero owner involvement.
- Clip intervals to the requested range and snapshot. Compute unions from original intervals.
- Separate direct completion of a user-owned field from explicit acceptance of a proposal.
- Preserve deleted Ticket snapshots without cascading history deletion.
- Keep collection on an explicit read-only database path and reuse the existing runtime and scheduler.

Existing open gates start with an unknown original opening and a known observation start. Older migrations can call current capture functions, so the hook must tolerate absent history tables.

## Test admission

No existing tests cover these new historical calculations. Unit fixtures protect distinct risks: DST date boundaries, comparison bounds, duplicate replay across windows, scope and group totals, unsliced shares, overlap unions, open intervals, missing history, and goal unattribution. History tests exercise canonical writes, rollback, rejection, holder transfer, deletion, and migration activation. These risks can silently return plausible incorrect figures.

One integration flow connects HTTP and CLI output. Another runs the real source runtime and reads retained values through the existing dataset API. Mock-only tests cannot establish those boundaries. Existing server and source fixtures supply the hosts. No browser host is necessary because the Ticket adds no frontend.

Runtime checks unset inherited live paths and use private test state. Worktree dependencies are independent and come from `scripts/dev setup`. No production server operations belong to this work.

## Review and delivery

An independent implementation review follows focused checks. Resolve every actionable finding, then record the checked commit and evidence in the canonical Implementation proposal. Consequences will use the ordinary pull request into staging, after approval. Production deployment and live skill application remain separate owner-controlled actions.

## Implementation review corrections

The independent review found three correctness gaps. All enter the implementation scope because they affect the approved figures or links:

- System loop prompts lacked sender metadata. Without trusted provenance, even future agent-only work could never establish absence of owner involvement. The canonical conversation contract now includes an optional `system_generated` marker. New internal admissions set it, queued messages retain it, and legacy rows keep unknown provenance. The public generic send route supplies its authenticated caller. Request text and sender labels cannot set provenance.
- A deleted Ticket's approval snapshot described that approval, not every message to that Ticket. Message fallback keeps its identity and title but strips unsupported Project and Manager occurrence attribution.
- Links must use the app's supported workspace hash addresses. Project groups have no detail route and therefore no URL.

Focused cases protect each gap using the existing conversation writer/API fixtures and data reader. The fake conversation system uses a separate observation surface, so it needs no canonical event marker change. No browser fixture is added. Group relation metadata now uses only the selected and comparison periods, rather than unrelated older events.

The migration test checks one migration head and the new revision's ancestry. It does not pin the application forever to this revision as its latest migration.

Final integration uses the existing SQLite rowid to break same-second replay ties, so the first persisted recipient wins. The connected reader case now covers that tie without another fixture. The independent review accepts this correction. The raw diagnostic oracle also recognizes the new provenance field, and its existing fixture exercises that field without a new test case.
