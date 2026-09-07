# Test integration review

Root reviewed the committed removal cohorts and the independent review/fixes in
04b and 04c. Meaningful worker claim, release, refusal, queued delivery, actual
prompt, planning-date, migration rollback, backup integrity, provider translation,
HTTP wire, and real-process conformance proofs remain. Runtime provider/conversation
code is unchanged. Source spelling, duplicate presentation mappings, repeated layer
tests, and equivalent input matrices were removed as actual cases.

Root checked the targeted review fixes: Conversation recovery closes the old tail,
reads from its last sequence, and opens a replacement without loss or duplication.
The compact HTML journey proves real sibling styles/image loading, script interaction,
and changed-file refresh while the Ticket remains mounted. It replaces an existing
case, keeping E2E at 19. No independent cases were hidden inside a counting loop.

## Merge decisions

- Pruning deletions win for retired auth-route, CLI projection, readiness-wake, and
  query-catalogue mirror files. Feature shape changes apply only to surviving proofs.
- Retained one existing bounded Ticket-read scenario because the new Backlog depends
  on its SQL filter/search/paging behavior; frontend HTTP fixtures do not prove it.
  The two other cases in that file remain deleted.
- Retained approval preserving Ticket guidance, alongside the surviving revision
  case. The redundant data-layer replace/append/clear test stays deleted because
  the guidance API journey exercises that writer.
- Five CLI/generic-field/type-ingress conflicts were resolved by an isolated agent:
  all pruning deletions preserved; new feature proofs add five collected cases.
- One canonical migration-head constant remains in test_db, updated to ticket_guidance.
  Historical transformation tests no longer duplicate that exact-head claim.
- Both new frontend feature scripts remain in the gate.

Final integrated collection found 946 unit, 8 integration, 19 E2E, and 178 Vitest
runtime cases: 1,151 total against a 2,505-case baseline. That is 1,354 cases
removed, or 54.1%. The baseline Vitest count is 528 expanded runtime cases; the
earlier 332-definition estimate was not used. Legacy Node scripts are excluded
from both totals. Commands and log hashes are recorded in
`08-final-measurements.md`. This review found no unresolved meaningful coverage
defect.

## Independent review of final integration repairs

Approved with no actionable findings. Reviewed only the test changes since
`ec81e965`, including the subsequently authorized two-line E2E keyboard repair,
against the first failed run in `data/simplification/verify-first-attempt.log`.
No tests or verification were run by this reviewer.

- The typing fixture uses the real guidance replacement/append signatures after
  deletion of field-note APIs. Plain-string field checks remain on proposal,
  acceptance, value editing, and pure decision functions; their intent is intact.
- Importing `project_record` from its defining module retains the same manifest
  order, default non-expansion, and exact expanded-content assertions. The explicit
  migration-fixture type annotation and bounded-read import spacing change no
  runtime behavior or assertions.
- Historical migration tests still seed historical note shapes and run through
  current schema creation. They now assert exact migrated guidance separately from
  exact value/proposal slots. Proposal provenance, control state, timestamps, and
  relationship checks remain; the planning-day case strengthens whole-field
  preservation and adds whitespace-sensitive historical guidance. The schema
  comparison removes the new guidance column only after asserting its complete
  name/type/nullability/default/key tuple, preserving the old-shape comparison.
- The Claude attachment assertion still checks the exact complete provider message.
  Resolving its expected path matches the unchanged managed-file resolver
  (`files/logic/paths.py:83`) and addresses the logged `/var` versus `/private/var`
  alias difference; it does not normalize or discard actual provider output.
- `ControlOrMeta+A` performs the intended select-all on macOS as well as other
  platforms. The failed E2E log shows text was previously prepended. Both exact
  request-body and persisted-value assertions remain unchanged, so the repair
  restores the editing gesture without accepting the incorrect result.

These are contract and platform fixture repairs, not suppression of the observed
failures. Root's focused results are recorded separately; the final canonical run
still owns repository-wide completeness.
