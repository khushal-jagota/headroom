# ACP-05 asynchronous compaction handoff plan review resolution

The sole review blocker is accepted.

The contract and implementation plan no longer claim that the hub's general `_source_capture` buffers
candidate traffic while N is ready. The active compaction transition now owns one aggregate-bounded FIFO
quarantine with exact source and validated session identity. Before commit it distinguishes ordinary N
from both legal candidate origins: post-fork traffic emitted by the original child and post-load traffic
emitted by the fresh unpublished child. Commit consumes the FIFO once, publishes only the winning N+1
entries between private replay and ready, and discards loser entries. Abort, fatal failure, external
winner, expiry, cancellation, shutdown, and final settlement clear it synchronously.

The required two-browser ordering and settlement regressions were added to the plan. No second review
round is needed because the review found no other blocker and this resolution implements its exact
requested correction without changing the other reviewed invariants.
