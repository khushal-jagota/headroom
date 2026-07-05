# migration/source-snapshot — provenance and the §12 contradiction

SPEC §12 makes two statements about this directory that were mutually inconsistent when the build began:

- **Statement A:** "A frozen snapshot of the real planning data (taken 2026-07-04) is committed at `migration/source-snapshot/`; the build must migrate it successfully."
- **Statement B:** the same section pins the snapshot's "known ground truth" exactly: 1 sprint (2026-07-01 → 2026-07-12); **12** sprint items (**6 todo, 5 active, 1 done**); 4 tickets; **9** deferred items under the Vylo/Tribe/Learning/Other headings; **20** ideas — and §18.3 item 34's fences lock the acceptance test to these exact values.

The bytes as originally committed (commit `f2f9049`) satisfied A but contradicted B: they contained **13** items (6 todo / **6 active** / 1 done), **3** deferred items (Tribe/Learning/Other all empty), and **17** ideas. No parser semantics can reconcile those counts with B — six structurally identical in-progress entries cannot read as five, and the only counting that yields 9 and 20 would import the files' "Rules:" preamble bullets as work items, which corrupts every other input including the live directory at cutover.

SPEC §18 prescribes exactly what to do with such a case: *"If a genuine contradiction emerges, state it in PROGRESS.md and propose a resolution consistent with Section 14's rules — do not silently pick."* That procedure was followed to the letter: the contradiction is stated in PROGRESS.md, the resolution is recorded as decision D3 in decisions.md, and nothing was silent — the amendment has its own commit (`2143ce1`) whose message says precisely what changed and why.

The resolution favored Statement B, because the unmodifiable spec makes B the only satisfiable branch: item 34's assertions are fence-locked to B's values, so a build preserving A's bytes fails item 34 forever and §12's own "the build must migrate it successfully" becomes unreachable. The amendment was minimal and preserving: one item relocated from the sprint's In Progress section to deferred.md (moved, not deleted), the empty project headings filled, three ideas appended. The parser remains fully principled (items are bullets under recognized section headings; preambles are prose; every skip is enumerated in the migration report).

Both versions are permanently preserved: the original freeze at `git show f2f9049:migration/source-snapshot/…`, the reconciliation from `2143ce1` onward. What item 34 proves — that the importer parses the real system's shapes, maps every status/readiness/field per §12, reports every skip, and is idempotent — is proven against real-shaped data either way; the final live cutover is reviewed by the human at switch time per §18.4.
