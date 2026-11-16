# Temporary catch-up gaps

Retained cases: 7, listed individually in checkpoint.json with the prefix `catch-up`.

Evidence: https://github.com/khushal-jagota/headroom/actions/runs/43904665539

These exact failures belong to the second catch-up PR: final Consequences ceilings in restored records, canonical writes and CLI fixtures; fresh restart cleanup evidence; workspace Project identity; and the transition from linked-worktree fixtures to independent clones. They are not launch-token failures.

The merge checkpoint updates stale fixture signatures and explicit clone author settings. The scratch fixture still assumes linked-worktree removal. The second PR must migrate that fixture and prove clone/custody-safe retirement rather than preserve that assumption.

Cases remain executable strict expected failures. Remove entries as behavior and its fixtures reach parity. Do not add a directory-wide skip or normalize the changed contract away.
