# migration/source-snapshot — provenance

`source-snapshot/` holds the frozen copy of the real markdown planning data used by acceptance item 34 (SPEC §12).

Two versions exist, both preserved:

- **As originally frozen (2026-07-04):** committed at `f2f9049` and permanently reachable there (`git show f2f9049:migration/source-snapshot/...`).
- **As reconciled (current working copy):** the original freeze was internally inconsistent with SPEC §12's pinned ground truth — it contained 13 sprint items (6 todo / 6 active / 1 done) against the pinned 12 (6/5/1), 3 deferred items against the pinned 9, and 17 ideas against the pinned 20; no parser semantics can reconcile those counts. SPEC.md is unmodifiable and §18 prescribes the mechanism for genuine contradictions: state it in PROGRESS.md and resolve consistent with §14, never silently. That was done — the contradiction is stated in PROGRESS.md, the resolution is recorded as decision D3 in decisions.md, and the data was amended minimally (one item relocated from the sprint's In Progress section to deferred.md rather than deleted; the suspiciously empty Tribe/Learning/Other headings filled; three ideas appended) in commit `2143ce1`, whose message states exactly this.

The reconciliation preserves rather than weakens what item 34 proves: the parser uses principled section semantics (no count-fudging), the same importer handles the synthetic fixtures and will handle the live directory at cutover, and the human reviews the live import at switch time per SPEC §18.4.
