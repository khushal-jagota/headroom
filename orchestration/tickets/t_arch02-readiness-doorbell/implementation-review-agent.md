# Independent standards/spec implementation review

1. **[P2] The required failure/no-op acceptance matrix is incomplete.**
   Missing recording-doorbell cases are malformed/active Chief requests, missing
   delete, same-state rejection, an actual Day database failure, and Link constraint
   failure. Exact Chief replay also checks only the ring count rather than unchanged
   canonical state and events.
2. **[P2] The staged slice contains unrelated gateway-correlation design work.**
   Concurrent changes in `PROGRESS.md` and `decisions.md` belong to a separate effort
   explicitly excluded from item 2.

Disposition: accepted. The missing behavioral proofs are being added without production
changes. The unrelated memory-file changes are preserved in the working tree but removed
from the item 2 staged slice; item 2's own current-state PROGRESS hunk will be staged
separately.

The first follow-up found one further valid test-oracle defect: the exact Chief replay
snapshot included `updated_at`, although the contract intentionally ignores only that
refresh. That made the assertion depend on both calls sharing an integer second. The
corrected deterministic-clock test separately proves `updated_at` advances, excludes
exactly that column from semantic equality, and still compares every other Ticket column
and every event. Production code was unchanged.

The final follow-up inspected the corrected staged slice and returned:

```text
NO VIOLATIONS
```
