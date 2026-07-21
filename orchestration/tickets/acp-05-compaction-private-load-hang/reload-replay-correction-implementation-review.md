# ACP-05 durable typed replay correction implementation review

## Verdict

**NOT READY.** The durable provenance CAS, current-capture replacement, raw replay ownership, one
Hub classification point, in-place typed replacement, successful-boundary de-duplication, controlled
load call sites, generation-fatal compaction failures, and one-error failed-activity behavior match the
frozen correction. The v24 schema entry path and required integrated proof are not ready.

## Findings

### [P1] `create_schema` now fails on supported ordinary SQLite connections

`src/planner/core/db.py:259` starts `BEGIN IMMEDIATE` inside
`_migrate_conversation_session_bindings`, but `create_schema` has already executed writes such as the
default-project seed. A caller using Python's ordinary `sqlite3.connect` transaction mode therefore
arrives with an active transaction, and the new binding migration raises `sqlite3.OperationalError:
cannot start a transaction within a transaction`. This is not hypothetical: the narrow existing test
`tests/unit/test_db.py:2208` fails at `create_schema(conn)` before reaching its version assertion. Many
existing DB tests use that same supported connection shape.

The v24 migration must enter its immediate transaction without nesting inside an implicit transaction,
while retaining the promised atomic binding backfill/amendment behavior. Prove the repaired path with
the existing ordinary-connection tests as well as the new autocommit `connect(...)` v24 reopen tests.

Narrow read-only reproduction:

```text
$ .venv/bin/pytest -q --tb=short tests/unit/test_db.py::test_fresh_schema_has_worker_type_not_null_no_default_and_composite_index
F                                                                        [100%]
E   sqlite3.OperationalError: cannot start a transaction within a transaction
```

### [P1] Three live schema tests still freeze version 23

`tests/unit/test_db.py:495`, `tests/unit/test_db.py:698`, and `tests/unit/test_db.py:2209` still assert
that the current schema version is 23. The correction makes v24 canonical and its new reopen tests
correctly assert `SCHEMA_VERSION == 24`. After the transaction-entry defect above is fixed, these three
assertions will still fail the live test contract and therefore `./verify`.

Update these current-schema assertions to the canonical version contract; do not weaken the new v24
idempotence assertions.

### [P1] The required second-compaction integrated replay proof is absent

`tests/e2e/test_acp_conversation.py:531` exercises one N -> N+1 compaction and then proves refresh,
child respawn, server restart, and a later prompt, but the test ends at `tests/e2e/test_acp_conversation.py:677`
without performing N+1 -> N+2. The correction plan explicitly requires an integrated second compaction
that proves the second boundary ID differs, N+1 reload retains the first capture's ID, and N+2 reload
emits only the second current-capture tuple. Repository units prove replacement in isolation, but they
cannot prove that broker provenance, registry CAS, Hub classification, and durable reload compose without
re-emitting the first boundary.

This integrated proof is required, not optional. Extend the production-shaped durable compaction e2e (or
an equivalent integrated test using the same real composition) through N+2 and assert exact typed replay,
distinct stable IDs, no raw Hermes summary, and no older boundary on the N+2 replay.

## Confirmed without findings

- Schema v24 remains version 24, and the ACP-06 v25 contract/plan now explicitly own the direct
  pre-column v24 -> v25 column creation/preservation handoff.
- The SQLite and in-memory compaction CAS compare exact N provenance and write only the ordered, unique
  current-capture tuple; a loser resolves the durable winner's exact tuple.
- Hermes capture and replay classification share one structural summary parser. The Hub classifies one
  complete raw batch at each ordinary attach/recovery, requested-cancel replacement, and compaction
  transition publication path.
- Replay replacement preserves the raw summary's exact position, and the broker does not publish a
  second successful completed boundary after Hub commit.
- Missing fork capability and normalization/post-validation failure remain generation-fatal after exact
  source admission.
- `activity:failed` emits one connection error only while the stream is ready, marks it not ready, and
  suppresses duplicate disconnect publication without changing ordinary activity publication.

## Review scope

Read-only review of the requested source, support, unit, and e2e files against the ACP-05 contract,
implementation plan, replay-correction plan and plan review, and implementation-review resolution. No
product or test source was edited and `./verify` was not run.
