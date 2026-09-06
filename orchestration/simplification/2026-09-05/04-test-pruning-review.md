# Independent review of the pruning plan

Verdict: proceed with the deletion/consolidation cohorts only under the conditions
below. The numerical budgets are targets, not evidence that individual tests are
redundant. This bounded review inspected the named large suites and sampled the
proposed replacement boundaries; it did not execute tests or audit every assertion.
Implementation still needs the planned independent diff review.

Static AST recount confirms 138 Python unit files / 1,526 definitions, five
integration files / 14 definitions, and 18 E2E files / 39 definitions at the audited
baseline. The plan's overall lower-bound arithmetic is consistent:
1,815 + 14 + 39 + 332 + 12 = 2,212; its suite ceilings sum to 1,106.

## [P1] An oldest-only fixture cannot replace all later migration-state proofs

The twelve-file migration cohort must name its exact files and the source states
that replace each test before deletion. Populating every table in the oldest schema
does not populate tables or states introduced later. For example,
`test_notification_subject_migration.py` starts at `notifications` and creates a
fact/decision/intent/subscription/delivery graph; a baseline-only fixture has none
of those rows when the later subject migration runs.

Use staged upgrade-and-seed checkpoints inside the consolidated journey, or keep
the tests whose necessary inputs cannot arise from the oldest seed. Prove the
transformation at the relevant revision or its surviving final invariant; a final
foreign-key check alone does not prove content or authority mapping.

Specifically retain the risks in `test_ticket_conversation_history_migration.py`:
valid unique active pointers, duplicate-pointer rejection, recovery from an exact
standard opener, missing references, malformed payloads and lookalike-opener
rejection. Its filename is outside `test_conversation_migration.py`, but it protects
conversation identity and the migration parser. Likewise retain the one-approval
gate migration's parked proposal/control metadata and retired-value rejection,
the Planning Day workflow's stranded-stage reconciliation and historical content,
and the notification migration's existing delivery graph. These may share setup;
they cannot disappear because the final schema has the right columns.

## [P1] The proposed Markdown browser replacements exercise different code

`test_failed_markdown_save_retries_exact_pending_source_without_more_input`
(`test_ticket_file_previews.py:1112`) edits a managed Markdown field next to a loaded
preview, fails the first field PUT, and retries after focus/blur without new input.
It protects the editor's retained pending source after its failed-save repaint.
`conversation-draft-transaction.test.ts` concerns the Conversation composer and its
images/files, not InlineEdit or the managed Markdown field. It is not a replacement.

Keep this risk, preferably move it to a real-DOM InlineEdit/managed-Markdown test
with mocked writes, and only then remove its live-backend E2E. Do not claim the
existing conversation draft transaction proves it.

The proposed deletion of
`test_loaded_preview_proposal_approves_without_edited_body` also needs explicit
replacement. The retained pending-proposal editing test edits plain text; it does
not prove asynchronous preview hydration leaves an untouched proposal clean.
Keep an assertion that loading Markdown/HTML previews then approving sends no
`edited_body` and preserves exact source. Add this to the retained preview or
approval journey, or prove it in the actual editor/card DOM with mocked requests.

## [P1] Conformance is not a replacement for wire or persistence boundaries

There is real duplication between the shared 51-case conformance suite and simple
system happy paths. Delete those only with a per-case retained-proof pointer.
The real-process suite checks Python contract objects plus actual backend writes;
it does not exercise the HTTP serializer or every persisted event shape.

For example, `test_conversation_api.py:918` proves all send fates serialize as their
actual tagged JSON shapes, including queued position and refusal reason. Preserve
that transport table even when deleting repeated backend-state assertions from it.
Keep sender-message identity through HTTP to the recorded row, stale active-pointer
rejection, multimodal byte delivery, replay/tail gaplessness, and held-message
mutation wiring at their actual boundaries.

Similarly retain storage coverage for every persisted event variant, legacy readable
shape and transactional write; combine fixtures/assertion tables where that removes
setup, rather than replace shape coverage with one convenient sample. Keep the
named start-wiring active-pointer races, held/refused configuration timing, and
reset/discard behavior. Those are Ticket association rules absent from generic
conversation conformance.

Replacing the full in-memory conformance run with a smoke is acceptable only when
the smoke covers the fake behaviors downstream domain tests depend on. Keep the
real-process conformance suite unchanged and do not modify a fake or production
implementation merely to satisfy the smaller test inventory.

## [P2] Preserve one real retained-page release cutover proof

`test_release_freshness.py:11` retains the old HTML document while its same-origin
server is replaced, then exercises the actual update control and verifies the new
document boot SHA. `release-monitor.test.ts` supplies fake document/window objects
and a mocked fetch/reload; it proves the monitor decisions but not shell mounting,
server metadata and real reload working together.

Fold these assertions into the retained restart/reconnect browser journey or retain
the existing case. Its standalone file is expendable; the old-document/new-server
boundary is not established by the cited unit test.

## [P2] Deployment assets are not exclusively source-string tests

Do not delete `test_deployment_assets.py` wholesale on the stated source-scanning
rationale. It also executes checked-in scripts: the Panels wrapper follows a moved
current-app symlink (line 13), runner registration executes the setup script (line
165), predeploy backup overrides ambient Hermes paths (line 270), service control
uses user-scoped systemd (line 312), and a launchctl restart recovers a loaded job
with no process (line 350).

Retain or relocate those actual script boundaries unless the replacement installed
integration invokes those exact assets and conditions. Core deployment-function
tests do not automatically prove their shell wiring. Static spelling/geometry and
retired-file absence assertions can be removed independently.

## [P2] Measure half with the same unit and an explicit E2E rounding rule

Twenty retained E2E cases out of 39 removes 19, or 48.7%. If “at least half of E2E”
is an exact acceptance condition, the ceiling is 19, not 20. Otherwise label 20 as
approximately half while preserving the strict overall 1,106 target. The keep-list
count itself is correct: its 19 entries include two live-update cases.

Case reduction must mean removed redundant execution/setup, not merely moving the
same independent tests into an internal loop so the definition counter sees one.
Count new smoke cases, new migration/doc/guidance tests and converted legacy scripts
once in their final runner. Use a disjoint per-file cohort manifest to prevent
shared CLI/domain or inherited conformance cases being counted twice. If a cohort
needs more coverage than its budget, find another genuinely redundant cohort;
do not remove a named unique risk solely to hit that local ceiling.

## Implementation conditions

Keep the plan's already-restored notification, Review keyboard, exact skill-draft,
paired-Ticket, trusted-origin, claim/release, prompt delivery and receipt, date,
atomic writer and backup integrity proofs. Feature owners retain their in-progress
guidance, Sprint document and backlog tests until contracts settle.

Each deletion/cohort records the exact retained test and the failure it still
catches. Consolidation may share fixture setup and follow one coherent journey.
The replacement migration fixture and the relocated real-browser/editor/script
proofs must exist before their originals are removed. No production Conversation,
backend or generic context code changes belong to pruning. Run only the named
focused gates after each completed cohort; the single final `./verify` remains the
repository completeness claim.

## Verification-instrument addendum — approved with two preservation conditions

Removing the retired SPEC substring/empty-test scanner and handwritten CSS parser
is a useful deletion. `compileall src/` adds no separate syntax proof while ruff
and mypy continue covering src. `verify_lib.py` can disappear when argparse owns
the settled mode grammar in verify.py. Keep the same full/fast/integration/e2e
selection, timeouts, stale-JUnit removal, reports, and nonzero failure behavior.
In particular argparse's ordinary error exit must not silently remove the existing
`VERIFY: FAIL` report for invalid invocation if the reporting contract stays fixed.

First condition: do not claim the production Vite build parses the two shared CSS
assets. `web/index.html` links `/assets/tokens.css` and `/assets/app.css`; the built
HTML preserves those links and FastAPI serves them directly. The retained
`web/tests/file-preview-browser.test.mjs:41,52` explicitly imports both files and
invokes Vite build, so that retained frontend gate provides the actual parser
replacement. Name and retain it (or another equivalent existing import/build
boundary) when removing the custom checker. There are currently no JavaScript
files under assets, so the removed per-asset node-check arm has no current input.

Second condition: `test_instrument.py:124–154` also proves PLAN_FAKE_NOW is honored
only with PLAN_TEST_MODE and otherwise builds a RealClock. This is product clock
isolation, not a test of the retired scanner. Move that compact assertion into the
config/clock suite, or point to an existing equivalent, before deleting the file.
Parser/scanner synthetic examples themselves can be deleted with their machinery.

Settled scope from root: the instrument change removes only the retired SPEC
skip-pattern/empty-test scanner and its dedicated synthetic assertions. Keep the
existing CSS parser/build gate, mode parser and reporting, and real
PLAN_FAKE_NOW/PLAN_TEST_MODE config/clock proof. Do not introduce argparse or another
CSS build arrangement. This narrower scope supersedes the broader addendum proposal.
