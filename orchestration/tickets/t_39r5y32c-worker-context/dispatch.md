# Implementation dispatch

Implement the accepted keyed worker-context subsystem in the isolated worktree `/tmp/panels-t_39r5y32c`.

- Keep contracts, persistence, and delivery composition separate.
- Use key/revision compare-and-ack semantics.
- Mark all relevant human Ticket/Review edits, including actor-gated per-field notes.
- Inject context only at actual Hermes `prompt.submit`, covering automatic, already-claimed, sync, streaming, and model-backed command paths.
- Keep Panels chat text unchanged and leave non-model commands pending.
- Cover both gateway submission helpers, the return-for-revision path, coalescing, concurrent updates, failures, and unedited/agent writes.
- Update runtime docs and run focused tests.
- Do not modify unrelated files, commit, or push.
