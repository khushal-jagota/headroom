# ACP-07 exact compaction prompt failure reason — implementation plan

1. In the uncancelled-exception branch of `_Actor._settle_prompt`, retain the concrete exception and
   ask the exact lease's strategy for the optional display-safe reason. Validate the return strictly;
   any invalid result or hook error falls back to the current generic reason. Call the existing
   `_fail_generation` once with the selected reason; do not add another settlement path.
2. In `CodexAcpTurnStrategy`, implement the hook for exact `/compact` or an already-active compaction
   state, format `TypeName: message`, and reset the provider-local compaction state. Keep ordinary
   prompt failures private and remove any impossible failed-tag normalization/test.
3. Add narrow broker and provider tests for the contract, then run focused Ruff, strict Mypy, the
   affected turn-broker/provider/in-place/Hermes tests, and diff check. Record results; no canonical
   verifier or live dogfood belongs to this slice.
