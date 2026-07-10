# t_new01 implementation plan

1. In `SharedGateway`, add one helper which starts a fresh chat session through the existing
   `_resume_or_create(child, None, CHAT_SOURCE)` path, calls the key callback only after the returned
   live and durable identities are bound, and returns the system result `New session started.`.
2. In `run_command`, intercept only the literal command `/new` immediately after obtaining the child
   and before ordinary session resolution. Return the helper result.
3. In `stream`, intercept only command mode plus literal `/new` at the same early boundary. Yield the
   new session key, then the existing token/done system-result sequence, and return.
4. Add a gateway regression which begins with an old key, runs synchronous `/new`, then sends an
   ordinary message. Assert exact RPCs `session.create`, `prompt.submit`; the submit uses the new live
   handle; the result and callback use the new durable key; and no resume/slash/dispatch RPC occurs.
   Inside the callback, assert `gateway.live_session(new_key)` already exists with the returned live
   handle so persistence cannot race ahead of binding.
5. Add streaming regressions both with an old key and with no prior key. Prove one create, one callback,
   the exact session/token/done chunks, no old-key resume, no throwaway key, and the same callback-after-
   binding ordering. Parameterize literal-boundary coverage across synchronous and streaming entry
   points so `/new title` must still follow generic command dispatch in both.
6. Document the fresh Hermes-conversation behavior in `docs/chat.md`. Keep the visible Panels
   transcript, public contracts, schema, and upstream Hermes unchanged.
