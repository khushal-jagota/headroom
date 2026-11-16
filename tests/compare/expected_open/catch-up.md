# Temporary catch-up gaps

Retained cases: 11, listed individually in checkpoint.json with the prefix `catch-up`.

Evidence: https://github.com/khushal-jagota/headroom/actions/runs/43904665539

These exact failures belong to the second catch-up PR: final Consequences ceilings in restored records, canonical writes and CLI fixtures; fresh restart cleanup evidence; workspace Project identity; and the transition from linked-worktree fixtures to independent clones. They are not launch-token failures.

The merge checkpoint updates stale fixture signatures and explicit clone author settings. The scratch fixture still assumes linked-worktree removal. The second PR must migrate that fixture and prove clone/custody-safe retirement rather than preserve that assumption.

Cases remain executable strict expected failures. Remove entries as behavior and its fixtures reach parity. Do not add a directory-wide skip or normalize the changed contract away.

The dataset CLI case reaches its actual comparison after fixture repair. [Run 43910025587](https://github.com/khushal-jagota/headroom/actions/runs/43910025587) shows the exact remaining gap: TypeScript Ticket lists still emit `waiting_to_closeout`, which frozen staging removes. The second PR removes that projection.

The launch-token port separates three additional CLI declaration/work-sequence cases that now reach explicit proposal revision guidance, plus a populated Manager workspace case that still emits `waiting_to_closeout`. These remain catch-up work, not authentication gaps.
