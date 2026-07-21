# ACP-07 generic Ticket employee-backend selection — implementation review

## Verdict

**READY.** No unresolved P0/P1 contract, concurrency, migration, or product-path violation was
found in the settled implementation.

## Findings

None.

## Focused evidence

- **Post-feature schema corruption fails without rewrite.** `create_schema` invokes the v26 migration
  only for an incoming version below 26. A database already marked v26 must pass
  `_tickets_table_is_v26`; otherwise it raises before the v26 rebuild path. The focused corruption
  proof retains the original Ticket table SQL and row, leaves `user_version` at 26, and creates no
  scratch table. The migration itself writes literal `hermes` only for pre-v26 rows, rejects an
  existing non-Hermes binding before destructive DDL, and preserves legitimate non-Hermes values on
  v26 reopen.
- **Catalog and Worker-type registry cannot diverge.** One immutable
  `ConfiguredEmployeeRuntimeDefinitions` pair owns both authorities and rejects a registry whose
  catalog is not the exact same object. The app lifespan installs/restores that complete pair; startup
  audit, manifest, Ticket writers, composition, discovery, and the step runner all resolve through
  the paired configuration. Composition materializes the catalog once, and `AcpEmployeeRegistry`
  rejects any missing, extra, reordered, or runtime-key-mismatched materialization.
- **Selection writer and first binding serialize safely.** Both paths acquire `BEGIN IMMEDIATE`.
  The Ticket writer performs its complete pristine-Kickoff/session/binding check under that lock;
  binding CAS re-reads the stored selection under its lock and atomically writes the binding and
  Ticket session mirror. The two-connection latch proof covers both commit orders and permits no
  stored/bound backend mismatch.
- **Observation remains unbound.** The real Vite Ticket-route test mounts the ordinary fresh
  `awaiting_approval` Ticket, observes the existing rail and served two-key selector, saves an
  override, and verifies both the binding row and Ticket session mirror remain absent. The route
  derives its options only from the served catalog and contains no backend-name branch.
- **Deferred first demand is exact-once.** A pristine route constructs a deferred controller and does
  not open a transport on mount. Its first prompt retains one content/choice/message tuple, attaches,
  waits for admitted contiguous `ready`, and sends once. Focused controller/runtime proofs cover
  reconnect before ready, duplicate ready, reconnect after delivery, a rejected second pre-ready
  prompt whose composer draft remains intact, disposal, and the unchanged eager-control path.
- **Kickoff advance enables one eager attach.** The pane reacts when the pristine predicate becomes
  false and calls the controller's idempotent `attach`; component proof repeats the transition without
  a second call. The real route proof advances Kickoff and observes one generation-1 binding whose
  backend/session exactly match the Ticket mirror.
- **Human and Automatic Employee work share the selected non-Hermes runtime.** The production-
  composition e2e installs one paired Hermes/probe catalog and registry, creates the Ticket with the
  registered `probe-backend` override before Kickoff acceptance, sends a human prompt through the
  WebSocket hub, and invokes the real `EmployeeStepRunner`. Both prompts reach the same ACP session,
  and the binding plus Ticket mirror retain that same session and backend.

## Evidence consulted

Reviewed the frozen contract and implementation plan, settled product/frontend/e2e source, named
load-bearing tests, `implementation-report.md`, and `focused-checks.txt`. The recorded focused gates
are green: Ruff, strict Mypy over 44 affected source files, 356 focused Python tests, three selector/
runtime e2e tests, two CLI e2e tests, five frontend suites, zero Svelte diagnostics, and a temporary
production build. Per ticket scope, this reviewer did not run canonical `./verify` or another broad
test pass.

One apparent duplicate rollback seen during review was a read-output artifact: two adjacent `sed`
ranges both printed the same boundary line. A non-overlapping numbered read confirmed exactly one
guarded rollback in `_compare_and_swap_sync`; no correction was warranted or retained.
