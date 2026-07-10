# t_hs01 plan review

Reviewer: Codex CLI `gpt-5.5`, read-only sandbox, high reasoning effort.

## Full review output

- **High - `Implementation order`, step 5:** The plan defers the authoritative full `./verify` gate
  to `t_hs04`. AGENTS.md says `./verify` is the source of truth and must run after changes land; hs01
  cannot be declared done on focused tests only. Change step 5 to run focused tests first, then full
  `./verify` for hs01.

- **High - `Define lifecycle without becoming a scheduler`:** `Lost` conflates child death with an
  unknowable submit outcome. A `prompt.submit` timeout can still be followed by ordered Hermes events
  from a live child; treating that as lost risks an inferred terminal and dropped events. Split this
  into child-offline state versus per-attempt `unknown`; only child death/shutdown closes ingress,
  while unknown submit attempts keep the session ingress alive and never retry.

- **Medium - `RED acceptance seams`:** Add explicit RED tests for create and resume events both before
  and after the RPC response frame but before caller attachment/binding. Current wording could pass
  with only `events_before`, leaving a post-response/pre-bind listener gap untested.

- **Medium - `Compose without migrating callers` / `Files permitted`:** The plan says server
  composition may receive “narrow constructor/shutdown adaptation,” but `src/planner/core/server.py`
  is not in the permitted file list. Either explicitly forbid server changes and preserve
  `_build_role_gateways` as-is, or add `server.py` to scope with a RED test proving the employee and
  Chief gateways remain separate.

## Resolution

All four findings are accepted and the plan is changed:

1. hs01 now ends with its own authoritative full `./verify`.
2. Per-attempt unknown no longer closes a live session ingress; child offline/shutdown is separate.
3. Create and resume each test both pre-response and post-response/pre-bind event timing.
4. `src/planner/core/server.py` is explicitly unchanged; `SharedGateway` preserves its composition
   surface.

## Follow-up sub-agent review

The fresh read-only follow-up confirmed all four Codex findings were resolved and found one remaining
violation:

- Acceptance seam 5 did not prove the per-session command lock is released after the outbound write.
  Because current `GatewayChild.request()` combines send and response wait, a naïve implementation
  could hold the lock until `prompt.submit` acknowledges and delay `session.interrupt`.

Resolution: the plan now requires an internal begin/write request seam returning a request-ID-
correlated response handle, while preserving `request()` as its compatibility wrapper. A RED test
withholds the first response and proves the second same-session command is already written in order.

Final follow-up verdict: `NO VIOLATIONS`.
