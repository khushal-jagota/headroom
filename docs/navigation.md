# Navigation transaction foundation

Navigation is a derived view of canonical database facts. It does not own Ticket or conversation state.

The public contract uses schema 6 and `attention-knowledge-v6-recap` semantics. It contains Ticket metadata, canonical status, assignment, approval references, orchestrator metadata, Chief metadata, conversations, addresses, Projects, and complete child/conversation counts.

Conversation attention comes from the accepted Signal producer. Each conversation carries complete current attention or explicitly unavailable runtime knowledge, plus independently durable message and failure facts. Raw lifecycle counters, read positions, and Ticket status never substitute for that source. Consumer adoption is in progress. Complete production parity, render proof, recovery, and performance acceptance remain open.

Revisions are canonical decimal strings. A scope identifies one workspace. An epoch identifies projection continuity. A snapshot contains records and matching continuity. A batch names its base and result revision and contains complete upserts and deletion keys. Aliases share a conversation identity and do not multiply distinct conversation totals.

The delivery layer exposes current and degraded snapshots through wire version 2. Current service also supplies resumable events. Browser consumer adoption remains separate. No public receipt change, automatic resend, queue replay, or provider recovery behavior belongs to this foundation.

Local incorporation into a worker's own branch is permitted. No push of any kind, merge or publication into staging/main, deployment, or push-triggered CI is authorized without the applicable owner release. Owner inspection remains required for publication.

## Composition and storage

The application lifetime in `server_lifecycle/runtime.py` owns startup and shutdown order. It acquires the producer lease before schema work, constructs the conversation runtime, initializes Signal, installs the complete navigation binding, checks ownership, then admits the runtime. The HTTP root consumes that lifetime. It supplies one callback to stop delivery and background resources. On exit, the lifetime invalidates Signal before that callback, then drains the conversation system before it detaches navigation and closes registrations. Producer and navigation adapters keep separate responsibilities. The conversation runtime builder lives outside the HTTP API.

Every common database connection calls `navigation.attach`. It installs the transaction guard even before the navigation schema exists. `navigation.finalize` checks schema availability on each commit. The module registry supplies this participant to the generic coordinator. CLI, server, cursor, bulk, context manager, SQL commit, outer savepoint release, and quiet commits share that coordinator. Quiet commits retain their existing wake suppression.

Source triggers capture transaction-local affected keys. Insert capture follows actual insertion, so ignored inserts create no revision. Update and deletion capture precede changes and cascades. Ticket capture includes both old and new parent totals. Savepoint rollback rolls back affected keys. Private conversation cursors, composer catalog, and compaction bookkeeping do not affect navigation.

The finalizer expands old indexed dependencies and current canonical relations. It derives affected Ticket, conversation, owner, Project, and totals records. Conversation roots own their address records. Old roots survive as dependency references until finalization, so a move or deletion can remove their former addresses. Totals use SQL counts and indexed address lookups. They do not rewrite dependencies for every child.

Each full rebuild reads Ticket ownership once from Tickets and once from historical conversation associations. It holds only Ticket identity, current parent and current conversation, plus association identities. Current and historical links to the same Ticket produce one address per conversation; different Tickets can share a conversation. Historical addresses use the Ticket's current parent assignment.

Each rebuild creates a fresh lookup, including repeated rebuilds before the same transaction commits. It discards the lookup afterward. Conversation derivation reads only its five sequence, activity and historical identity columns. Incremental finalization keeps its existing per-conversation ownership query. Ownership source reads during rebuilds stay constant as the number of conversations increases. Other navigation work retains its existing costs.

The original navigation migration creates six tables and four indexes plus source triggers. The version 2 migration adds two partial indexes. A migration transaction backfills synthetic or existing canonical rows and initializes continuity. Rebuild replaces derived records, dependencies, and history in one transaction. Source facts remain unchanged. `reset_epoch` clears journal continuity under a write transaction and returns the new continuity. The scope persists across rebuild and reset. A consistent database copy also copies its scope. Authentication and workspace routing remain downstream responsibilities.

## Current staging migration

`navigation_current_workspace` joins `prepare_navigation_attention` and staging's
`remove_priority`. The source branch also carries `chief_recap`. The accepted source
migrations remain unchanged. Chief recap stays a separate editable content query;
no new Chief navigation field or recap writer exists.

The source shape must agree across Projects, orchestrators, Tickets and schedule
configuration: either all four tables still contain historical priority columns,
or none do. Mixed shapes fail before projection conversion. On old source,
historical navigation schemas1–4 retain their priority fields and semantics. When
source-first migration has already removed the columns, historical helpers select
explicit prepared5 instead of inventing priority values. The join refreshes source
triggers and rebuilds prepared5. Its records omit priority and conversation attention.
Snapshot, journal and replay reads reject prepared5, including after failure/reopen.
Only complete canonical Signal installation activates public6.

Normal schema admission accepts a nonempty set of wholly carried migration heads
and lets Alembic perform its normal DAG upgrade. It rejects empty sets and sets
containing unknown revisions. It does not stamp or rewrite them. This permits retry
after source removal committed separately and the joined rebuild failed. That
failure restores the join's immediate checkpoint, not the earlier source commit.

Old schema4 clients/cursors require snapshot replacement and updated assets. Their
schema/semantics cannot certify current navigation. Selected current-staging595
readers work read-only against upgraded synthetic data; f097 readers requiring
priority are intentionally unsupported on this result. Their earlier positive
proof belongs to the older candidate. Matching-source backup restoration remains
lossy: it discards writes made after the backup and requires a fresh upgrade/start.

## Exact records

All records have `kind`, `id`, and `value`. `navigation/contracts.py` declares a tag-discriminated typed union.

| Tag | Value fields |
| --- | --- |
| `ticket` | `title`, `recap`, `recap_preview`, `worker_type`, `stage`, `ticket_status`, `deadline`, effective `project_id`, `orchestrator_kind`, `sprint_item_id`, `conversation_id`, `is_backlogged`, `ceiling`, `ceiling_holder`, `proposal_field`, `proposal_revision`, `proposal_created_at`, `updated_at`, `created_at`, `worker_step_claim_out`, `blocked`, `stage_ownership`, `gating_field`, `terminal`, `locale` |
| `orchestrator` | `title`, `recap`, `recap_preview`, `created_at`, `project_id`, `settled`, current `conversation_id` |
| `chief` | `label`, current `conversation_id` |
| `project` | `name` |
| `conversation` | raw `latest_sequence`, `owner_read_through_sequence`, `latest_agent_activity_sequence`, `latest_agent_activity_at`, `error_acknowledged_through_sequence`, `lifecycle_summary`, `attention` |
| `address` | `owner_kind`, `owner_id`, `conversation_id`, `current`, `parent_kind`, `parent_id` |
| `totals` | `child_count`, `distinct_conversation_count` |

Ticket status uses the canonical pure status rule. Stage ownership, terminal state, and `gating_field` use the stored Worker definition from the same database transaction. The required nullable gate comes from `definition.gating_field` for the exact Ticket stage. Null means that the stage has no gate. A manifest never supplies category authority. Gating-only definition changes publish affected Ticket records without changing status or ownership. This field was introduced in schema4 and remains in schema6; historical schema1/2/3 layouts stay unchanged. Approval data contains the field, revision, creation time, and holder. It excludes the proposal body and field values.

Ticket and orchestrator `recap_preview` are required strings introduced in schema4 and retained in schema6. They use the unchanged canonical helper over `tickets.recap` and `sprint_items.recap`. The helper compacts whitespace and applies the configured 160-character bound and ellipsis. Row, title, and preview enter the same publication. Chief retains its existing list-count fallback. No browser truncation or separate preview writer supplies these values. Historical schema1/2/3 shapes stay unchanged.

Current Ticket and orchestrator records also contain required full `recap` text from the same sources. Rail popovers use this text and apply a visual limit of five lines, or fewer when available space requires it. Dense rows retain `recap_preview`. Historical schema1–4 shapes stay unchanged. Startup installation rebuilds stored records before public delivery, so existing schema6 records acquire the full text. The recap semantics identify the changed wire shape and reject older cursors.

The character bound is not a byte or memory bound. UTF-8 and JSON escaping change the encoded cost. Initial full-payload cost, removed preview requests, recurring-update savings, and browser memory require separate matched-candidate evidence. Final cost acceptance remains open.

Ticket `created_at` comes from exact stored `tickets.created_at`. This required integer was introduced in schema4 and remains in schema6. Existing Chief children use filtered creation-time/identity order before their 30-row page and grouping. Navigation does not substitute update time or conversation activity for that order.

Schema 3 introduced six required source facts. Orchestrator `priority` and `created_at` come from the corresponding `sprint_items` columns. Ticket `updated_at` comes from its stored timestamp. `proposal_created_at` comes from the strict canonical proposal decoder and is null exactly when no proposal exists. `worker_step_claim_out` is true exactly when the stored claim is `out`. Summing that flag across all Ticket records matches the existing unfiltered Worker claim count, including terminal, blocked, and backlogged Tickets. Schema6 removes the priority fields to match current staging. Orchestrators retain creation-time/identity ordering. Grouped child rows retain activity/title/identity ordering; the Chief child page retains creation-time/identity ordering. Ticket update time and proposal creation time remain independent. Conversation activity never substitutes for these values.

Ticket `blocked` is the canonical live-blocker predicate, independently of status precedence. A proposal, an outstanding claim, or an errored claim can mask the blocked status without clearing this fact. Existing dependency capture handles edge changes and blocker completion, reopening, or deletion in the same source transaction. No blocker identities or names enter navigation.

The explicit schema 2 to 3 migration rebuilds the projection in one transaction. It rotates the epoch and clears the journal once through the existing rebuild operation. That schema migration retained wire version 1. The current transport uses wire version 2. Command fence and lifecycle input versions remain 1. Historical schema 1 and 2 projections retain their original layouts and historical cancelled/blocker rules. The compact lifecycle migration stays explicitly version 2. Schema 3 uses the accepted current source rules. Current public reads reject every schema except6, including prepared5. A failed migration transaction restores its immediate source/projection checkpoint; source migrations committed earlier remain committed. Full body, transcript, and configuration detail reads remain separate.

Schema 4 adds required conversation `attention`. Its `current` value contains the producer's `signal` (`working`, `message_for_owner`), `awaiting_answer`, and `last_turn_failed`. Its separate `durable` value contains `message_for_owner` and `last_turn_failed`. Current records require both durable booleans to equal their current counterparts. Degraded delivery replaces only this attention field with `current:null` and coherent canonical durable values. No missing fact receives a false default.

The current joined migration prepares schema5 without conversation attention. Complete producer installation alone activates schema6 and rebuilds its records. Historical schema1–4 layouts and meanings remain reproducible on genuine priority-bearing source. Restart or generic rebuild does not grant current delivery authority.

Conversation lifecycle entries contain the compact raw summary described below. They preserve uncertainty. They do not imply provider capability, active public Signal state, or automatic recovery. Detailed lifecycle identities remain in the canonical source, outside navigation.

Address IDs are canonical JSON arrays of owner kind, owner ID, and conversation ID. Owner kind is `ticket`, `orchestrator`, or `chief`. A Ticket address follows canonical Ticket history and the current pointer. An orchestrator address also follows durable supervisor identity metadata. Parent membership follows the Ticket's current assignment. One conversation can have multiple addresses. Distinct totals count its identity once per scope. Totals IDs are `workspace`, `chief:chief`, and `orchestrator:<id>`. Chief's record ID is `chief`.

## Snapshots and replay

`navigation.snapshot(conn)` returns scope, schema version, `semantics`, epoch, revision, and records in one read transaction. `semantics` is exactly `attention-knowledge-v6-recap`. Stored records and journal upserts contain the complete current attention profile. Reads reject a caller transaction that contains pending canonical changes. They do not finalize it early.

`navigation.journal(conn, epoch, after_revision, limit)` returns the same continuity plus complete batches and `has_more`. Limits range from 1 to 1,000 batches. Each batch contains scope, schema version, semantics, epoch, `base_revision`, resulting `revision`, typed upserts, and typed deletion keys. Revisions use exact canonical decimal strings. A relevant transaction creates one batch even when the public delta is empty. Unrelated transactions create none.

A changed epoch, future cursor, or journal gap raises `HistoryUnavailable`. Malformed decimal cursors raise `ValueError`. Batches remain durable when the wake signal is lost. The delivery layer below owns retention, transport, startup admission, and command certification. Browser publication remains separate.

## Current and degraded knowledge

A wire2 current snapshot carries `mode:current`, the full current availability profile, an exact permit, a navigation cursor, and optional existing command proof. A current batch envelope carries the same authority profile as its accepted snapshot. The profile includes an opaque delivery session, availability state/generation/reason, and permit scope/runtime epoch/installation/generation. Generations use canonical decimal strings and compare only within one delivery session. Producer ordering stays independent of navigation revisions.

A degraded snapshot carries noncurrent availability, `permit:null`, `cursor:null`, and `proof:null`. Its structural continuity is provenance, not replay authority. Every extant conversation has `current:null` and a fresh coherent durable observation. Before an activated structural projection exists, or when a coherent complete read fails, the endpoint returns 503. No partial rows or false defaults fill a gap.

Snapshot reads first enumerate identities through a short separate physical read-only connection. A fresh read-only/query-only transaction then calls the canonical durable reader before any source SELECT pins its snapshot. That same SQL snapshot supplies the actual inventory and stored navigation records. Coverage must match exactly. Current attention must agree with canonical durable values. After SQL closes, delivery checks the full availability profile and, for current service, its exact permit. Send admission checks the profile again.

Loss fences current streams. Existing browser recovery requests the same snapshot door. A complete current replacement is required before a new stream starts. A later current state cannot revive an old permit or stream. Network loss detection takes time and cannot recall bytes already sent.

## Isolation and evidence

Tests use synthetic SQLite files in temporary directories. The old-head migration fixture uses the migration engine against its own file. The isolated runner strips inherited `PLAN_` values and assigns database, locks, sockets, logs, skills, and agent homes within a disposable runtime. Tests never open the live database or real agent homes.

Focused tests assert rollback, savepoint rollback, source failure, alias counts, historical discovery, repeated parent moves, deletes, blocker dependencies, same-transaction definitions, read facts, exact migration backfill, migration failure rollback, repeated schema opens, snapshot isolation, journal gaps, and epoch reset. Additional actual-producer fixtures prove attention-only publication, deletion, rollback, physical preparation, failed activation, and complete installation. Delivery and browser fixtures cover current/degraded knowledge and authority loss. These bounded results do not establish final consumer parity, actual DOM boundaries, or complete-path performance.

Composition enables SQLite recursive triggers. `REPLACE` performs an implicit deletion, and SQLite otherwise skips its deletion triggers. This setting lets old dependency capture remove the replaced identity. Current source triggers contain immutable-history and assignment guards. Legacy supervisor mutation triggers disappear in the earlier table rebuild. Navigation triggers write only the dirty table, so they do not recurse into canonical source writes. A raw connection without module composition cannot change relevant source rows because it lacks the guard function.

Project names belong to separate Project records. Priority no longer exists in current canonical storage or navigation. A Project rename updates only its Project record. Ticket records contain the effective Project identity and do not depend on Project display metadata. An orchestrator's Project reassignment still updates each affected child's effective identity.

Update capture names actual navigation inputs. Current Project updates capture `id` and `name`. Worker type updates capture `worker_type` and `definition_json`. Chief settings updates capture `employee_id` and `label`. Excluded column-only updates create no navigation batch. Examples include timestamps and Chief launch columns. Worker launch defaults reside in `definition_json`, so an update to those defaults still captures referencing Tickets. An actual Worker definition update derives each referencing Ticket's ownership and terminal facts from the same transaction. It does not derive their conversations or parent totals because those counts do not depend on Worker definitions. A captured update with equal public values still creates an empty batch.


## Compact lifecycle contract, schema 2

`lifecycle_summary` has four fields:

- `latest_turn` contains one raw durable turn's `identity`, `turn_number`, and `status`, or null. The greatest turn number wins. Identity in descending order breaks a tie.
- `turn_status_counts` contains exact `intent` and `running` counts across all turns.
- `request_status_counts` contains exact `pending` and `answering` counts across all turns.
- `attempt_status_counts` contains exact `intent` and `unknown` counts across all turns.

These are persisted statuses. A durable `running` fact does not prove that a process is alive. An `unknown` attempt remains uncertain even when later turns finish. No count has a cap. No newer turn clears an older uncertain outcome. The summary does not select Signal, unread, attention, retry, or recovery behavior.

The output contains one optional fact and six integer counts. It has a fixed number of fields, not a hard byte limit on canonical identities or integer digits. It contains no lifecycle history list. Complete identities and settled details remain available through canonical conversation source reads.

The latest-turn partial index covers conversation identity, descending turn number, descending identity, and status for turn rows. The unresolved-status partial index covers conversation identity, kind, and status for the exact six counted groups. The query and index share one predicate. Navigation does not scan irrelevant settled history for sequence, read, or lifecycle updates. Exact count work remains proportional to the number of unresolved source rows. The canonical lifecycle writer separately reads its source history. This change does not make that writer constant-cost.

The original migration explicitly creates schema 1 and uses a private, frozen conversation list helper. Current typed snapshot and journal reads reject historical schemas 1, 2, and 3. The additive schema 2 migration installs both indexes, changes metadata, and rebuilds records atomically. It replaces the epoch and journal while preserving workspace scope. A failed upgrade restores the original schema version, epoch, records, and journal. A second current-schema open does not replace continuity.

The original schema 2 synthetic proof adds 1,000 and 10,000 irrelevant settled turn, request, and attempt facts while preserving the same latest turn and unresolved facts. Sequence and read finalization each use 1,025 SQLite VM instructions at both sizes. Lifecycle finalization uses 1,011 at both sizes. Each full conversation record is 448 bytes. The lifecycle source write occurs before this VM measurement. A separate fixture preserves all 5,001 unknown outcomes in the exact count. Its summary uses 250 bytes and 45,173 VM instructions, versus 173 with the small unresolved set. These observations prove the stated projection boundary only.

## Resumable delivery

A current subscriber first obtains one complete current snapshot. It then requests every committed revision after that snapshot. Each wire2 `BatchEnvelope` contains one whole transaction, including deletions, and the exact current authority profile. The subscriber receives either the next complete revision or an explicit instruction to replace its snapshot. It never silently skips missing history.

```text
source transaction -> navigation records + journal + commit witness
                              |
                       successful commit
                              |
                    snapshot / complete events
                              |
                       browser publication
```

The snapshot and event endpoints share the existing workspace ingress boundary. Cursors carry workspace identity, schema, semantics, epoch, and exact decimal revision. The server and browser check continuity and the full authority profile before duplicate or future-revision decisions. Periodic journal reads recover missed process signals and quiet commits. Database reads end before a network wait.

The event stream limits each frame and the complete prefetched page. It closes a connection whose writes exceed the send deadline. An indivisible batch that exceeds the frame limit requires a replacement snapshot. Snapshots use a separate path without that frame limit, so the same large change cannot trap the subscriber in repeated resets.

Retention periodically prunes complete journal prefixes and records their replay floor. Its count and byte settings are maintenance targets, not instantaneous limits between maintenance cycles. Ingest during that interval adds to the high-water mark. An expired cursor requires a new snapshot. No subscriber depends on a process signal for correctness.

Code paths: `navigation/api.py`, `navigation/delivery.py`, `navigation/delivery_contracts.py`, `navigation/retention.py`, and `navigation/configuration.py`.

## Startup continuity

One real conversation runtime holds the exclusive database lease. It reconciles lost capabilities before the server prepares its other reconciliation work. Complete producer installation commits the startup navigation epoch before requests or background loops start. No separate startup reset supplies authority. A second runtime cannot rotate that epoch. Ordinary database connections do not allocate startup epochs.

Restart, rebuild, and incompatible schema invalidate previous continuity. Restore requires a stopped runtime and a fresh startup. A copied epoch or a numerically larger client revision never establishes continuity after an older backup returns. Shutdown closes navigation streams before it releases runtime ownership.

Code paths: `core/server.py`, `core/loops.py`, `conversation/system.py`, and `conversation/storage.py`.

## Command visibility

Relevant successful commands add fence metadata in response headers. Existing bodies, statuses, authorization, and receipts retain their meanings. A fence describes committed navigation history. It does not prove provider acceptance or browser display.

Random witnesses commit atomically with relevant source changes. The generic commit boundary reports them only after the outer commit succeeds. Command capture includes explicitly awaited conversation tasks and threads. Detached background work does not enter another command's fence. Failed observation preserves the successful command result and leaves visibility unknown.

A replacement snapshot can certify every witness from an issued fence in the same read transaction as its records. The witnesses survive epoch resets and rebuilds. If a backup loses one, certification remains unknown even if new writes reach a larger revision. Later writes can replace a command's value. Certification proves inclusion of committed history, not that the value remains unchanged.

Capture, response metadata, proof requests, and witness retention are bounded. Missing, expired, or incomplete evidence never becomes a certified no-op. Unknown visibility never triggers an automatic resend. The browser owner must observe published state before it resolves visibility. Degraded or retained knowledge cannot certify current attention or command visibility.

Code paths: `navigation/proof.py`, `navigation/proof_contracts.py`, `navigation/commands.py`, `navigation/middleware.py`, `core/commit_capture.py`, and `core/commit_observers.py`.

## Browser synchronization boundary

The browser navigation module maintains one shared replica above routes and panes. The workspace sidebar, ordinary orchestrator headers and children, Chief child pagination, Review queue, shell work count, Ticket masthead and current-stage section, and Chief conversation pointer use it. Ordinary orchestrator content and exact-identity proposal bodies remain separate detail reads.

One presentation result belongs to each effective view identity. Shared indexes supply parent membership, identity joins, addresses, and Review facts. Knowledge changes invalidate the result even without a new publication. Feature modules own DOM assertions and dependency checks. The application captures relevant mounted observers before each command and retains bounded visibility evidence independently of the domain result.

The current-stage section admits detail only when public identity, control facts, and exact proposal revision match. Worker configuration must describe the canonical stage. Retained detail preserves edits but cannot authorize a gesture against a replaced identity or certify a newer publication. A confirmed read immediately invalidates the exact conversation fact at the returned watermark before recovery.

Ticket masthead Project names follow the existing parent-or-own Project rule. The masthead never rewrites the full detail object used by actions, fields, proposals, blockers, and configuration. Missing source membership cannot credit a retained masthead as current. Shared read errors appear once in a composed workspace. Distinct failed content or detail reads retain their own error lines.

The Review and shell count deduplicates current Ticket, Chief, and existing orchestrator conversation pointers. Historical-only and unlinked conversations do not contribute. The exact unfiltered Worker out-claim count remains a separate fact. Unknown runtime count is null and omits the runtime mark or count line. Known zero preserves the existing behavior.

```text
complete snapshot / revision batches
                |
       one browser owner
                |
     one atomic source collection
                |
      coherent shared selectors
                |
        rendered consumers
                |
    observed command visibility
```

The owner validates continuity before revision comparisons. Obsolete requests cannot replace newer state. A gap or reset requires a complete snapshot replacement.

The source collection atomically publishes records, local publication identity, mode, full authority, and nullable cursor. Request admission tokens distinguish accepted replacements even when structural cursors match.

Observed connection loss immediately invalidates the shared runtime-knowledge gate. An older admitted collection transaction cannot restore it. Only a newly accepted complete current snapshot restores runtime authority. Independently confirmed message and failure facts retain their conversation identity and provenance. Their state is `fresh`, `retained`, or `unknown`. Confirmed operations invalidate only the durable facts that their existing semantics affect.

Effective selectors preserve unknown values. Unknown runtime knowledge supplies no runtime mark. A known durable message keeps its existing indicator. Retained knowledge never becomes fresh or supplies visibility proof. This bookkeeping adds no new UI.

Local drafts and manual choices remain separate. Transport state does not add visible badges, new loading treatments, or changed command authority.

A successful command and its visible result remain separate facts. A visibility wait requires the specified consumers to observe a coherent publication.

An adapter commit alone does not prove display. A consumer removal or late callback cannot supply missing render evidence.

After an epoch change, the server must certify the issued fence against a replacement snapshot. Missing or expired evidence leaves visibility unknown.

Unknown visibility never causes an automatic command resend. A certified no-op does not imply a relevant visible change.

Existing frontend browser hosts exercise the converted consumers with strict synthetic source publications. Isolated runtime and socket fixtures exercise actual producer admission and delivery separately. The comparison harness runs the actual built App and application lifetime with synthetic source data and controlled backend adapters. It retains exact source, dependency and asset identities, raw traffic, DOM observations and measurement limits. Separate process runs compare the original browser and candidate under matched workloads. These results require review of their coverage and costs before product acceptance.

Code paths: `web/src/lib/navigation/`, `web/tests/navigation-*`, and `scripts/navigation_browser_harness.py`.

_Last verified: 2026-10-18._

## Signal installation and delivery

The application connects the accepted Signal producer to navigation storage and
delivery admission. The public schema6/wire2 contract now exists. Consumer adoption
and complete product acceptance remain separate work.

`core.db` accepts a registered transaction guard before an outer transaction starts.
The same admission covers managed connections and raw migration transactions. Guard
settlement precedes commit observers. Closed or failed registrations reject later
transactions. Scoped admission tokens also cover schema initialization after migrations.

The shared transaction-control guard preserves nested restrictions around both
lifecycle callbacks and finalizers. Each commit runs Signal first, then the commit
witness, then navigation. Admission refreshes producer functions on an idle
connection, including a connection retained from an earlier runtime.

Startup acquires the actual conversation lock before migrations in the application
lifespan thread. The same thread performs reconciliation, initialization and complete
installation. No producer gate spans an await. Installation rebuilds navigation and
creates the startup epoch before delivery or background loops start.

The conversation system accepts the exact borrowed lock handle. Successful system
construction enters the cleanup stack before later components. Earlier failures
release the bootstrap handle. Later failures drain the system before release.
Shutdown invalidates delivery, drains writers, then detaches the participant and
closes registrations. A refused guard drain retains module hooks and ownership.

Navigation delivery accepts injected authority checks. Current delivery requires
the exact active attachment permit. Degraded snapshots require coherent durable
knowledge and an activated structural projection. Each current snapshot and stream
retains its original permit. Installation checks use the SQL snapshot. Authority
checks occur after SQL reads close and again at send admission. Invalidation fences
later sends and cancels admitted sends. Bytes already accepted by the network cannot
be recalled. No producer gate spans a network wait.

Focused isolated tests connect the actual producer, retained connections, runtime
lease and delivery adapter. They cover bootstrap order, thread affinity, ownership
transfer, stale idle connections and failure cleanup. This bounded evidence does
not establish whole-application recovery or consumer cutover. Public attention has
separate source, delivery, and browser contract coverage.

`navigation.signal_publication` stores complete typed publications and their producer
cursor in private tables. Installation checks every conversation in the same source
transaction, then rebuilds navigation with a fresh navigation epoch. Batch acceptance
checks exact continuity, duplicate content, and conversation versions. Deletion
records retain version fences. Association-only batches advance the producer cursor.

The joined physical migration prepares schema5 and its private tables. Generic
schema creation and rebuild cannot activate schema6. Complete installation validates
the exact conversation inventory, installs publications, changes public metadata to
schema6, and rebuilds once in the same transaction. A
failed install restores the prior SQL state. Committed installation alone grants
no current delivery if callback registration fails.

Its typed attachment binder maps unavailable errors and checks installation identity
inside SQL snapshots. Startup registers the actual producer attachment before current
delivery admission. A caller must establish exact domain repair before a complete
replacement attachment. The binding never retries domain operations.

Accepted replacement and deletion publications mark their conversation roots dirty
before witness and navigation finalization. Projection reads exact installed current
attention. Projection rejects missing coverage, a tombstone for an extant conversation,
an epoch mismatch, or malformed facts. Canonical deletion removes the conversation
and address records. An order-only producer batch advances its private cursor without
inventing a public revision when no navigation source is dirty.

A successful registered admission supplies an opaque reconciliation handle before
SQLite begins. An uncertain result carries that same object through commit paths
and cleanup errors. Core does not inspect it or retry the operation. The Signal
lane owns evidence resolution. Failures from observers after a known commit do
not turn that commit into an uncertain result.
