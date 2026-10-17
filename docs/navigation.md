# Navigation transaction foundation

Navigation is a derived view of canonical database facts. It does not own Ticket or conversation state.

The generic contract has schema version 2. It contains Ticket metadata, canonical status, assignment, approval references, orchestrator metadata, Chief metadata, conversations, addresses, Projects, and complete child/conversation counts.

Signal compatibility remains unsatisfied. This contract omits public activity, unread, request, failure, work, and attention semantics and their dependent counts. An omitted fact is unknown under this contract. It is not false or zero. Raw lifecycle and read positions do not prove those public semantics. Downstream consumers cannot cut over dependent views from this contract alone.

Revisions are canonical decimal strings. A scope identifies one workspace. An epoch identifies projection continuity. A snapshot contains records and matching continuity. A batch names its base and result revision and contains complete upserts and deletion keys. Aliases share a conversation identity and do not multiply distinct conversation totals.

No transport, consumer cutover, public receipt change, automatic resend, queue replay, or provider recovery behavior belongs to this foundation.

NO PUSH AND NO MERGE until the owner inspects and explicitly decides. No deployment or push-triggered CI is authorized.

## Composition and storage

Every common database connection calls `navigation.attach`. It installs the transaction guard even before the navigation schema exists. `navigation.finalize` checks schema availability on each commit. The module registry supplies this participant to the generic coordinator. CLI, server, cursor, bulk, context manager, SQL commit, outer savepoint release, and quiet commits share that coordinator. Quiet commits retain their existing wake suppression.

Source triggers capture transaction-local affected keys. Insert capture follows actual insertion, so ignored inserts create no revision. Update and deletion capture precede changes and cascades. Ticket capture includes both old and new parent totals. Savepoint rollback rolls back affected keys. Private conversation cursors, composer catalog, and compaction bookkeeping do not affect navigation.

The finalizer expands old indexed dependencies and current canonical relations. It derives affected Ticket, conversation, owner, Project, and totals records. Conversation roots own their address records. Old roots survive as dependency references until finalization, so a move or deletion can remove their former addresses. Totals use SQL counts and indexed address lookups. They do not rewrite dependencies for every child.

The migration creates six tables and four indexes plus source triggers. The version 2 migration adds two partial indexes. A migration transaction backfills synthetic or existing canonical rows and initializes continuity. Rebuild replaces derived records, dependencies, and history in one transaction. Source facts remain unchanged. `reset_epoch` clears journal continuity under a write transaction and returns the new continuity. The scope persists across rebuild and reset. A consistent database copy also copies its scope. Authentication and workspace routing remain downstream responsibilities.

## Exact records

All records have `kind`, `id`, and `value`. `navigation/contracts.py` declares a tag-discriminated typed union.

| Tag | Value fields |
| --- | --- |
| `ticket` | `title`, `worker_type`, `stage`, `ticket_status`, `priority`, `deadline`, effective `project_id`, `orchestrator_kind`, `sprint_item_id`, `conversation_id`, `is_backlogged`, `ceiling`, `ceiling_holder`, `proposal_field`, `proposal_revision`, `stage_ownership`, `terminal`, `locale` |
| `orchestrator` | `title`, `project_id`, `settled`, current `conversation_id` |
| `chief` | `label`, current `conversation_id` |
| `project` | `name`, `priority` |
| `conversation` | raw `latest_sequence`, `owner_read_through_sequence`, `latest_agent_activity_sequence`, `latest_agent_activity_at`, `error_acknowledged_through_sequence`, `lifecycle_summary` |
| `address` | `owner_kind`, `owner_id`, `conversation_id`, `current`, `parent_kind`, `parent_id` |
| `totals` | `child_count`, `distinct_conversation_count` |

Ticket status uses the canonical pure status rule. Stage ownership and terminal state use the Worker definition from the same database transaction. Approval data contains the field, revision, and holder. It excludes the proposal body and field values.

Conversation lifecycle entries contain the compact raw summary described below. They preserve uncertainty. They do not imply provider capability, active public Signal state, or automatic recovery. Detailed lifecycle identities remain in the canonical source, outside navigation.

Address IDs are canonical JSON arrays of owner kind, owner ID, and conversation ID. Owner kind is `ticket`, `orchestrator`, or `chief`. A Ticket address follows canonical Ticket history and the current pointer. An orchestrator address also follows durable supervisor identity metadata. Parent membership follows the Ticket's current assignment. One conversation can have multiple addresses. Distinct totals count its identity once per scope. Totals IDs are `workspace`, `chief:chief`, and `orchestrator:<id>`. Chief's record ID is `chief`.

## Snapshots and replay

`navigation.snapshot(conn)` returns scope, schema version, `semantics`, epoch, revision, and records in one read transaction. `semantics` is exactly `generic-source-facts-v2`. It explicitly marks the limited compatible fields. Reads reject a caller transaction that contains pending canonical changes. They do not finalize it early.

`navigation.journal(conn, epoch, after_revision, limit)` returns the same continuity plus complete batches and `has_more`. Limits range from 1 to 1,000 batches. Each batch contains scope, schema version, semantics, epoch, `base_revision`, resulting `revision`, typed upserts, and typed deletion keys. Revisions use exact canonical decimal strings. A relevant transaction creates one batch even when the public delta is empty. Unrelated transactions create none.

A changed epoch, future cursor, or journal gap raises `HistoryUnavailable`. Malformed decimal cursors raise `ValueError`. Batches remain durable when the wake signal is lost. This module supplies no retention scheduler, transport, browser publication, startup admission, or cross-epoch command certification.

## Isolation and evidence

Tests use synthetic SQLite files in temporary directories. The old-head migration fixture uses the migration engine against its own file. The isolated runner strips inherited `PLAN_` values and assigns database, locks, sockets, logs, skills, and agent homes within a disposable runtime. Tests never open the live database or real agent homes.

Focused tests assert rollback, savepoint rollback, source failure, alias counts, historical discovery, repeated parent moves, deletes, blocker dependencies, same-transaction definitions, read facts, exact migration backfill, migration failure rollback, repeated schema opens, snapshot isolation, journal gaps, and epoch reset. These tests prove generic source facts. Final Signal parity and downstream consumer cutover remain unsatisfied.

Composition enables SQLite recursive triggers. `REPLACE` performs an implicit deletion, and SQLite otherwise skips its deletion triggers. This setting lets old dependency capture remove the replaced identity. Current source triggers contain immutable-history and assignment guards. Legacy supervisor mutation triggers disappear in the earlier table rebuild. Navigation triggers write only the dirty table, so they do not recurse into canonical source writes. A raw connection without module composition cannot change relevant source rows because it lacks the guard function.

Project names and priorities belong to separate Project records. A Project rename updates only its Project record. Ticket records contain the effective Project identity and do not depend on Project display metadata. An orchestrator's Project reassignment still updates each affected child's effective identity.

Update capture names actual navigation inputs. Project updates capture `id`, `name`, and `priority`. Worker type updates capture `worker_type` and `definition_json`. Chief settings updates capture `employee_id` and `label`. Excluded column-only updates create no navigation batch. Examples include timestamps and Chief launch columns. Worker launch defaults reside in `definition_json`, so an update to those defaults still captures referencing Tickets. An actual Worker definition update derives each referencing Ticket's ownership and terminal facts from the same transaction. It does not derive their conversations or parent totals because those counts do not depend on Worker definitions. A captured update with equal public values still creates an empty batch.


## Compact lifecycle contract, schema 2

`lifecycle_summary` has four fields:

- `latest_turn` contains one raw durable turn's `identity`, `turn_number`, and `status`, or null. The greatest turn number wins. Identity in descending order breaks a tie.
- `turn_status_counts` contains exact `intent` and `running` counts across all turns.
- `request_status_counts` contains exact `pending` and `answering` counts across all turns.
- `attempt_status_counts` contains exact `intent` and `unknown` counts across all turns.

These are persisted statuses. A durable `running` fact does not prove that a process is alive. An `unknown` attempt remains uncertain even when later turns finish. No count has a cap. No newer turn clears an older uncertain outcome. The summary does not select Signal, unread, attention, retry, or recovery behavior.

The output contains one optional fact and six integer counts. It has a fixed number of fields, not a hard byte limit on canonical identities or integer digits. It contains no lifecycle history list. Complete identities and settled details remain available through canonical conversation source reads.

The latest-turn partial index covers conversation identity, descending turn number, descending identity, and status for turn rows. The unresolved-status partial index covers conversation identity, kind, and status for the exact six counted groups. The query and index share one predicate. Navigation does not scan irrelevant settled history for sequence, read, or lifecycle updates. Exact count work remains proportional to the number of unresolved source rows. The canonical lifecycle writer separately reads its source history. This change does not make that writer constant-cost.

The original migration explicitly creates schema 1 and uses a private, frozen conversation list helper. Current typed snapshot and journal reads reject schema 1. The additive schema 2 migration installs both indexes, changes metadata, and rebuilds records atomically. It replaces the epoch and journal while preserving workspace scope. A failed upgrade restores the original schema version, epoch, records, and journal. A second current-schema open does not replace continuity.

Synthetic proof adds 1,000 and 10,000 irrelevant settled turn, request, and attempt facts while preserving the same latest turn and unresolved facts. Sequence and read finalization each use 1,025 SQLite VM instructions at both sizes. Lifecycle finalization uses 1,011 at both sizes. Each full conversation record is 448 bytes. The lifecycle source write occurs before this VM measurement. A separate fixture preserves all 5,001 unknown outcomes in the exact count. Its summary uses 250 bytes and 45,173 VM instructions, versus 173 with the small unresolved set. These observations prove the stated projection boundary only.
