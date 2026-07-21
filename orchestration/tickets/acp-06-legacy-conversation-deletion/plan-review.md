# ACP-06 legacy conversation deletion — plan review

Verdict: **NOT READY**.

The hard ACP-05 gate, retained worker-context boundary, Employee-step replacement, one-way cutover,
and runtime/frontend writer split are otherwise coherent. Three blocking corrections remain.

## Blocking findings

1. **[P0] Freeze an atomic v25 entry path; the current plan can pre-create or backfill the state it
   promises to reset.** `implementation-plan.md:105-108` says to add the Employee-step table to the
   canonical DDL and then run a guarded immediate migration. Today `create_schema` executes the DDL
   before any migration (`src/planner/core/db.py:230-251`), so a direct implementation would create
   `employee_step_runs` outside the required immediate transaction. The same current sequence calls
   `_migrate_conversation_session_bindings` before the proposed terminal migration; that function
   validates/backfills legacy Ticket and Chief mirrors and can reject data that v25 is expressly
   meant to discard. It also cannot remain on a v25 reopen because it queries the now-dropped
   `agent_chat_sessions`. Amend Phase 4 with the exact branch/order: capture the incoming
   `user_version`, perform only required historical Ticket normalization, skip/remove legacy ACP
   binding backfill, and make one v25 immediate transaction own Employee-step table/index creation,
   worker-row/event conversion, Ticket/session/binding reset, legacy drops, and the durable v25
   marker. Add a forced-failure rollback test in addition to the happy-path/idempotent fixture at
   `implementation-plan.md:33-40`; otherwise the contract's one-transaction data-preservation claim
   is not proved.

2. **[P1] The required replacement test is also in the exact deletion glob.** The contract orders
   deletion of every `tests/unit/test_hermes_backend_*.py` (`contract.md:209-210`) but then requires
   live configuration proofs to move to `test_hermes_backend_configuration.py`
   (`contract.md:211-213`; `implementation-plan.md:51-54`). That destination matches the deletion
   glob, so an implementation cannot satisfy both instructions. Rename the retained destination to
   a conversation-owned name outside the glob (for example
   `test_conversation_hermes_backend_configuration.py`) or state one exact exception consistently in
   both artifacts.

3. **[P1] The exact mutation allowlist omits two required closure edits.** The final absence boundary
   forbids `PoolStepGateway`/relay vocabulary in Python source (`contract.md:222-231`), but
   `src/planner/core/testmode.py:101-104` still describes the deleted relay/Pool path and is absent
   from the allowlist at `implementation-plan.md:254-276`. Also, Phase 4 removes the chat-message
   branch of `_cleanup_legacy_execution_route_records`; once employee-session history is deleted,
   that leaves `LEGACY_EXECUTION_ROUTE_VALUES` and
   `redact_generated_execution_route_segment` in
   `src/planner/core/legacy_execution_route.py` with no caller or retained responsibility. Add both
   files to the allowlist and explicitly rewrite the test-mode description for ACP and delete the
   newly dead redaction symbols. Otherwise implementation must either violate its file boundary or
   leave a forbidden token/deletion ghost.

No tests were run; this was a read-only plan/caller review.

## Finding dispositions

1. **Accepted and corrected.** The contract and Phase 4 now capture incoming `user_version`, suppress
   pre-transaction Employee-step DDL and the legacy binding migration for `<25`, and put every v25
   mutation plus `PRAGMA user_version=25` in one immediate transaction. The v24 fixture now requires
   a destructive-mid-migration rollback proof.
2. **Accepted and corrected.** The retained suite is named
   `test_conversation_hermes_backend_configuration.py`, outside the deleted
   `test_hermes_backend_*.py` glob.
3. **Accepted and corrected.** `core/testmode.py` and `core/legacy_execution_route.py` are in the exact
   allowlist with only the ACP description rewrite and two dead redaction-symbol deletions authorized.

Post-amendment verdict: **READY**, subject to the unchanged ACP-05 Computer Use gate.
