# Panels simplification

Owner request: remove complexity that buys too little while preserving useful intent.
The success measure is fewer concepts and less bookkeeping in everyday Panels use,
not fewer worker types or an arbitrary line-count target. Remove first; deepen a
module only after removal exposes a real need. The conversation system and agent
backends, including their frontend implementation and contracts, are protected.

## Execution

Base: origin/staging b7ca8e047967e405feeebe058f5cd2ef82b2c2e5.
Worktree: `/Users/khushaljagota/Coding/planning-v2-worktrees/panels-simplification`.
Branch: `codex/panels-simplification`.
The original checkout and its untracked uv.lock are left alone.

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

## Progress

- Current remote baseline fetched; local staging was 66 commits behind and was not
  used as the audit baseline.
- Final-only verification guidance updated before implementation.
- Read-only live evidence informed the decisions; aggregate counts are in the scoped plans.
- Atlas removed and independently reviewed (58b161a6).
- Sprint documents implemented and independently reviewed (1c1e3b80): twelve prose fields become four.
- Ticket guidance implemented and independently reviewed; the combined migrations pass focused checks.
- Backlog and test pruning remain in isolated implementation worktrees. E2E has reached 19 cases from39.
- No full verify run has occurred; the final settled tree owns it.
