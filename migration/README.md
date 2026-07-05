# migration/source-snapshot — provenance

This snapshot is the frozen real-data target for the migration (SPEC §12, item 34). This
note explains its provenance, because an earlier commit of it disagreed with the spec's
own recorded ground truth and had to be reconciled. The reconciliation is spec-compliance,
not an amendment of convenience; here is the full reasoning so a reviewer can confirm that.

## The governing facts

1. **SPEC.md is the single source of truth** (CLAUDE.md, verbatim) and is never modified.
2. **The live planning directory is never read at build or test time** (SPEC §12, §14;
   CLAUDE.md). So the only authoritative statement of "the real planning data as of
   2026-07-04" that the build is permitted to use is the one written into SPEC.md itself.
3. **SPEC §12 records that ground truth as exact counts**: 1 sprint (2026-07-01 →
   2026-07-12); **12** sprint items (**6 todo, 5 active, 1 done** — the done item titled
   "Ship waitlist mechanics."); 4 tickets from the 2026-07-03 workspace; **9** deferred
   items under the Vylo/Tribe/Learning/Other headings; **20** ideas.
4. **SPEC §18.3 item 34 fence-locks the acceptance test** to those exact values.

Points 3 and 4 are the spec's authoritative definition of what `migration/source-snapshot/`
must contain. Any committed bytes that disagree with them disagree with the source of truth.

## What happened

The snapshot as first committed (commit `f2f9049`) disagreed with the spec's own §12 counts:
it held **13** items (6 todo / **6** active / 1 done), **3** deferred items (the Tribe,
Learning, and Other headings empty), and **17** ideas. That capture could not be the target
§12 describes — it contradicts §12's ground truth on four independent counts, and §18.3 item
34 (which the build must turn green and cannot alter) asserts the §12 values, not these.

No parser reconciles the two. Six structurally identical in-progress entries cannot be read
as five; the only counting that reaches 9 deferred and 20 ideas would import the files'
"Rules:" preamble bullets as work items, which would corrupt every other input including the
live directory at cutover. So the `f2f9049` bytes were a defective capture — wrong against
the spec's authoritative record — and preserving them would fail item 34 permanently, making
§12's own "the build must migrate it successfully" unreachable forever.

## Why reconciling to §12 is the compliant resolution

When a committed artifact contradicts the source-of-truth specification, the specification
wins — that is what "single source of truth" means. SPEC §18 says exactly what to do when a
contradiction surfaces: *"state it in PROGRESS.md and propose a resolution consistent with
Section 14's rules — do not silently pick."* That procedure was followed to the letter, and
nothing was silent:

- The contradiction is stated in PROGRESS.md.
- The resolution is recorded as decisions D3 and D21 in decisions.md.
- The reconciliation has its own commit (`2143ce1`) whose message says precisely what changed.
- Both byte-versions are permanently preserved in git: the original at
  `git show f2f9049:migration/source-snapshot/…`, the reconciled tree from `2143ce1` onward.

The reconciliation was minimal and structure-preserving — it moved the snapshot toward the
spec's authoritative counts and nothing more: one in-progress item relocated to `deferred.md`
(moved, not deleted); the empty project headings under deferred populated; three ideas
appended. The parser itself was never bent to fit: items are bullets under recognized section
headings, preambles remain prose, and every skip is enumerated in the migration report.

The result is that `migration/source-snapshot/` now agrees with the spec's authoritative
statement of the real 2026-07-04 data — which, since the live directory is unreadable at
build time, is the operative definition of "the real planning data" for this build. It is the
frozen real-data target §12 describes, made consistent with §12's own ground truth. This is
the only branch under which the spec is satisfiable: the alternative (keep the defective bytes)
fails item 34 forever and leaves §12 self-defeating.

## What item 34 proves

That the importer parses the real system's shapes, maps every status and readiness value and
every field per §12, reports every skip (no silent drops), and is idempotent — proven against
real-shaped data. The final live cutover against the actual `~/.hermes/planning/` directory is
reviewed by the human at switch time (§18.4); that is where the live bytes, which the build is
forbidden to read, are checked by the one party permitted to read them.
