# ACP-00 plan review disposition

One focused independent review was requested. It returned `NOT READY` with one blocker and one
high-value correction. Both are accepted:

1. **Official client-side SDK path — accepted.** The plan now requires the reference subject to
   launch the scripted agent through `acp.stdio.spawn_agent_process`, implement the official Client
   callbacks, drive the returned SDK connection, and use the observer seam for the stdout proof.
   Only the production wrapper remains deferred to ACP-01.
2. **ACP union ownership proof — accepted.** The TypeScript test now combines fixture assignability
   with a deterministic source-boundary assertion that requires SDK/donor imports and rejects local
   declarations of ACP-owned union/type names.

These are bounded proof corrections and do not change the ticket architecture or public contracts.
Per the owner direction to keep review proportional, no second broad plan review is commissioned.
The amended plan is ready for implementation.
