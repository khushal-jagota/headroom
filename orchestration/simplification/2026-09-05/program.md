# Panels simplification

Owner request: remove complexity that buys too little while preserving useful intent.
The success measure is fewer concepts and less bookkeeping in everyday Panels use,
not fewer worker types or an arbitrary line-count target. Remove first; deepen a
module only after removal exposes a real need. The conversation system and agent
backends, including their frontend implementation and contracts, are protected.

## First-pass execution

Base: origin/staging b7ca8e047967e405feeebe058f5cd2ef82b2c2e5.
Worktree: `/Users/khushaljagota/Coding/planning-v2-worktrees/panels-simplification`.
Branch: `codex/panels-simplification`.
Implementation used the isolated worktree. The original untracked uv.lock is
preserved; the original staging checkout advances only at Closeout.

The owner explicitly reserves `./verify` for the very end. Individual chunks use
named focused gates. No broad baseline run. Audits count tests without running them.
The owner clarified that removing at least 50% of actual test cases is a firm
acceptance criterion, including at least half of E2E (at most 19 of the baseline 39).
New tests count against the final inventory. Critical behavior must still have credible proof;
deletions will name what is retained and why, not manipulate collection counts.

Root owns contracts, decisions, integration, and final verification. Agents own
implementation within assigned files. Astra handles semantic audits and intricate
changes; Sol handles bounded inventories and mechanical work. Model names from the
repository's older Opus/Sonnet guidance are unavailable in this harness, so these are
the corresponding capability choices. Independent review precedes implementation
and checks settled chunks. No two agents write the same files concurrently.

## Evidence rules

For each candidate: describe the useful intent, trace who writes and reads it, name
what disappears and what remains, and define the minimum behavior check. Populated
fields and agent tool usage alone do not demonstrate value. Prefer evidence of a
decision made easier, a user action saved, or a correctness risk controlled.

Initial parallel audits cover ticket/sprint semantics, tooling/UX, and tests.
Atlas is the first confirmed removal candidate: it duplicates canonical Workspace
and Review state in a separate 3D rendering without owning useful unique data.

## First-pass progress

- Current remote baseline fetched; local staging was 66 commits behind and was not
  used as the audit baseline.
- Final-only verification guidance updated before implementation.
- Read-only live evidence informed the decisions; aggregate counts are in the scoped plans.
- Atlas removed and independently reviewed (58b161a6).
- Sprint documents implemented and independently reviewed (1c1e3b80): twelve prose fields become four.
- Ticket guidance implemented and independently reviewed; the combined migrations pass focused checks.
- Backlog is implemented and independently reviewed; unscheduled Tickets and briefs open in Workspace.
- Test pruning is complete: 2,505 collected cases become 1,151 (54.1% removed),
  including E2E from 39 to 19. Expanded runtime collection corrected the earlier
  frontend estimate. See `08-final-measurements.md` for the comparable inventory.
- Final `./verify` passed all seven gates after repairing the concrete failures
  from its first attempt. Full output is in `verify.log`. No full run occurred
  during the implementation chunks.

## Second-pass status

The deeper simplification is implemented in the isolated
`panels-simplification-deeper` worktree. Per-field proposal slots are replaced by flat
saved values plus one current proposal. Durable Outcomes are separated from explicit
Sprint commitments and Ticket scheduling, with reviewed carry and placement behavior
and the Outcome interface follow-through. The three concrete UI findings from the
combined review are repaired in the affected routes and their existing browser
harnesses.

The independent combined implementation review is approved with no unresolved
findings. The combined migration rehearsal passed on the read-only API projection;
`15-api-migration-rehearsal.md` records its exact preservation results and the raw
production facts that the API fixture cannot establish. The final comparable inventory
is 1,155 collected cases, 53.9% below the 2,505 baseline, with E2E at its 19-case
ceiling. The post-inventory UI repairs expanded only existing standalone `.mjs`
harnesses and did not change that count.

The second pass is not closed out. Its single final `./verify`, incorporation of
current `staging`, any resulting repair and verification, advancement and exact push
of `origin/staging`, remote-ref confirmation, rolling-PR check, and task-owned cleanup
remain pending with root.

## First-pass rehearsal on visible live records

Ran both migrations in sequence on a private temporary database seeded from the
read-only live API exports: 778 Ticket records and seven Sprints. All 642 nonempty
visible note sections (605,070 UTF-8 bytes) matched the new guidance documents exactly.
All 22,349 UTF-8 bytes of consolidated Sprint prose matched their new documents;
primary bets, identities, dates, and timestamps were unchanged. SQLite integrity and
foreign-key checks passed. No live records were written.

This exercises visible exported prose, not a full production database backup. Raw
dual-key historical notes and relationship preservation are covered by the separate
populated migration fixtures. Private exports and rehearsal script stay under
`data/simplification/`, outside git.

## First-pass final integration repairs

The first final `./verify` found stale migration-to-HEAD expectations for retired
field notes, a few strict-typing fixture references to the old API, one import
format issue, and two macOS test assumptions. The historical migration seeds
remain; their assertions now explicitly prove exact guidance preservation. The
attachment assertion uses the canonical resolved path, and the proposal editor
test uses the platform-aware Select All shortcut. Neither change loosens its
exact outgoing content assertions. Focused checks passed, and the independent
review in `07-test-integration-review.md` approved the repairs. Conversation and
backend implementation code remains unchanged.

The normal local suite retains 20 opt-in real-provider cases (seven Claude, nine
Codex, four Hermes). These require their explicit `PANELS_REAL_*_TESTS=1` settings
and provider setup, and are not enabled by this program. They are included in
both collection inventories; the reduction is actual deletion, not new skips.

## First-pass verification result

The final settled tree passed Ruff, strict mypy over 352 files, 926 enabled unit
cases, the compile/CSS checks, Svelte checking, the production build, frontend
tests, eight integration cases, and all 19 E2E cases. The 20 real-provider cases
remain opt-in as described above. Vitest reports 334 passes when its typecheck
project is included; the comparable runtime-only inventory is 178 cases. All
11 standalone frontend scripts also passed. Full output: [`verify.log`](verify.log).

Independent reviews have no unresolved findings. The protected conversation,
backend, delivery, and conversation-start paths have no implementation diff
against the fetched baseline. Existing migration files are unchanged; only the
two new migration revisions are added. Integration ends at `origin/staging` and
the existing rolling staging-to-main PR; main deployment remains a later owner
action. Task-owned services, local state, worktrees, and branches are removed
after the remote staging revision is confirmed. The visual before/after artifact
is retained in the original checkout under `.lavish/panels-simplification/`.
