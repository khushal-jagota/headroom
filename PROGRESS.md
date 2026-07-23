# PROGRESS

## Current work cycle (2026-07-23): Use the user's normal Hermes installation and home (`t_pw264y71`)

Hermes-backed Panels runtime now defaults to the operator's normal `~/.hermes` home instead of the
database-adjacent `data/hermes-home`. Production server startup no longer injects a
Panels-managed home, prepared-environment launches no longer export `PLAN_HERMES_HOME`, and the
worker-settings API now resolves the active runtime skills root from the same default home. The
prepared live/staging environment materializer no longer pre-provisions a separate per-environment
Hermes skill home as part of default prepare/reset, while the explicit `PLAN_HERMES_HOME`
override path and the legacy live-import Hermes-home copy path remain intact.

What passed: focused Ruff on the changed source/tests, focused Mypy on the five changed source
files, `git diff --check`, and the focused unit suite covering Hermes home resolution, environment
run-env injection, environment CLI/materialization, worker settings, and Hermes backend
materialization:
`tests/unit/test_conversation_hermes_backend_configuration.py`,
`tests/unit/test_environment_credentials.py`,
`tests/unit/test_environment_cli.py`,
`tests/unit/test_environment_fake_fixture.py`,
`tests/unit/test_environment_lifecycle.py`,
`tests/unit/test_worker_settings.py`,
`tests/unit/test_hermes_acp_backend.py`.

Next: propose Implementation for approval. No canonical `./verify` yet; that remains for later
integration/closeout, per the accepted ticket shape.

## Current work cycle (2026-07-23): Close rolling backup into staging (`t_12sap6vx`)

Current `staging` merged cleanly into the verified backup branch as `f4594bf2`. The first canonical
closeout run exposed one stale exploration wording assertion introduced by current staging's
`db560c78` documentation/skill change; the assertion now follows that accepted simple definition.
The backup implementation itself required no integration repair. The repaired prospective staging
revision `0271df5d` passes the canonical gate: Ruff, strict Mypy, all 1,362 unit tests, compile/CSS
checks, every frontend check/build/test, and all 123 Playwright tests; final `VERIFY: PASS`. Next:
advance and push `staging`, then clean up the Ticket branch.

## Current work cycle (2026-07-23): SQLite backup and operator restore (`t_12sap6vx`)

Implemented the narrow SQLite-only backup/restore slice. The repository environment CLI now creates
online, temporary, integrity-checked snapshots with checksum/revision metadata, atomically publishes
them, and retains the seven newest verified snapshots without duplicating database files for rotation.
Restore requires an explicit stopped-live acknowledgement,
rechecks verification, stages old SQLite sidecars for rollback, and atomically replaces the
destination while leaving stale canonical sidecars absent after success. Added
nightly/pre-deployment ops inputs resolve the current revision from the configurable live checkout.
First-class backup documentation and the separate operator runbook are current. Corrective review
also added clean malformed-metadata rejection, replacement-failure safety, deterministic retention,
and a real v34-to-v35 migration recovery proof. Independent re-review has no unresolved finding.
The canonical `./verify` passes Ruff, strict Mypy, all 1,358 unit tests, compile/CSS checks, every
frontend check/build/test, and all 123 Playwright tests; final `VERIFY: PASS`. Next: commit and
Implementation proposal.

## Current work cycle (2026-07-23): Make the Panels staging push explicit

Panels repository guidance now requires Closeout to push the exact verified staging revision to
`origin/staging` and confirm the remote ref matches before removing Ticket worktrees and state. The
Panels-specific wording is mirrored in `AGENTS.md` and `CLAUDE.md`; the generic coding-worker skill
continues to defer repository-specific integration and publication policy to each repository.

## Current work cycle (2026-07-23): Commit live and align staging from main

The live checkout is clean and attached to local `main` at `869619ca`. Its runtime work is committed
as separate catalog-cache, conversation-recovery, frontend-build, authentication-merge, and
documentation commits. Focused backend tests and the complete frontend test/check gates pass.

The staging checkout committed its New Conversation recovery tests, checkout and provider-home
bookkeeping, visual exploration, and the reviewed nested shared-frontend result. Generated Vite
cache churn in two completed Ticket worktrees was removed. `main` was then merged into `staging`
as `db93830c`; staging retains its newer dynamic-port environment contract and uses a frontend build
from the merged source. The one canonical `./verify` passed Ruff, strict Mypy across 156 source
files, all 1,335 unit tests, compile/CSS checks, zero Svelte diagnostics, the production build and
complete frontend suite, and all 122 Playwright tests; final `VERIFY: PASS`. No source or generated
application file changed after that run. Both role checkouts are clean and `main` is an ancestor of
`staging`.

## Current work cycle (2026-07-23): Consolidate staging and live checkout roles

The coding/integration checkout now lives at
`/Users/khushaljagota/Coding/planning-v2` on `staging`; the old
`/Users/khushaljagota/.hermes/planning-v2` path is absent. The moved repository still owns the shared
Git metadata, and every registered linked worktree was repaired and validated. The prepared staging
manifest, canonical staging skill links, virtual-environment wrappers, and editable package link now
resolve the Coding path. The detached live checkout remains separate and healthy at its accepted
main revision. Before the conversion, the primary checkout's local authentication commit and
uncommitted work were preserved under the safety branch
`safety/pre-topology-consolidation-20260723` and stash entry
`topology consolidation: primary checkout WIP 2026-07-23`; that work was then restored onto staging.
The live checkout and process were not modified or restarted.

## Current work cycle (2026-07-23): Durable Worker launch catalog cache (`t_nvv1550r`)

Backend/model-scoped Worker launch catalogs now persist in SQLite under a NULL-safe identity,
survive a server restart, and expire exactly 24 hours after discovery according to the injected
clock. Stored JSON is parsed and checked against its backend/model key before it is served.
Normal reads never force rediscovery; the two shared setup components offer a deliberate Refresh
which uses the existing abort and request-generation guards. One per-key in-process refresh task
coalesces concurrent discovery, and successful results atomically replace the durable row. A failed
refresh reports an error while leaving a stale row intact. Independent implementation review found
that the shared Worker/Chief defaults control initially hid saved values removed by a refreshed
catalog; it now renders those values as disabled unavailable options, matching Ticket setup, and a
second independent review confirmed the correction. Focused DB/catalog/API tests, Ruff, strict
Mypy, Svelte check, and diff check pass. Next: canonical `./verify` and Ticket Implementation
approval.

## Current work cycle (2026-07-23): Diagnose Claude auth failure in live Panels

The affected Claude worker fails because Panels launches Claude with the isolated live runtime
home `/Users/khushaljagota/.hermes/runtime/panels-environments/live/current/user-home`, not the
interactive shell home. `claude auth status` is logged-in for the normal home
(`khushaljagota@gmail.com`, Claude Max) but reports `loggedIn: false` and `authMethod: none` for
the Panels home. The managed `.claude/daemon.log` records a refresh failure on 2026-07-16 and
explicitly says `headless daemon cannot complete OAuth — run claude auth login`; its auth status is
still `auth_required`. No files or credentials were changed. Next: user runs login against the
managed HOME, then the live Panels process must be restarted so newly launched Claude children
inherit the refreshed state.

The isolated `HOME` is intentional: it keeps worker/Chief sessions, provider settings, plugins,
MCP configuration, and credentials separate from the operator's personal home and from staging or
preview environments. The current single-user macOS setup exposes a provisioning usability gap:
the isolated Claude home does not automatically share the interactive Claude login, and copying
the config file is insufficient because Claude also relies on its native credential/keychain path.

The owner decided the isolation tradeoff is not useful for this single-user local deployment:
provider CLIs should use the normal operator `HOME` everywhere, while Panels' own Hermes and
database state remains isolated by its explicit `PLAN_HERMES_HOME` and `PLAN_DB_PATH` values.
The launch environment helper and detached live checkout now implement that policy, with focused
environment credential/CLI tests passing. The live process was fully relaunched after repairing
stale runtime manifest metadata (missing port and symlinked generation paths); it now has the
operator `HOME`, while live HTTP remains healthy (200) and Panels-owned paths remain generation-
scoped.

Follow-up diagnosis: durable `conversation_session_bindings` still retain provider ACP session ids
after a process restart. A browser attach with such a binding first calls the registry's load path;
if the provider no longer has that session, the attach fails before the browser action loop is
available. The frontend then stays reconnecting, so it cannot send `new_conversation`. The Hub's
server-side `new_conversation` path can start empty without loading the old binding when reached,
but the current wire/attach boundary makes that path unreachable from a failed attach. This is a
real recovery UX/correctness gap, not evidence that New intrinsically requires the old session.

## Current work cycle (2026-07-22): Adopt persistent staging and separated live operation (`t_b5ja4rqu`)

The accepted `main` revision `279cb972` now anchors a local `staging` branch, a persistent staging
checkout, an isolated Ticket worktree, and a detached live checkout. Each checkout has its own
Python and Node dependencies, and the private `khushal-jagota/panels` origin is configured without
an initial push. A SQLite-consistent pre-adoption database backup plus managed files, Worker
settings, Hermes home, config, and logs lives under
`/Users/khushaljagota/.hermes/backups/planning-v2/t_b5ja4rqu-pre-adoption-20260722-1430`.

Prepared runtime environments are now exactly live and staging. Live retains fixed port 8767 and
has a stopped-only, staged `import-live` path for SQLite/WAL, managed files, Worker settings, and
Hermes, Codex, Claude, and archived-log state. The live import builds one complete generation,
switches one stable pointer atomically, and treats cleanup after commit as best effort. Staging keeps
a resettable fake database and durable Hermes/files/private-user-home state but no stored
runtime port; each launch carries an OS-bound dynamic listener through the supervisor into Uvicorn.
The prepared preview commands, contracts, registry allocation, Hermes smoke, Linux unit, env input,
docs, and tests are removed. The coding-worker skill has only the universal lifecycle sentence;
repository-specific setup, server use, integration, and cleanup guidance lives in `AGENTS.md` and
`CLAUDE.md`.

The persistent runtime root is prepared at
`/Users/khushaljagota/.hermes/runtime/panels-environments`. Staging inspection reports the dynamic
policy with no port; prepared live observes the current port-8767 lease and therefore refuses import
until the operator stops the untouched current server. Combined focused unit/E2E tests, Ruff, strict
Mypy on the changed source, diff checks, and preview-surface checks passed before corrective review.
The accepted correction also proves private identity modes/special-file exclusion,
cross-private-manifest isolation, atomic generation failures, prepared skill-link repair, and a
root-owned pinned Linux manager which validates and executes each target checkout's own interpreter.
The settled combined tree passes all 115 focused unit/E2E tests, including the real typed-ACP
Worker prompt/session proof, plus Ruff over every changed Python file, strict Mypy over the 13
changed source modules, and diff checks. Fresh independent re-review explicitly dispositioned all
prior corrective findings and reports no unresolved Implementation blocker. The canonical
Closeout gate first passed Ruff, strict Mypy, 1,330 unit tests, and every frontend gate, then exposed
that the ACP proof's internal `asyncio.run()` collided with Playwright's already-running event loop
only in the full E2E suite. The proof now runs its real ACP child on an isolated thread; the exact
browser-then-ACP regression, Ruff, and diff checks pass. The one canonical rerun on the settled
prospective staging result passes Ruff, strict Mypy across 156 source files, all 1,330 unit tests,
all frontend checks, and all 120 E2E tests; final `VERIFY: PASS`. Staging remains unchanged at the
accepted base. Next: operator stop, final quiesced backup/import, separated-live start and health
checks, staging advance/push/PR, and Ticket-worktree cleanup.

The worker-owned Closeout turn was automatically redispatched more than three times while the
operator-owned server continued to answer on port 8767. No migration or integration action was
repeated. The original checkpoint deadlocked because stopping the foreground server also stops the
Employee turn, so the operator could not confirm the stop before receiving the import/start command.
The durable implementation report now gives one complete stop -> atomic import -> normalized final
backup -> inspect -> separated-live run sequence. The verified Ticket launcher invokes the detached
accepted-main checkout's own interpreter. After Panels reconnects, continue with health/state/session
proof before advancing staging or cleaning anything up. The worker must not stop, signal, replace,
or launch the operator-owned server.

## Current work cycle (2026-07-23): Operator cutover for separated live operation

The old foreground `panels serve` launcher and its stale detached application process were
stopped after owner approval. The stopped-only live importer copied the source database, managed
files, Hermes state, runtime home, and logs into a complete atomic generation and advanced the
stable live pointer to `generations/generation-b_x625dv`. A recoverable final-cutover copy was
written under `/Users/khushaljagota/.hermes/backups/planning-v2/t_b5ja4rqu-final-cutover/`.

The detached live checkout at `/Users/khushaljagota/.hermes/live/planning-v2` is running on fixed
port 8767 through `panels environment run`. HTTP health and the Day/Review/Workspace/Board/Sprint/
Ticket endpoints returned 200, and environment inspection reports prepared and running. The
affected Ticket `t_b5ja4rqu` still cannot load its conversation replay: the imported replay is
4,433,710 bytes against the 1 MiB serving limit, so browser reconnects receive `conversation replay
unavailable; retry`. The process remains up for owner inspection; no repository code changes or
canonical `./verify` run was needed for this operator-only cutover.

The live-only temporary mitigation then raised the conversation replay limit from 1 MiB to 6 MiB
in the detached live checkout's conversation composition and Hub, with comments marking it for
removal after replay retention is fixed. Live was restarted successfully; HTTP returned 200 and
the affected Ticket replay connected without the previous replay-limit error.

## Current work cycle (2026-07-22): Materialize semantic browser replay for `t_b5ja4rqu`

The owner approved the two minimal shared replay changes: coalesce streamed message/thought text
for reconnect, and omit terminal-output deltas from reconnect while retaining final tool state.
The Hub now owns that backend-independent policy for Hermes, Claude, and Codex; backend definitions
no longer select replay retention. Live delivery remains exact. The focused regression delivers 101
original message/terminal/final envelopes to the attached browser, then reconnects without another
backend attach and receives only reset, one complete message, the final tool result, and ready. Text
coalescing is metadata-exact and contiguous, and genuinely oversized semantic state still fails
closed. The old orphan application was stopped after owner approval and `panels serve` now supervises
the healthy application on port 8767. The real `t_b5ja4rqu` WebSocket reaches ready with a contiguous
244-frame, 223,530-byte snapshot and zero terminal-delta frames, down from the prior 1+ MiB failure.
The same live stream later crossed the limit again during a long active turn. A fresh durable load is
only 359,561 bytes, proving the database and completed transcript are not the overflow. Since restart,
73 command completions carried 439,976 JSON bytes of final `rawOutput`; for 43 commands without a
separate streamed output event, the Codex adapter also embedded 344,942 bytes of the same output in
completion metadata. Panels retains both fields even though the frontend renderer reads neither.
Thus about 784,918 bytes of retained, invisible command output is the direct live-cache bottleneck.
The 657 standalone terminal deltas are being discarded correctly. Repeated provider diff snapshots
are ignored before browser replay but still inflate the unrotated adapter log. The owner approved
the minimal correction. The shared replay projection now omits `rawOutput` and embedded
`terminal_output` / `terminal_output_delta` metadata from completed or failed tool updates while
preserving exact live delivery and compact tool identity, title, kind, status, content, locations,
terminal exit, and unrelated metadata. The focused materializer and Hub regressions, Ruff, strict
Mypy, and diff checks pass. Canonical `./verify` passes Ruff, strict Mypy, 1,386 unit tests, all
frontend checks, and 119 Playwright tests. The supervised application restarted successfully. The
real Ticket first loaded a contiguous 421,115-byte snapshot through `ready`; a second warm attach
reached `ready` with 586 frames / 426,184 bytes and about 622 KiB of headroom. Both snapshots contain
zero `rawOutput` or terminal-output metadata updates. The 1 MiB value itself is a hard-coded
per-employee replay policy introduced with the ACP migration, not a WebSocket limit or a measured
browser threshold. It
bounds retained server memory and reconnect transfer/parse work and fails closed instead of serving
partial history. Raising it to 3 MiB would be a viable temporary availability measure after restart,
but would only postpone the same dead-output accumulation; a semantic progress-view snapshot should
remain comfortably below 1 MiB once unused output and superseded state are removed.
The settled tree's canonical `./verify` passes Ruff, strict Mypy, 1,386 unit tests, all frontend gates,
and 119 Playwright tests; final `VERIFY: PASS`. Final standards and spec reviews found one contract
edge case: malformed active terminal updates were projected as though they were completed. Replay
projection is now limited to completed or failed tool updates, and the focused materializer/Hub,
Ruff, and strict Mypy gates pass with an explicit malformed-active regression. Next: commit and
advance separated live after owner approval.

## Previous work cycle (2026-07-22): Diagnose replay and runtime size for `t_b5ja4rqu`

The Ticket's current Codex session remains unattachable with `1013 conversation replay unavailable`.
Its Ticket fields are only 11.5 KB and its event payloads are 67.7 KB, so SQLite Ticket data is not
the source of the megabyte replay. The current session has 4,713 agent-message deltas, 60 completed
command executions, 44 command-output deltas, and 21 turns; Panels retains ordinary replay envelopes
individually against a 1 MiB limit. Codex terminal output is also duplicated in the common streamed
output plus final `rawOutput` path because the adapter's completion metadata does not match the
materializer's exact terminal-final predicate. The frontend state keeps the full transcript and tool
results, although terminal metadata is ignored by its tool reducer. Separately, the unrotated
`data/codex-acp-logs/app-server.log` is 555 MB in about 39 hours; repeated skills/config responses
and ignored provider traffic are logged verbatim. Hermes `data/hermes-home/state.db` is 1.2 GB,
with 142 MB of messages and roughly 938 MB of regular/trigram FTS structures. Next: report the
direct failure, retention/serving problems, and separate disk cleanup opportunities; no fix made.
## Current work cycle (2026-07-22): Pause stale Codex thread for `t_b5ja4rqu`

The Ticket `Adopt staging and separated live operation on the current host` had a missing
Codex rollout bound to Panels session `019f88f7-0770-7af2-8543-8ff0e230a4f8`, producing a rapid
retry storm. A recoverable SQLite backup was written under `/private/tmp/`. The binding and Ticket
session mirror were removed, the active run was settled as errored, and the Ticket was marked
`errored` with the operator-cleanup reason. Historical runs remain intact; a five-second check
confirmed no new run or binding was created. Next: discuss the permanent retry/thread-lifecycle fix.

## Current work cycle (2026-07-22): ACP full-access reload invariant

Codex now launches its ACP adapter in `agent-full-access`. Claude's user configuration uses
`permissions.defaultMode = bypassPermissions`. Codex, Claude Code, and Hermes reassert their fixed
permission mode after every durable-session load while leaving historical Model and Reasoning
choices untouched. Every registry load, recovery, adoption, requested-cancel replacement,
compaction handoff, and backend-switch creation path goes through that invariant. Failed or
cancelled warm reloads invalidate the exact runtime and schedule child closure before returning;
the cancellation regression proves cleanup does not wait on a held publication gate.

The focused provider/registry suite, Ruff, strict Mypy, and diff checks pass. Independent contract
and standards reviews both reported `NO VIOLATIONS`. The one canonical `./verify` passed: Ruff,
Mypy, 1,383 unit tests, compile/CSS checks, Svelte checks and build, frontend contract tests, and
119 Playwright end-to-end tests. The scoped commit excludes the concurrent canonical-skill-source
work. The orphaned application process was stopped after explicit owner approval and replaced by
the normal `panels serve` supervisor. Its application child is healthy on port 8767, the control
socket exists again, and new ACP children now use the full-access reload invariant.

## Current work cycle (2026-07-22): Panels-owned canonical skill source (`t_hugw8uj1`)

The complete canonical tree now lives under `src/planner/skills/`; the repository base skill remains
`panels`, and the separate user-level update bridge is imported as
`panels-update-chief-of-staff`. One resolver now feeds Hermes provisioning and managed Worker
bootstrap, package metadata includes every skill asset, internal paths and live docs use the new
source, and managed Worker specialist edits still materialize runtime copies. Independent plan
review reported no violations. The focused 30-test skill/settings suite and 15 additional
environment/Hermes checks pass; `git diff --check` is clean.

Both project-native roots resolve the canonical tree, and the package/native-root assertion passes.
Independent implementation review accepted the indirect protected Codex link and found four
issues: stale CLAUDE guidance, an over-broad Hermes-link statement, stale memory, and the protected
user-level bridge migration. The repository findings are corrected. The owner then repeated the
instruction to propose Implementation after the protected-write blocker was surfaced, so the live
user-level swap is now an explicit Closeout provisioning action rather than a reason to withhold the
reviewable repository package.

The settled tree's one canonical `./verify` passed Ruff, strict Mypy over 155 source files, all
1,383 unit tests, compile/CSS checks, zero Svelte diagnostics, the production build and complete
frontend suite, and all 119 Playwright tests; final `VERIFY: PASS`. Next: propose Implementation
with the user-level `panels` copy/link replacement named as the remaining Closeout action.

Implementation is approved and Closeout resumed in the same durable generation-1 Codex
conversation after a Panels restart. Repository integration landed directly on `main` as
`8ffb6c65` from base `f9bd107e`; its 43-path staged scope contained only this Ticket's source,
package, links, docs, tests, decisions, and memory. Both user-level native roots now expose
`panels-update-chief-of-staff` as direct symlinks to the canonical package, and the old discoverable
`panels` bridge paths are absent. Project Codex and Claude roots resolve the same canonical tree;
Hermes shared links resolve it too, while managed `panels-worker-coding` remains a regular runtime
directory as designed. The old user-level directory and Claude symlink remain only as recoverable
backups under `/private/tmp/t_hugw8uj1-old-*`, outside every discovery path; permanent deletion was
not required for correctness and the command guard rejected it. Only the three pre-existing nested
worktrees remain dirty. Next: commit this final memory update and propose Closeout.

## Current work cycle (2026-07-22): Workers-page launch-default controls (`t_f0f8pp6y`)

The existing managed Worker/Chief launch-default authority is now exposed on the Workers page.
The page shows backend-driven Backend, Model, and Reasoning controls for each Worker detail and
Chief of Staff, saves through the existing validated endpoints, and labels the values as applying
to future launches. Registered backend keys are served by the management API; no permission field
was added. Ruff, Mypy, unit tests, Svelte check, production build, frontend tests, and the focused
Workers-page E2E suite pass. The canonical `./verify` reached the E2E gate but reported seven
unrelated existing flow/sprint assertion failures. Scoped changes are committed on main as
`4b8cf4aa`; no deploy or restart. Closeout proposal is next. Three unrelated nested worktree
modifications remain untouched.

## Current work cycle (2026-07-22): Empty Panels conversations and lazy ACP binding

The reconnecting failure was structural: Panels persisted an ACP session as soon as **New** was
pressed, but Codex does not make an empty session loadable until its first turn. A later Panels
restart therefore tried to load a provider session that did not exist durably. The scoped contract
and plan are in `orchestration/tickets/acp-lazy-new-conversation/`.

Implementation now gives Panels its own durable employee conversation generation. Browser attach
to an unbound conversation returns an empty reset/ready state without spawning a backend. **New**
advances that generation, clears the old binding and Ticket mirror, closes the old child, snapshots
Chief launch defaults, and leaves the conversation empty. The first browser or Automatic Employee
prompt creates and binds the ACP session through the existing registry and broker. Browser prompts
carry content rather than a fabricated provider session id, and stale generation cursors receive a
full current reset. Structured activation/load failures include employee, generation, backend, and
operation.

The real composition proves empty attach, first activation, New without replacement creation, old
child retirement, and generation-2 first activation. Final review also proved New can retire a
durable binding after a Panels restart without loading it, and that a Chief settings edit after New
cannot alter the accepted empty conversation's eventual launch setup. Canonical `./verify` passes:
ruff, mypy over 154 source files, 1,379 unit tests, compile/CSS checks, Svelte check, production
frontend build and tests, and 118 e2e tests. The scoped change is committed. The live supervisor
restarted onto schema 33 and
returned HTTP 200. The affected Ticket's poisoned generation-3 Codex binding was advanced to
generation 4; its first Automatic Employee prompt created the real generation-4 session, which is
now actively producing ACP updates instead of failing `thread/resume` with `no rollout found`.
Unrelated dirty nested worktrees remain untouched.

## Current work cycle (2026-07-22): Paint ACP browser replay atomically

Live Playwright measurement proves the Ticket-switch complaint is not a wrong final scroll position:
the transcript remained bottom-anchored at every observed scrollable frame, but long replay painted
in multiple commits and advanced the bottom twice (`6562 -> 7191`, then `9378 -> 10007`). The server
already owns an atomic ordered replay snapshot. Its subscriber bootstrap intentionally streams
individual envelopes, and the browser controller publishes after every one; Svelte then repeats the
correct follow-scroll against each growing DOM commit.

The contract places the missing transaction at the browser controller's admission/presentation
boundary. `reset -> replay -> ready` now reduces into a detached candidate while the last complete
snapshot remains visible, then commits and publishes once at `ready`. Failure discards the candidate;
post-ready live updates remain incremental. Before the production change, the controller regression
exposed one replay message through `snapshot()` before ready, and the real-controller mounted pane
grew 28 times before ready while remaining bottom-anchored. The focused controller and mounted-browser
regressions now pass, including replacement cutover, failure discard, committed-cursor recovery,
deferred-prompt ordering, and post-ready live growth. No Hub, queue, wire, adapter, pane scroll code,
visual control, pagination, or virtualization changed. All focused controller/component gates pass;
Svelte check reports zero diagnostics, the production build succeeds, and `git diff --check` is clean.
Independent diff review found one missing malformed-transport candidate-discard case; the exact
reset/message/raw-`not json` regression now passes and proves committed-cursor recovery. The first
canonical `./verify` then passed 1,358 unit tests and all 117 Playwright tests but stopped in the
frontend conformance script because that old fixture inspected grouping state before ready. The
grouping, plan-transition, and protocol-rejection probes now commit ready before inspecting replay
state while retaining post-ready incremental coverage. The focused conformance test and full
frontend `npm test` pass. One canonical rerun on the settled tree remains. The four pre-existing
dirty nested worktrees and unrelated backend edits remain untouched.

The final canonical `./verify` passed Ruff, strict Mypy across 154 source files, all 1,358 unit
tests, compile/CSS checks, zero Svelte diagnostics, the production build and complete frontend
suite, and all 117 Playwright tests; final `VERIFY: PASS`. Independent implementation re-review
reports `NO VIOLATIONS`. The contract landed on current `main` as `ca92361`, without staging the four
dirty nested worktrees, and the supervised Panels child was replaced successfully. Live switching
from Chief to the exact affected Ticket `t_xq6ragj3` showed the new pane empty at height 749, then one
complete 68-message transcript paint at height 25,300 with bottom distance 0. There were no
intermediate transcript growth commits and no visible travel through history. The equivalent Chief
load also had one transcript-bearing growth commit. The fix is complete and live.

## Current work cycle (2026-07-22): Materialize live terminal replay

Ticket `t_xq6ragj3` proves a second replay-overflow class after the Codex file-edit correction.
Its durable session privately loads as 590 normalized notifications / 361,465 bytes, but two live
verification turns emitted 4,140 `terminal_output_delta` notifications carrying 535,573 bytes of
terminal text. The Hub retained every transient browser envelope in the ready stream's append-only
reset buffer, crossed the 1,048,576-byte integrity ceiling, discarded replay atomically, and now
closes every Ticket attach with 1013 `conversation replay unavailable; retry` while the worker
continues running.

The landed atomic-session-load work fixes historical ingress saturation and browser reload races;
it intentionally retains the replay-byte ceiling and does not materialize live terminal progress.
The new contract keeps byte-identical immediate delivery for attached browsers but consolidates
well-formed terminal deltas by session and tool-call identity in the reconnect snapshot. Malformed
extensions and all other envelopes remain append-only; genuine materialized overflow still fails
closed. Contract and implementation-plan review passed. The RED Hub reconnect regression first
closed 1013 after 50 live deltas crossed its test ceiling; the implementation now preserves all 50
live deliveries while reconnect receives one consolidated terminal update. The first independent
diff review found provider knowledge in the Hub, permissive shape matching, and canonical byte-
accounting drift; all three were corrected. Focused Hub, Codex materializer/backend, and employee-
child tests now pass with Ruff, strict Mypy, and `git diff --check`. Root made only three mechanical
integration repairs: removed two unused values and added explicit Mapping/string narrowing for
strict Mypy. The final independent review reports `NO VIOLATIONS` after the reconnect regression
was strengthened at sequence 100 so subscriber resequencing changes digit width and would expose
the former accounting mutation. The canonical `./verify` passed Ruff, strict Mypy across 154 source
files, 1,354 unit tests, compile/CSS checks, zero Svelte diagnostics, the production build and
frontend tests, and all 117 Playwright tests; final `VERIFY: PASS`.

Landed on main and restarted. The first restart request exposed a separate lifecycle fault: the
nine-hour-old supervisor accepted the request but its application child did not exit after SIGTERM,
so no replacement had started and the first proof still exercised old code. The exact stalled
application process group was force-finished; its supervisor exited, so a fresh supervised
`panels serve` was started from the committed main tree. A direct WebSocket attach to `t_xq6ragj3`
then replayed 779 ACP updates plus reset/ready—781 envelopes / 698,415 bytes—reached `ready` at
sequence 6,632 on durable session `019f87af-1efe-7232-9e0c-456ceb42e0dc`, and received no prompt.
The four pre-existing dirty nested worktrees remain untouched.

## Current work cycle (2026-07-22): Bound Codex file-edit conversation payloads

Live reproduction for Ticket `t_m024gke4` is exact: its durable Codex session loads 239 typed ACP
notifications, but Panels attempts 241 browser envelopes totaling 1,137,603 bytes against the
1,048,576-byte reset-buffer limit. The hub therefore clears the atomic replay and closes every
attach with 1013 `conversation replay unavailable; retry`; the browser receives no cursor, renders
no history, and cannot send. Ticket `errored` status is unrelated.

One completed Codex `Editing files` update is 956,514 bytes. The pinned external Codex ACP adapter
expands two small append patches against `PROGRESS.md` and `decisions.md` into complete old/new file
snapshots. Panels will not patch or fork that dependency. Instead, its backend definition supplies
one typed normalizer applied inside ordered ingress after the raw/typed fingerprint match and before
both live publication and private replay capture.
Codex file edits will retain truthful paths, status, bounded changed hunks, original line origins,
and explicit truncation metadata without forwarding complete unchanged files. Other Codex updates
and all other backends remain byte-for-byte unchanged. The reset-buffer ceiling remains the final
integrity guard, with focused live/replay regressions followed by one canonical `./verify`.

The implementation now bounds Codex edit detail to 64 KiB beyond measured mandatory identity,
uses grouped small-file hunks and fixed-work large-file evidence, and carries truthful origin and
truncation metadata through transcript steps and permission prompts. Focused normalizer, real SDK
child live/private-load, frontend component, and Hub replay tests are green; the Hub fixture proves a
raw update above 1 MiB reaches `ready` after normalization without changing the replay cap.

Independent implementation review found and resolved collision-ordering and trailing-newline
presentation defects; the final review reports `NO VIOLATIONS`. The canonical `./verify` passed
Ruff, strict Mypy across 153 source files, 1,304 unit tests, compile/CSS checks, zero Svelte
diagnostics, the production build and frontend tests, and all 116 Playwright tests; final
`VERIFY: PASS`.

Landed on main and restarted. A live attach to the affected Ticket now replays all 239 ACP updates,
reaches `ready` at sequence 241, and transfers 198,376 bytes total; the largest envelope is 60,730
bytes. The pre-existing dirty main-worktree contents were restored exactly for every non-generated
path. The one generated-bundle conflict was resolved by rebuilding from the combined source tree,
so the dirty bundle contains both the landed fix and the owner's restored frontend work. Both
temporary stashes were removed; older owner stashes and all dirty nested worktrees remain untouched.

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log. Older cycles collapse into the "Recently landed" ledger at
bottom; the blow-by-blow is git's.

## Current work cycle (2026-07-22): backend-only Ticket errors (`t_2y1s72x4`)

Implementation is complete on the dedicated ticket branch. After merging current main,
Ticket schema v32 (following main's Chief-launch v31) owns the exact `backend_error`;
tracked ACP results distinguish confirmed backend Worker failures from conversation failures;
and the runner writes Ticket `errored` only for the former. Every non-error status writer
clears the reason atomically. Legacy v31 errors are
cleared back to their effective current-Stage resting control status because their
correctness rows did not preserve provenance. The migration uses a current-Stage ownership
override before v30's captured default, so user- and paired-owned work does not become
worker-dispatchable. Workspace exceptional treatment reads only this canonical fact, while
the Ticket page shows the exact reason.

Strict-TDD corrections cover migration ownership, restart/revision/session/collision
recovery, and preservation of concrete prompt and child-process backend reasons. The final
independent review reported both blocking findings resolved. The canonical `./verify` then
found one stale E2E assertion that still expected an intentional interrupt to error its
Ticket; that assertion now proves the Ticket returns to `empty`, keeps its session id, clears
`backend_error`, and retains the interrupted correctness row.

Current main 770c3f9 was merged into the ticket branch at f8e7ec0. The conflict resolution
preserves main’s Chief-launch v31 migration and adds backend errors as v32, while retaining both
Workspace contracts. Independent merge review reported NO VIOLATIONS. The prospective merged
tree’s canonical `./verify` passed Ruff, strict mypy, the unit suite, build and frontend checks,
and all 118 E2E tests, ending with VERIFY: PASS. No recovery control or timeout policy changed.
Main fast-forwarded from 770c3f9 to 8594f2b. The four pre-existing dirty nested-worktree paths and
their exact subproject diffs were preserved byte-for-byte. No restart or deploy was performed.
Next: propose Closeout.

## Current work cycle (2026-07-22): Atomic ACP session-load replay (t_k431pv7q)

Implementation is complete on the branch. A first or cold attach privately captures the complete
durable ACP session load and publishes it as one sequenced, atomic replay transition before racing
live callbacks. A per-generation publication barrier preserves that order without holding the gate
across external I/O; temporary sequencer pressure backpressures instead of dropping or failing; and
an ordinary reconnect to a ready stream uses its materialized snapshot without another load. Exact
source/barrier cleanup covers retry, child death, and shutdown; diagnostics remain content-free; and
the existing finite browser and external-operation protections remain in force.

Current main base `d5f4d0e` was merged into feature branch `ticket/t_k431pv7q-acp-replay` at
`282319d`. The prospective merged tree's canonical `./verify` passed Ruff, strict Mypy over 153
source files, 1,346 unit tests, compile/CSS checks, zero Svelte diagnostics, frontend build/tests,
and 117 e2e tests, ending with `VERIFY: PASS`. Main fast-forwarded from `d5f4d0e` to `282319d`.
The four pre-existing dirty nested-worktree paths and their exact subproject diffs were preserved.
No restart or deploy. Next propose Closeout.

## Current work cycle (2026-07-22): Coherent blocked-Ticket intake and workspace (`t_np7fjas6`)

Both ordinary and Chief external-work Ticket creators now accept zero or more existing
blocker Ticket ids through API and repeatable CLI options. Ticket insertion, every canonical
`blocks` link, and all creation/link events share the creator's one transaction; missing or
duplicate blockers return structured link errors with no writes, and a successful action
wakes eligibility once after commit.

The board exposes only the derived active-blocker fact. Workspace presentation keeps blocked
Kickoff Tickets in Kickoff, then places blocked post-Kickoff Tickets in a synthetic quiet
Blocked section above Kickoff without changing their real Stage or control state. Ticket
detail now omits empty blocker context, shows only direct active incoming blockers, and removes
links during Kickoff or later through the canonical delete route. Cleared and reverse rows are
removed from Ticket detail and copied Ticket text; Ticket-to-Sprint-item link behavior remains
unchanged.

The settled focused gates are green: the 141-test backend Ticket/link/Sprint/creator suite,
strict Mypy across 150 source files, focused Ruff, all frontend tests, zero Svelte diagnostics,
the production frontend build, and both Chromium blocker scenarios. Independent review found
indistinguishable blocker-removal accessible names. Exact browser RED proved no `aria-label`;
dynamic `Remove blocker <title>` labels and assertions are GREEN, and narrow follow-up review
reports `NO VIOLATIONS`. The first canonical `./verify` run had Ruff, Mypy, build, frontend,
and 116 Playwright tests green, but unit tests failed only because this isolated worktree lacked
the ignored locked `agent_backends` packages. `npm ci` from `agent_backends/package-lock.json`
restored the harness; the unchanged complete 1,297-test unit suite then passed. The unchanged full
canonical `./verify` then passed Ruff, strict Mypy across 150 source files, 1,297 unit tests,
compile/CSS checks, zero Svelte diagnostics, the production build and frontend tests, and 116
Playwright tests; final `VERIFY: PASS`. The creator guidance follow-up at `c759a2e` is included.
Main `0534d7d` was merged into the feature at `4e4e741`, with only a PROGRESS conflict that
preserved both active records. The prospective merged tree's canonical `./verify` passed Ruff,
strict Mypy across 152 source files, 1,297 unit tests, compile/CSS checks, zero Svelte diagnostics,
the production build and frontend tests, and 116 Playwright tests; final `VERIFY: PASS`. Main was
fast-forwarded from `0534d7d` to `30d7729`. Unrelated dirty work was stashed and restored unstaged;
restore conflicts preserved both board tests and the pre-existing local bundle pointer. All 16
untracked files matched their pre-merge hashes, and the dirty status path set matched except for
the old bundle deletion now owned by the landed commit. No restart or deploy. Next: propose
Closeout.
## Current work cycle (2026-07-22): Worker and Chief launch defaults (`t_xq6ragj3`)

Managed Worker settings now carry Backend, Model, and Reasoning defaults; new Tickets copy
their Worker's trio once. Managed Chief settings carry the same trio; each new Chief
conversation copies the then-current values into its durable binding while active bindings
remain stable. Production defaults are Codex, `gpt-5.6-sol`, and medium reasoning.

The live owner correction removed permission from settings and persistence entirely. No Ticket,
binding, API payload, managed file, or migration column stores it. Every actual new Worker or
Chief session instead applies backend-native full access: Codex `agent-full-access`, Claude Code
`bypassPermissions`, and Hermes YOLO plus `dont_ask`, with Hermes' residual adapter limitation
documented. Bound reloads do not reconfigure an existing session.

Independent review found a Chief settings/SQLite lock-order inversion and an incorrect first read
after last-known-good recovery. Both are fixed with focused regressions: Chief binding CAS now holds
the Chief settings lock before `BEGIN IMMEDIATE`, and recovery parses the restored launch trio.
The isolated branch was then rebased onto current main so the implementation gate included the
already-landed blocker and Codex-ingress work without integrating this Ticket into main.

Implementation was approved. Closeout merged current main `9945d0f` into the feature branch and
preserved both launch-default and newer atomic-replay behavior across two additive conflicts. One
new replay fixture was aligned to its intentional Hermes-only test catalog. An interrupted verify
also exposed fixed-port cross-run contamination in the environment-isolation E2E; readiness now
requires its supervisor socket and temporary ports are process-scoped.

The final prospective-tree `./verify` at `1484187` passed Ruff, strict Mypy across 154 source files,
1,357 unit tests, compile/CSS checks, zero Svelte diagnostics, the production build and frontend
tests, and all 117 E2E tests; final `VERIFY: PASS`. No deploy or restart is part of this Ticket.
Next: fast-forward main and propose Closeout. No blocker.

## Current work cycle (2026-07-22): Clear Worker-message attention on Ticket open (`t_m024gke4`)

The approved implementation plan adds one idempotent Ticket-open acknowledgement that clears only
the durable completed-response attention fact, routes it through the shared Ticket surface and the
existing ticket-scoped invalidation path, and keeps the server-side Workspace-dot classifier as the
single decision point. The classifier will restore the existing green completed mark for terminal
Tickets after exceptional, active, and attention states take precedence. Focused projection/API,
classifier/view, frontend, and Playwright regressions will cover both Ticket entry paths, preserved
attention, repeat acknowledgement, and terminal green before the one canonical `./verify` run.

Implementation and independent review are complete with no unresolved violations. The first
canonical `./verify` exposed only a faulty new Playwright setup: the completed card was correctly
present under the existing collapsed Done section, but the test waited for visibility before opening
Done. The test-only integration repair opens Done first; its focused browser regression passed.

The final canonical `./verify` passed Ruff, strict Mypy across 153 source files, 1,314 unit tests,
compile/CSS checks, zero Svelte diagnostics, the production frontend build and frontend tests, and
all 116 Playwright tests; final `VERIFY: PASS`. A concurrent external change landed during the run;
the green gate covers that combined current tree, and its unrelated Hermes configuration,
skill/worktree, and Vite-cache changes remain untouched. Closeout committed exactly this Ticket's
backend, frontend, tests, generated bundle, docs, and memory changes on `main` as `bf0b219`, from
base `553b0a4`. All unrelated worktree state remains unstaged. The documented Panels supervisor
restart was accepted, and a post-restart `panels ticket show t_m024gke4` confirmed the replacement
server is healthy on the expected `needs_closeout` Ticket. No external deployment applies. Next:
propose Closeout for approval.
Pre-existing `src/planner/conversation/composition.py`, nested `.claude` worktree, and `.worktrees/`
changes are unrelated and must remain untouched. No blocker.

## Current work cycle (2026-07-22): Quiet unmatched ACP tool updates (`t_7dr3czcm`)

The browser reducer now ignores a structurally valid `tool_call_update` when its `toolCallId` is
absent from `pendingToolCalls`; it adds no message, timeline entry, or unsupported-content marker.
Matched tool updates still patch normally, while recognized unsupported updates such as
`plan_removed` remain visible as unsupported agent content.

Focused TDD evidence: `node tests/acp-browser-state.test.mjs` first failed on the new unmatched-update
assertion, then passed after the reducer change. Main advanced by fast-forward from
`d60e62dda2aa7ac1693fdf989bf72d41009e724d` to `bc9de5ca40fa3099e006e8a7ac875c5b0146f7fc`.
The canonical `./verify` evidence remains the exact prospective/final tree result: Ruff, strict mypy
across 152 source files, 1,292 unit tests, compile/CSS checks, zero Svelte diagnostics, production
frontend build/tests, and 115 Playwright E2E tests passed; final `VERIFY: PASS`. Unrelated local
changes were preserved exactly. No deploy or restart applied.

## Current work cycle (2026-07-22): compact Workers Closeout (`t_6v0bjnwh`)

The approved compact Workers implementation and its direct-edit correction are reconciled with
current ACP-era `main` on `closeout/t_6v0bjnwh-current-main`. The integration preserves main's
Project v28 and Ticket-conversation projection v29 migrations; captured Stage ownership defaults
are the additive v30 migration. A backup copy of the live schema-v28 database migrated through
v29/v30 with 119 Tickets, no missing non-terminal defaults, no terminal defaults, and no foreign-key
violations.

Focused backend, frontend, environment-isolation, and browser gates pass. The independent full-diff
review found two Closeout defects: an independent skill-field save could overwrite the other field's
failed candidate, and environment preparation could materialize specialist settings from the wrong
parent before replacing its data tree. Both were fixed with RED/GREEN regressions. The final focused
review reports `NO VIOLATIONS`. The canonical gate also exposed an unrelated racy ACP test assertion:
a post-fork notification may safely arrive after the next load request once the exact-session private
epoch is installed. The test still proves exact private/source routing, replay order, and no deadlock,
but no longer asserts that unsupported wire ordering.

The settled tracked tree is reserved for the repository's one canonical `./verify`; its full output is
captured as the Ticket's managed `verify-closeout.log`. Only a `VERIFY: PASS` may advance `main`.
After landing, use the documented Panels restart, verify the Workers API and page, preserve the
unrelated `composition.py` and nested-worktree edits exactly, then propose Closeout.

## Current work cycle (2026-07-21): Ticket Workspace dot projection (`t_xb76vw05`)

The one pure `workspace_dot_state` classifier is implemented with exceptional > active > needs_attention
> quiet precedence. Board cards now carry its result from Ticket facts plus a durable Ticket-linked ACP
projection. Accepted delivery or real turn activity clears prior response attention; only a later idle after
that activity creates response attention. Initial idle, connecting/loading, repeated idle, and re-attach
preserve quiet or existing response facts. For Ticket activity, accepted delivery, and permission request/outcome,
Hub now validates the exact current employee+binding under one per-Ticket lock, awaits the SQLite projection write,
and publishes the ACP envelope as the final awaited operation; non-Ticket paths remain direct. New-conversation
reset happens only after replacement stream establishment succeeds. Compaction and requested-cancel recovery now
put their final stream commit/cutover under that same lock, while preparation, settlement, backend I/O, and replay
construction remain outside the lock. A failed permission-request publication compensates the pending projection
fact with `record_permission(False)` while preserving response attention. A replay-build failure after replacement
binding keeps the prior projection and reports a conversation error; stale publications queued behind replacement
fail validation before writing. The approved review P2 about done/paired/takeover field marks is intentionally
refuted: the single Workspace dot remains the derived Ticket result and no status mapping is restored.

Implementation commit `71c5971` is on feature branch `ticket/t_xb76vw05-workspace-dot`. The current target base
`3efab7a` was merged into the feature branch; conflicts preserved both main and ticket behavior, with main
schema v28 retained and the Ticket projection moved to v29. The merged frontend bundle was regenerated. The
prospective merged tree's canonical `./verify` passed Ruff, strict Mypy across 148 source files, 1,274 unit
tests, compile/CSS checks, zero Svelte diagnostics, production frontend build and all frontend tests, and 110
Playwright E2E tests; final `VERIFY: PASS`. Implementation commit: `71c5971`; base: `3efab7a`; integration
merge/main revision: `e4c4b07`. Main v28 was preserved and the Ticket projection is v29; the merged bundle was
regenerated. Main advanced by fast-forward to `e4c4b07`. The pre-existing unrelated uncommitted ingress-capacity
edit in `composition.py` was removed temporarily and reapplied exactly, and the pre-existing
nested-worktree/untracked-worktree state was left alone. No deploy or server restart was performed or required.
Next step: propose Closeout.

## Current work cycle (2026-07-21): Hide completed chat task lists closeout

Ticket `t_3udvypru` is integrated on `main` at merge commit `2c272c6`. The task strip keeps its
existing active-turn gate but additionally requires at least one pending or in-progress task, so a
stored all-completed plan cannot reappear during a later turn. Integration preserved the newer
environment and GFM work and rebuilt the combined frontend bundle from merged source.

Both the prospective merge and the final `main` tree pass the mounted-browser regression and canonical
`./verify`: Ruff, strict Mypy across 146 source files, 1,234 unit tests, build/frontend checks with zero
Svelte diagnostics, and 109 e2e tests; final `VERIFY: PASS`. No restart or deployment was required.
Next: propose Closeout for approval; no blockers.

## Current work cycle (2026-07-21): canonical-verify integration repairs

On current `main`, the environment closeout repairs remove the retired test-mode
`PLAN_GATEWAY_ADAPTER` assignment and adapt the process-level runtime-isolation e2e to the
current ACP-era system. That e2e now proves independent project/database and managed-file
mutations, plus distinct skill-only Hermes homes with no copied session, auth, or config state;
real unrelated durable sessions remain covered by the official ACP smoke instead.

What just passed:

- Focused Ruff passed for the changed environment, ingress-closure, and process-e2e files.
- `test_chat_ingress_contract.py` passed: deleted chat routes and legacy tables remain absent.
- All `test_environment_*.py` unit tests passed.
- Strict mypy passed across 144 source files, and `git diff --check` passed.

The requested process-level e2e passed after the current-ACP adaptation:
`pytest tests/e2e/test_environment_runtime_isolation.py -q`.

Next step:

- Final evidence: canonical `./verify` passed Ruff, strict mypy across 146 source files, 1,234
  unit tests, compile/CSS checks, zero Svelte diagnostics, production frontend build/tests, and
  109 e2e tests; final `VERIFY: PASS`.
- The integrated environment closeout is ready to propose. No operator server, VPS services,
  credentials, or deployment were touched.

## Current work cycle (2026-07-21): isolated runtime review corrections

Starting point is commit `78b2b5a`. This closeout fixes three independent review findings
without touching the operator server: normal environment launches now resolve the operator's
Hermes Python selection before HOME is scrubbed and pass it as contract-owned
`PLAN_HERMES_PYTHON`; the official ACP smoke now checks `end_turn` plus exact agent text; and
each smoke session is loaded through a fresh ACP child before its id is reported. Smoke-created
sessions are unrelated durable sessions owned by their isolated Hermes homes and are not deleted.

RED evidence:

- Added a focused CLI/run-env regression for the missing `PLAN_HERMES_PYTHON` contract value.
- Added official ACP smoke regressions for failed stop reason/output and fresh-child loading.
- The intended RED commands could not execute in this isolated checkout because its `.venv` and
  project dependencies are absent and network/package installation is unavailable. Current code
  inspection confirms each new assertion targets a present defect.

Current hypothesis:

- The minimal fixes are confined to the launch-env contract, official Hermes smoke lifecycle,
  the focused tests, and live environment documentation. Credential confinement and production
  Hermes backend defaults remain unchanged.

Next step:

- Run the requested unit, Hermes backend, Ruff, strict mypy, and diff gates when the project
  environment is available, then record the full evidence and commit.

## Current work cycle (2026-07-21): isolated runtime integration closeout

Ticket `t_2r1u7f39` is cherry-picked onto the current ACP-era main worktree at `28c439e`.
The integration keeps the current typed ACP conversation and documentation architecture.
Only stale environment seams are being adapted: deleted `planner.minds` smoke/config modules
now use the official ACP stack, and removed chat-managed-file storage now uses the current
database-backed managed-file layout.

Current hypothesis:

- The environment implementation is otherwise isolated and should need only compatibility
  repairs at those two seams. The opt-in Hermes smoke must still launch distinct real sessions
  from separate nonproduction Hermes homes.

What just passed:

- Added a focused RED/GREEN unit test for the official ACP Hermes smoke child. The smoke path now
  passes only parsed credential-file key names into an opt-in Hermes definition extension; the
  child revalidates those names, retains their values, and still overrides HOME/HERMES settings
  while scrubbing ambient PLAN/PYTHONPATH/session pollution. Production Hermes inheritance is
  unchanged.
- The requested environment unit suite passes with `PYTHONPATH=src`, Ruff passes across `src` and
  `tests`, strict mypy passes across 144 source files, and `git diff --check` is clean. The
  environment e2e is intentionally deferred to parent verification after merge because this
  isolated worktree cannot use the main editable `panels` binary.

Next step:

- Commit the settled integration repair. The parent agent owns the later full `./verify`.

Blockers:

- The process-level environment E2E cannot bind Unix control sockets in this sandbox; the parent
  environment can rerun it where socket binding is permitted.

## Current work cycle (2026-07-21): proper GFM rendering closeout

Ticket `t_vznnv05w` is integrated on `main`. Every shared Markdown surface now uses the
Vite-owned unified/remark/rehype GFM pipeline; raw HTML remains inert, unsafe content is
sanitized, and managed previews, chat images, exact tokens, and direct editing retain their
existing lifecycle. The old `assets/markdown.js` seam is removed and the live docs and built
frontend are current.

The stale ticket branch was merged with current ACP and frontend work before landing. The
settled tree passes the canonical `./verify`: Ruff, strict Mypy, 1,102 unit tests, compile/CSS
checks, zero Svelte diagnostics, production build and frontend tests, and 108 Playwright E2E
tests. Final result: `VERIFY: PASS`.

## Current work cycle (2026-07-21): ACP browser replay and live backpressure

Live diagnosis proved employees continued working and filing proposals while every fresh Chief and
Ticket conversation attachment failed before delivering one envelope. Six direct WebSocket probes
closed with 1013 `conversation client is too slow`. The server synchronously loaded replay into the
128-envelope live browser queue before starting its writer, so a valid long history was classified as
a slow client.

The fix is complete. First load, idle refresh, compaction, requested-cancel recovery, and active
attach now build and validate replay away from browser live queues. The attaching browser receives a
subscriber-local bootstrap; an already-connected browser receives an ordered cutover whose replay
does not consume live capacity. The production live queue remains genuine slow-client isolation and
is raised to 1,024 envelopes. Replay-unavailable and slow-live-client closures emit content-free
structured warnings, and permission-browser detachment is idempotent across server closure and
WebSocket finalization.

The first independent implementation review found production compaction/recovery bypasses, a
pre-writer refresh race, missing replay-classification failure handling, and an incorrect live-queue
log count. All were corrected and re-reviewed. Focused evidence is Ruff clean, strict Mypy clean, 29
hub/composition tests, and 4 selected real-WebSocket/slow-client tests. The settled canonical
`./verify` passed: Ruff; strict Mypy across 134 source files; 1,102 unit tests; compile/CSS/JS checks;
zero Svelte diagnostics; production frontend build and all frontend tests; and 107 e2e tests. Final
result: `VERIFY: PASS`. Next: commit the settled fix.

## Current work cycle (2026-07-21): project-and-Worker-type Closeout lanes

Ticket `t_scvazj90` is integrated on `ticket/t_scvazj90-closeout-lanes` against current
`main`. Closeout still uses the complete Automatic Employee-step eligibility decision;
its only added scheduling fact is that any non-empty matching effective-project +
Worker-type Closeout occupies the lane. Discovery chooses the oldest eligible waiter per
free lane, and the existing `BEGIN IMMEDIATE` claim rechecks the complete decision. No
queue, lease, claim record, migration, staging-PR queue, or Integration Worker was added.

The three merge conflicts were integration-only: preserve current ACP runtime docs, keep
both current gateway and Closeout test imports, and retain the current build snapshot.
The settled product/test tree passes Ruff, strict mypy across 134 source files, 1,098 unit
tests, frontend checks/build/tests, and 105 Playwright E2E tests in the canonical
`./verify` run: `VERIFY: PASS`.

## Current work cycle (2026-07-21): Visible system and programmatic prompts

The conversation pane now shows Panels-supplied prompts instead of hiding them. The broker publishes
the exact admitted Automatic Employee prompt and the one-shot employee role prefix as typed
\`programmatic_prompt\` envelopes; the browser folds them into the ordered transcript with visible
\`System message · worker\` or \`System message · role\` labels. Role echo filtering is removed, so live
and replayed ACP updates are forwarded unchanged. Ruff, strict mypy, 1,085 unit tests, the frontend
build/check/suites, and 105 Playwright E2E tests all pass in the final canonical \`./verify\` run:
\`VERIFY: PASS\`.

## Previous work cycle (2026-07-21): Worker model and reasoning selection

ACP-10 and the ACP migration are already complete on `main` at `34bb5c1`. ACP-11 implements the
owner's follow-up consistency decision without changing an upstream adapter: the repository's
canonical `skills/` directory is linked into the native Codex and Claude project roots, while the
existing Hermes-home provisioning remains the Hermes installation path. Claude's provider-specific
system-prompt append is removed.

Every successful new Ticket or Chief ACP session arms one role instruction. The first ordinary
message consumes it regardless of whether the message came from the browser or Automatic Employee
work. A first text-only slash command passes through unchanged and leaves the role armed. Loaded,
forked, and replacement children do not re-arm it. Prompt/replay echo normalization is one-shot and
operation-scoped, including Hermes' flattened text replay, so the Panels transcript remains exactly
the supplied prompt.

One independent implementation-review round found three concrete P1s: failed replacement session
creation lost the old arm, first slash commands were converted into model prompts, and permanent
exact-text filtering both missed Hermes' merged replay and risked hiding legitimate text. All three
are corrected. The settled focused gate passes Ruff, strict Mypy, 37 unit tests, and all 18 ACP
conversation e2e tests. Root also tightened command recognition to text-only prompts and inspected
the pinned Hermes flattening/command paths directly.

Live Safari dogfood against the current server is complete. A fresh Chief conversation accepted
`/help` as a real command, then loaded `panels-chief-of-staff` on the next message and returned the
requested exact reply. A fresh Ticket conversation loaded `panels-worker` and returned its requested
exact reply. Reload replay showed only the human messages, skill activity, and answers; neither role
directive appeared.

The single final `./verify` passed on the settled product/test tree: Ruff; strict Mypy across 130
source files; 1,027 unit tests; compile/CSS/JS checks; zero Svelte diagnostics; the production build
and every frontend suite; and 104 Playwright e2e tests. Final result: `VERIFY: PASS`. Only this
verification-result documentation changed afterward. ACP-11 then landed on `main` as `b097f92`.

Current research establishes that Codex and Claude expose live model and reasoning selectors through
ACP session config options. The pinned Hermes ACP adapter exposes model selection but no functional
session reasoning option. Owner direction is now settled for the first UI: separate Worker, Model,
and Reasoning controls live inside the Kickoff section beside approval, not in the Ticket header or
conversation chrome; omit Reasoning when Hermes is selected. Each Worker type's trio seeds a new
Ticket once, after which the user edits that Ticket's values directly—there is no reset or restoration
to Worker-type defaults. Model and reasoning are persisted as the requested first-session launch
configuration, not a live mirror: after initial binding ACP owns the session state, bound-session
loads do not reapply the stored values, and no post-Kickoff UI presents them as current settings.
Combined presets and post-Kickoff controls are deferred.

The implementation contract and four-ticket frontier are now published at
`orchestration/acp-model-selection/contract.md` and `tickets.md`: MR-01 owns the generic persisted
Ticket configuration, atomic pristine-Kickoff writer, catalog boundary, inside-Kickoff UI, and
session-application lifecycle; MR-02 through MR-04 add Hermes, Codex, and Claude provider behavior.
MR-01 and the three provider implementation plans are complete. The focused MR-01 plan review's
only finding—a losing first-binding candidate could carry stale launch inputs while adopting the
winner—is corrected and the narrow check is `READY`. The combined provider plan review is also
`READY`; MR-02 now explicitly owns its exact Hermes legacy-wire compatibility seam serially after
MR-01, while MR-03/MR-04 remain provider-local and parallel-safe.

MR-01's Ticket schema/domain/API and Kickoff UI slices are complete. The domain slice passes scoped
Ruff, strict Mypy, and 129 focused unit tests; it adds v27, seed-once defaults, pure dependency
normalization, the exact atomic writer/catalog REST shell, the server-owned pristine flag, calm
catalog failure, CLI adaptation, and both writer/first-binding race orders. The UI slice passes
Svelte check, its runtime component proof, the full frontend test script, and a production build;
Worker is gone from the header and the quiet setup row exists only beside Kickoff approval with
loading/error/Retry and stale-response protection. Its independent review found only missing
canonical test registration and invisible keyboard focus. Both are corrected: the new suites run
inside `npm test`, and a registered production-CSS browser proof verifies the restrained visible
focus treatment. The narrow correction review is `READY`.

The generic ACP catalog plus first-unbound-session configuration slice is complete. Its one focused
independent review found no production-code defect and only two bounded proof gaps: automatic-first
work must observe the selected pair through the shared gateway, and bound attach/recovery/compaction
must explicitly observe zero configuration reapplication. Both are now closed with tests only: the
real EmployeeStepRunner/AcpStepGateway route proves configuration then binding then first automatic
prompt, and an observing adapter proves zero reapplication across every named bound path. The narrow
correction check is `READY`, so MR-01 is complete.
The persisted fields are deliberately named `employee_launch_model`
and `employee_launch_reasoning_effort` so they cannot be mistaken for live state. Stable ACP
configuration runs model before refreshed reasoning, and temporary discovery sessions are closed.
The first Ticket binding is fail-closed on the prepared complete trio; a CAS loser can adopt only
after repository re-resolution clears launch inputs. Existing loads, replacement/recovery,
compaction, and New Conversation do not reapply historical values. The settled combined MR-01 gate
passes 404 domain/runtime unit tests, the registered frontend suite, zero Svelte diagnostics, and
the focused catalog-no-binding/first-prompt E2E proof. All three provider registrations are now
complete. Codex and Claude share their durable factories with the generic semantic-category adapter.
Hermes uses a standalone read-only current-provider probe and the exact legacy
`session/set_model` wire, with no Reasoning. The settled cross-provider gate passes Ruff, strict
Mypy across 31 conversation files, 343 focused Python tests, the registered frontend suite, and zero
Svelte diagnostics. The one combined provider/docs implementation review is `READY` with no P0/P1
findings. The first canonical `./verify` passed Ruff, strict Mypy, the frontend/build gates, and 103
of 105 Playwright tests, but failed because two pre-existing route tests still targeted the
deliberately removed header Worker selector. A bounded independent spot check confirmed no
production defect. Those tests now target the inside-Kickoff setup and prove it disappears after
first binding or Kickoff approval; the corrected slice passes 2/2. The settled replacement
`./verify` then exposed six more stale exact-shape unit fixtures: four Worker-type manifests omitted
the two new default fields, and one external-work event pair omitted the seeded launch values. No
production correction was needed. The fixtures now assert the complete public shapes and the exact
slice passes 6/6. The final clean `./verify` passes Ruff, strict Mypy across 134 source files, 1,084
unit tests, compile/CSS/JS checks, zero Svelte diagnostics, the production build and registered
frontend suites, and 105 Playwright e2e tests: `VERIFY: PASS`. Three fresh live Tickets are ready for
real Safari dogfood; the Mac is currently locked, so UI operation resumes after the owner unlocks it.
Blockers: desktop unlock only.

## Prior work cycle (2026-07-19): ACP migration — architecture and contract freeze

Owner direction: ACP replaces both non-ACP conversation paths and becomes the single system. P2 of
the shared-registry program is superseded and will not be implemented; P1's employee-keyed child
ownership remains reusable. The Hermes checkout is strictly read-only, so this repository will stop
depending on its local patches but will not physically revert them.

Reference study is COMPLETE. Source pins and protocol findings are recorded in
`orchestration/acp-migration/research.md`. Hands-on computer-use study covered Zed 1.11.3 with a real
Codex ACP session (typed/collapsed thought, compact tool rows, inline diff, streamed terminal,
mid-turn queue + Send Now interruption, permission outcomes, command palette, visible compacting)
and local acp-ui 0.1.16 against `hermes acp`. Owner design ruling: acp-ui is behavior-only and is not
a visual donor; Zed supplies the legibility bar, while Panels' existing tokens and restrained,
cardless spatial language remain the design source.

Architecture/ticket graph is frozen at `orchestration/acp-migration/plan.md`: server-side official
ACP SDK; typed updates through one thin websocket envelope; ordered per-session ingress; durable
employee/session binding; first-party turn broker (Steer / Send Now / Queue), compaction normalizer,
and transient permission broker; Svelte typed transcript; Hermes-first vertical cutover; dogfood;
legacy deletion; then functional Codex and Claude Code worker backends with a Worker-type default and
per-Ticket Kickoff selection. Gemini was explicitly removed from delivery by the owner on 2026-07-20.

Independent Codex program review is COMPLETE. Round 1 found five blockers: exact step-runner
settlement/CAS obligations, the complete registration matrix, command provenance, permission shape,
and mandatory ownership classification before legacy deletion. Every finding was accepted and
amended into the plan. Round 2 found no unresolved blockers and returned `READY`; full outputs and
dispositions are in `program-review-round-1.md` and `program-review-round-2.md`.

Next: cut/freeze ACP-00 contracts and conformance harness → sub-agent implementation plan → independent
plan review → sub-agent implementation → independent diff review. No product code has changed in this
cycle and `./verify` has not been run (correctly: there is no settled implementation to claim).

Owner review-process override: ticket plan/diff reviews may be performed by independent sub-agents;
the Codex CLI is no longer required per ticket. The same author/reviewer separation, written findings,
dispositions, and two-round maximum remain, with one focused round the normal case and another only
for a concrete unresolved or load-bearing correction. Contract inspection also corrected two overstatements before
ACP-00 freeze: ACP permission options have `kind` but no separate `scope`, and Queue/Send Now are
common Panels broker behavior rather than backend capabilities (only native Steer is backend-specific).

ACP-00 is now CUT and plan-reviewed. The contract lives at
`orchestration/tickets/acp-00-contracts-conformance/contract.md`; its implementation plan received one
focused sub-agent review. The review's sole blocker (the harness must prove the official client-side
stdio path, not reserve it for ACP-01) and its type-ownership proof gap were both accepted and amended.
They are bounded test-mechanics corrections, so a second plan-review round would add ceremony rather
than confidence. Next: dispatch ACP-00 implementation, run focused tests, independently review the
allowed-file diff once, then integrate and run the canonical verification gate.

ACP-00 implementation, focused review, and integration are COMPLETE. The slice adds exact Python/TypeScript pins,
the four-file byte-identical typed-state donor with provenance/license, frozen conversation/backend/
wire contracts, Python-emitted browser fixtures, and a real official-SDK scripted conformance harness.
The settled focused sequence passes ruff, strict mypy, 66 Python tests, the direct TypeScript contract
test, Svelte check, and the full web test script. The implementation review found three substantive
proof defects; all were corrected, and its narrow correction check marked every finding resolved and
returned `READY`.

The first canonical `./verify` was contaminated by the owner's live, uncommitted
`relay_backend_enabled: true`: 64 unit and 35 e2e legacy-chat tests failed while every static/frontend
gate passed. A controlled public-seam rerun proved the same representative unit and browser failures
become `2 passed` at the checked-in relay-off default. The clean canonical run then passed Ruff, Mypy
across 145 source/typing files, 1,119 unit tests, compile/CSS, Svelte diagnostics, frontend build/tests,
and 135 e2e tests (`VERIFY: PASS`). The live relay-on value was restored immediately; no product or test
code was changed to manufacture the result.

Next: cut ACP-01 (generic child runtime + Hermes definition), plan it against the frozen ACP-00
contracts, and use one focused independent plan review before implementation.

ACP-01 is now CUT at `orchestration/tickets/acp-01-child-runtime-hermes/contract.md`. It freezes an
uncomposed official-SDK child/factory, bounded ordered ingress, an async employee-keyed ACP registry,
an injected durable-binding CAS seam, and the pinned Hermes definition. Child generation is process
ownership; binding generation changes only with ACP session identity. Attach/load includes an
observed-frame-to-typed-consumer barrier rather than a timer, and ingress overflow retires the child
instead of dropping updates. A fresh sub-agent is writing only the implementation plan; source work
has not started.

The disjoint ACP-03 frontend ticket is also CUT at
`orchestration/tickets/acp-03-browser-state-pane/contract.md` and is being planned in parallel. Its
visual thesis is deliberately narrow: the existing cardless Panels transcript, compact closed
thought/tool disclosures, one persistent status line, and permission as the only prominent blocking
inset. It freezes one fixture-driven transport/controller/reducer, fail-closed sequence/generation
handling, donor corrections with provenance, and the minimal Svelte inventory; no route, legacy pane,
shared token, or app layout changes are allowed.

Contract cutting exposed one real ACP-00 omission: ACP session updates embed only a terminal ID,
while accumulated output/exit state belongs to the client's reverse terminal service. ACP-00a is now
CUT at `orchestration/tickets/acp-00a-terminal-state-wire/contract.md` to add one typed
`terminal_state` envelope wrapping the exact SDK output response before ACP-02/03 implementation.
This is not a parallel transcript vocabulary and terminal output remains tool state, never assistant
text.

ACP-00a implementation is COMPLETE. Its exact Python/TypeScript SDK wrapper, eleventh union variant,
exports, canonical fixture, and focused checks are green. The one independent review found no
production defect and one missing common-envelope mutation proof; the orchestrator added the seven
identity/generation/sequence cases and the corrected suite passed 64 tests plus Ruff and the
TypeScript contract test. No second broad review was used.

ACP-03 planning then exposed a second small wire omission: unsupported Steer cannot be disabled
without a machine-readable capability. ACP-00b is CUT at
`orchestration/tickets/acp-00b-steer-capability-wire/contract.md` to add required `supportsSteer` to
the existing connection payload. This avoids backend/command/text inference and adds no event family.

ACP-00b implementation is COMPLETE. Required strict `supportsSteer` now crosses every connection
state; reset/ready and closed/error fixtures, invalid type/missing/extra proofs, Python/TypeScript
contracts, and focused gates are green. Its independent review returned READY with no findings.

ACP-02 is now CUT at
`orchestration/tickets/acp-02-turn-broker-reverse-services/contract.md`. It freezes the active-turn
state machine, server-owned FIFO/Send Now/declared Steer, reject-all queue recovery on child death,
compaction capture isolation, exact attached-browser permission ownership, confined filesystem, and
scoped bounded terminals. Planning begins only after ACP-01's lifecycle plan is frozen; source work
waits for ACP-01 and ACP-00a integration. The concrete implementation plan is now complete and in
one focused independent plan review. That review returned NOT READY with eight load-bearing gaps:
cancel timeout ordering, closing-cause successor policy, exact-generation runtime leases, permission
source/late-open ownership, multiple automatic compactions, private-capture isolation/provenance,
race-safe write confinement, and terminal kill/end-to-end cleanup bounds. The plan now resolves each
one explicitly, including a fresh child-generation replacement for private capture so unrelated live
updates cannot be swallowed. The narrow check resolved six and identified two remaining integration
edges: intentional generation-N retirement callback classification and assistant/merged Hermes
summary placement. The plan now has exact planned-retirement tokens and all four pinned replay
placements. The two-round cap is exhausted, so the orchestrator recorded the final finding-by-
finding disposition as READY; ACP-02 source still waits for corrected ACP-01 integration.

ACP-01's one focused plan review returned NOT READY with two real blockers and three bounded high
findings. The orchestrator accepted them: the observer will reserve/fingerprint ordered slots and
emit the frozen rejection for SDK-router-invalid replay; a per-employee publication/update gate will
quiesce admitted N sinks before N+1 is visible; the direct ACP-00 reference subject will receive the
same deterministic load barrier; generic cwd comes from the backend resolver; and the factory fails
closed when the SDK would inject an undeclared default environment name. The contract and plan were
corrected finding-by-finding before implementation dispatch.

ACP-03's one focused plan review returned NOT READY with one blocker, two high findings, and one
bounded contract correction. All four are accepted and amended: ordinary Hermes mode/config/session
updates are typed non-transcript metadata; `AcpComposer` receives the exact connection
`supportsSteer` value; gap/browser errors clear only after a contiguous replacement-epoch ready
event while protocol rejection persists; and Svelte receives a recursively readonly/frozen public
projection rather than the donor's mutable maps/arrays. The reviewer explicitly found the remaining
reducer, terminal, component, and visual plan sound and said these dispositions do not justify a
second broad plan review. ACP-03 is ready for implementation in its disjoint frontend/fixture scope.

ACP-01 and ACP-03 implementations are in progress in disjoint scopes. Orchestrator spot-checking of
ACP-01 found three lifecycle failures before review: injected durable reads under the global lock,
shutdown waiting on an unfulfilled observer reservation, and a downstream sink exception that could
strand the load barrier. The implementer accepted all three and is adding explicit concurrency and
failure proofs. ACP-03 has the pure reducer/controller/transport, conformance adapter, and restrained
component inventory under construction; it has not touched routes or shared visual assets.

ACP-01 implementation is now ready for independent review. Its focused Ruff and strict Mypy gates
pass, its exact ACP-01 plus ACP-00 conformance set is 44/44, the ACP TypeScript contract and Svelte
checks pass, and scoped diff-check is clean. The corrected tests cover all three orchestrator-found
lifecycle failures. Two broader frontend assertions are deliberately deferred while ACP-03 owns the
concurrent donor provenance/hash and managed-markdown caller inventory; ACP-01 did not touch or
revert that work.

ACP-03 implementation is now also ready for independent review. It adds the unmounted typed
transport/controller/reducer, corrected donor helpers and provenance hash, canonical browser
fixtures/conformance adapter, and all nine restrained Svelte components without touching routes,
shared CSS/tokens, package files, or the legacy pane. Its focused contract/state/conformance/
component tests, Svelte diagnostics, and production build pass. ACP-01's one implementation review
is running; ACP-03's one review follows as soon as a reviewer slot is free.

ACP-01's independent implementation review returned NOT READY despite its green focused suite. It
found strict-wire reservation and unsafe-discriminator failures, forced-close/load wakeup and
accepted-prefix overflow gaps, new-conversation persistence-failure teardown, a non-hard shutdown
deadline, incompatible backend demand coalescing, and missing named lifecycle proofs. The original
implementation author is correcting exactly those findings in the existing ticket scope; no broad
second review is planned, only a narrow correction check after the focused suite expands.

ACP-01 correction and independent narrow review are now COMPLETE. All seven findings are resolved:
raw reservation is transactional and display-safe, every terminal ingress path wakes response/load
waiters, overflow drains the complete accepted prefix, failed conversation replacement tears down its
child, registry shutdown obeys one hard deadline, incompatible employee/backend demands are never
coalesced, and the named lifecycle/race proofs are present. The settled focused suite is 70/70 with
scoped Ruff, strict Mypy, ACP TypeScript contracts, Svelte diagnostics, and diff check green. The
reviewer returned `READY`; ACP-02 may now implement against this exact runtime boundary.

The orchestrator's required load-bearing spot-check found the reviewed mechanics present in source:
observer reservations append complete valid/rejected slots before ordinal advance; overflow stops new
acceptance while typed callbacks can finish the reserved prefix; retirement publishes a terminal
cause before cancellation; conversation replacement retires a session-created child on persistence
or publication failure; and shutdown shares one absolute deadline across graceful and force-close
work. No additional ACP-01 correction was needed. ACP-02 implementation is now active in its reviewed
allowed-file scope.

ACP-03's independent implementation review likewise returned NOT READY with eight bounded production
and proof gaps: missing Panels-owned payload invariants, higher-generation reset entity drift,
human-echo fallback ordering, conformance bypassing transport/controller admission, source-only
component checks plus missing new-error alert behavior, permission-send retry lockout, a disposal
publication, and receipt recency based on record insertion order. Thought isolation, metadata,
tool/plan/terminal folding, immutable projection, recovery, Steer flow, donor provenance, line diff,
and the restrained visual boundary were confirmed sound. The original author is correcting exactly
the eight findings and replacing source-only acceptance with runtime component interaction proof.

ACP-03 correction and independent narrow review are now COMPLETE. All eight findings are resolved at
their production boundaries, including strict transport invariants, entity-stable generation reset,
missing-ID human turn boundaries, complete fake-socket-to-reducer conformance, retryable permission
sends, silent disposal, and explicit receipt recency. The component proof now mounts the real pane in
Chromium, exercises all nine contracted components and controls, and verifies the new-error alert
without duplicating the one persistent status line. Fixture/contracts/state/conformance/component
tests, Ruff on the Python harness, Svelte diagnostics, the full web test command, and production build
are green. The reviewer returned `READY`; the pane remains deliberately unmounted until ACP-04.

The orchestrator's ACP-03 spot-check found the reviewed boundaries present in source: transport
receipt/activity/permission cross-fields fail closed; employee/entity/session/generation admission is
checked before reduction; a non-optimistic human echo clears the missing-ID agent group and advances
the turn exactly once; thought remains a distinct collapsed part; protocol failure remains visible;
and the pane exposes one persistent status line. No ACP-03 correction beyond ACP-04's explicitly
contracted same-binding restart reset is needed.

ACP-04 is now CUT at `orchestration/tickets/acp-04-hermes-vertical-cutover/contract.md` while ACP-02
implementation runs. It freezes one production conversation hub/websocket, an explicit durable ACP
binding table mirrored atomically to the existing Ticket/Chief session fields, typed reset/load/ready
and active-turn replay behavior for multiple browsers, the synchronous `AcpStepGateway` bridge that
preserves EmployeeStepRunner's caller-thread session claim, and the three exact Svelte mount points.
It also closes a restart integration edge: a same-binding reset greater than the retained cursor may
rebase the stream, after which ready and ordinary events are contiguous again. Planning waits for
ACP-02's actual settled internal API so the planner does not invent an adapter against a draft.

ACP-02 implementation has completed TDD phases 1–5: ordinary FIFO/Send Now/ID rejection,
response-consumption and private-capture ingress, Hermes native steer/provenance with all four pinned
summary placements, permission attachment/first-settlement/tombstones, and descriptor-backed
filesystem confinement. Its current new focused subsets are 24/24 and the ACP-01 child compatibility
set remains 30/30. Scoped real-subprocess terminals are now in progress; the implementer reports no
contract blocker. SDK reverse bridging, production conformance probes, and the final focused sweep
remain before independent review.

The orchestrator's first ACP-02 load-bearing spot-check identified two integration gaps before the
ticket may report: the registry still returned the raw backend strategy instead of the defined
exact-generation proxy and the capture path did not visibly hand the replacement N+1 handle back to
the broker; additionally, a publisher exception on a public actor command settled only that caller
instead of failing the generation. The shutdown close path's broad exception suppression also needs
an exact audit. These findings are with the active implementer for correction and deterministic
generation/publisher/deadline proofs, not a separate review round.

The exact-generation strategy proxy and capture replacement handoff are now present in the active
ACP-02 source, and focused permission/terminal coverage has expanded. A second orchestrator
spot-check found that the standalone permission and terminal services were not yet composed through
the turn actor: cancel, death, new conversation, and shutdown therefore could not own late permission
tombstones or terminal cleanup as the reviewed plan requires. Terminal publisher failure could also
strand release waiters, and pending cleanup tasks were cancelled without being awaited. The same
implementer is closing those integration and hard-deadline paths before reporting; ACP-02 remains in
implementation and no independent diff review or canonical `./verify` has started.

That ACP-02 lifecycle correction is now behaviorally green. The broker owns source-aware permission
admission and terminal cleanup across cancellation timeout, child death, compaction N→N+1, new
conversation, and shutdown; permission/terminal publication failures stop delivery; official-SDK
permission, filesystem, and terminal bridges are exercised; and the production ACP-02 subject passes
ACP-00 probes 5/7/8 with the matching mutations failing. The five named suites pass 66/66. The
implementer is resolving only scoped Ruff/Mypy findings, then will run the complete reviewed focused
gate and write `implementation-report.md`/`focused-checks.txt`. Independent diff review has not yet
started, and the canonical `./verify` remains correctly deferred.

The first complete ACP-02 acceptance-8 run then reached 206 passing Python tests and one unrelated
ACP-00 provenance assertion failure: that older test still required the byte-for-byte upstream
`sessionStore.ts` hash after ACP-03's reviewed local reducer corrections. The orchestrator made the
small integration repair in `tests/unit/test_conversation_contracts.py`: it now asserts the complete
original upstream hash table and, separately, the documented ACP-03 local hash/correction section.
The exact test passes. The ACP-02 implementer is rerunning the entire focused sequence from its first
command so the final evidence is uncontaminated by the repair.

ACP-02 implementation and final focused evidence are now COMPLETE pending independent diff review.
The settled gate passes scoped Ruff; strict Mypy over 18 conversation files; 207 combined ACP
Python tests (68 owned by ACP-02); the ACP TypeScript contract; Svelte diagnostics with zero errors
or warnings; the package web suite; the three standalone ACP browser state/conformance/component
suites; and scoped diff check. `implementation-report.md` and `focused-checks.txt` retain the exact
commands and output. A fresh independent sub-agent is reviewing only concrete contract/race
violations, including late callbacks after actor closure and cancellation-resistant hard deadlines.
No canonical `./verify` has run, correctly, and ACP-04 planning still waits for this review verdict.

ACP-02's independent implementation review returned `NOT READY` with four reproduced ownership
failures despite the green focused suite: a paused generation-N acceptance could resume on a
concurrently installed N+1 handle; late child/permission/terminal callbacks could enqueue after the
actor runner exited and hang; cancellation-resistant cleanup could exceed the one absolute shutdown
deadline; and permission activity publication failure could contradict an already-visible selected
outcome or self-cancel the settlement path and strand the ACP callback. One bounded lifecycle/activity
typing weakness was also accepted. The original implementer has one correction pass for exactly
these findings; the same independent reviewer will then perform only a narrow finding-by-finding
check. ACP-04 remains intentionally undispatched until that check is READY.

That bounded ACP-02 correction pass is now COMPLETE. Runtime adoption is serialized through the
actor and retains each submitted complete handle; only the broker-owned capture handoff rebinds
queued intent from N to its returned N+1. Command admission closes atomically with actor-runner exit
and drains every admitted callback. Deadline expiry force-detaches cancellation-resistant broker,
permission, and terminal work without a second wait. Permission outcome publication is the response
commit point, so later activity failure is generation-fatal but cannot revoke the selected ACP
response or self-await its settlement task. Lifecycle/activity state is frozen to the contract
types. The named correction set passes 11 tests; the complete affected ACP set passes 214 tests with
scoped Ruff, strict Mypy, and diff check green. The same reviewer is now checking only these five
findings; ACP-04 planning remains the immediate next step after a READY verdict. Canonical `./verify`
is still deferred to the serial integration gate.

ACP-02 is now SETTLED. The same independent reviewer checked only the five round-one findings and
returned `READY`: exact generation-N delivery and controlled N→N+1 FIFO capture are distinct and
correct; closed actors cannot strand late callbacks; cancellation-resistant ownership is detached
at the one absolute deadline; permission response commit precedes fallible activity restoration;
and lifecycle/activity types retain the frozen contract vocabulary. The reviewer independently
reran the named correction set (11/11). The full affected evidence remains 214 passing ACP Python
tests plus scoped Ruff, strict Mypy, and clean diff check. The orchestrator spot-check agrees with
the finding-by-finding review. ACP-04 Hermes vertical-cutover planning is now active against the
settled ACP-02 API; no canonical `./verify` runs until the program's frozen final gate.

The ACP-04 planning audit has identified three concrete adapter seams that the plan must close
without changing frozen wire values: registry ingress must retain employee/child source identity
because a rejected update has no session ID (and a valid same-session update can still belong to a
stale child generation); the turn broker needs one exact prompt-epoch completion handle so the
synchronous step gateway does not infer success from an idle activity event; and Ticket/active-step
session validation must occur at the permission broker's selection boundary so a stale worker
permission cannot win between a hub-side DB check and settlement. These are internal extensions of
the settled single owners, not reasons to add another registry, broker, or transcript state machine.
The source-aware ingress adapter must also preserve notifications emitted during `session/new`
before a first or replacement binding is publishable: a bounded exact-child pre-binding capture
holds them until the binding transaction commits and the reset epoch exists, then flushes them in
order. It may not drop them, guess their owner from session text, or block the SDK's response barrier
in a cycle.

ACP-04's concrete implementation plan is now COMPLETE at
`orchestration/tickets/acp-04-hermes-vertical-cutover/implementation-plan.md`. It maps the migration,
repository, internal owner adapters, hub/WebSocket, synchronous step gateway, one production
composition, three restrained mounts, and scripted vertical proof across eight phases; it reports no
frozen-contract blocker. One fresh sub-agent is performing the single focused plan review. The
orchestrator has already flagged four bounded corrections for that review: source identity must cover
valid as well as rejected ingress; the frozen attach action has no session-ID cursor; random UUIDs
belong to client-message IDs rather than a browser ID; and the plan must construct the existing
`RunResult` status shape exactly. No product source has changed for ACP-04 yet.

ACP-04's single focused plan review returned `NOT READY` with six bounded corrections and explicitly
approved the restrained three-mount UI boundary. The plan must route both valid and rejected ingress
through one immutable-source, bounded-enqueue sequencer without holding a transition lock across an
owner call that publishes back; initialize a reset epoch when a worker is the first demand; capture
worker permission provenance at admission; use the frozen attach/CAS/RunResult/strict-recovery
shapes; derive the real sibling `hermes` CLI rather than treating Python as the ACP command; shut
owners down while their publisher still drains; and preserve persistent protocol rejection across a
same-binding reset. The original plan author is amending exactly these findings. This is the one
review round; after orchestrator disposition, implementation starts without another broad review.

ACP-04 planning is now `READY`. The amended plan accepts all six findings and the orchestrator's
finding-by-finding spot-check confirms the required mechanics: immutable-source valid/rejected
ingress enters one bounded sequencer and owner calls close through barriers outside its drain;
worker-first demand establishes the reset/load/snapshot/ready epoch before prompt delivery; tracked
failure settles before collector teardown and any queued successor; permission origin is copied at
admission and worker selection validates exact durable binding/Ticket/turn state under the settling
transaction; every frozen/current type is used exactly; the sibling `hermes` CLI is the child
command; and publisher/writers stop last under one deadline. Persistent protocol rejection survives
same-binding reset and the check ledger names the real suites. Per the owner-requested review model,
there is no second broad plan review. ACP-04 implementation may start in the reviewed allowed scope.

ACP-04 implementation is now ACTIVE in the main worktree. Phase 1 has added the schema-24 durable
binding table, atomic Ticket/Chief mirror repository, migration/backfill tests, and the source-aware
registry plus tracked-turn/permission internal seams. The orchestrator caught and corrected two
early Phase-1 issues before review: valid non-Hermes bindings must survive restart for the later
backend tickets, and the new Ticket fixtures must use the complete current row shape. The extracted
Chief writer also retains its existing public event semantics. Phase 3's typed hub is under
construction. Current spot-checks have sent three load-bearing requirements back to the implementer:
an existing binding must load only once through the ready barrier; asynchronous sequencer failures
must retire the exact source rather than disappear; and protocol rejection/capture observation must
close through the reviewed barrier ordering before worker settlement or a queued successor. The
new binding tests pass; the hub tests are still work in progress, and neither independent diff review
nor canonical `./verify` has started.

ACP-04 Phases 1–5 are now behaviorally green. The settled current slice passes 71 binding/hub/
registry/broker/permission cases and 14 parameterized WebSocket/gateway cases with scoped Ruff and
strict Mypy. The hub corrections now prove one existing-binding load, reset→captured ingress→ready,
same-binding restart floor, generation-fatal sequencer publication failure, rejection settlement
before a queued successor, and active slow/replay-unavailable socket closure. The strict WebSocket
accepts only text and attach-first serial actions and treats an ordinary disconnect quietly. The
synchronous gateway rejects a missing required binding before spawn/load/reset, runs the existing
session callback on its caller thread before prompt, and validates worker permission against the
exact binding, Ticket, running worker turn, record, and prompt epoch. The implementer must still add
the remainder of the named gateway outcome/interrupt/timeout matrix before final reporting; the
current implementation now advances to the one production composition and legacy-startup cutover.

ACP-04 Phases 6–7 are now green in their owned scope. Production composes one conversation owner,
derives the lexical sibling `hermes` executable from the configured venv Python without resolving
the Python symlink first, injects only `AcpStepGateway` into the Employee runtime, exposes the one
conversation state handle, closes browser admission before runner stop, and shuts the hub publisher
last under the same absolute deadline. The composition/WebSocket/hub slice passes 15 tests with
scoped Ruff and strict Mypy. The three existing Chief/Ticket mounts now unconditionally instantiate
one non-visual ACP wrapper using current-origin `/api/conversation`, 500 ms reconnect, and
`crypto.randomUUID()` only for client message IDs. Same-binding reconnect reset, persistent protocol
rejection, explicit replay-unavailable recovery, and all mounts pass Svelte diagnostics plus the ACP
state/conformance/component/contract/production-mount suites. No shared CSS, tokens, app layout, or
generated dist changed. Phase 8's actual official-SDK vertical proof and the remaining named gateway
outcome/interrupt matrix are the only implementation work before the focused ACP-04 report/review.

Owner pace clarification: the measured change is substantial (about 13,175 production additions and
15,763 test/support/fixture lines before ACP-04 Phase 8), so the established process should continue
rather than be artificially compressed. The owner's standing bounded-review judgment still applies:
one focused review is normal and another occurs only for a concrete unresolved correction. ACP-04 is
about 80–85% complete; its remaining pre-dogfood work is the five named gateway outcome/race proofs,
one official-SDK vertical e2e file, focused checks/report, and one implementation review. ACP-05 starts
immediately after `READY`, without an intermediate canonical `./verify`. The active implementer reports
no blocker and is completing the gateway matrix before the Phase-8 vertical proof.

The gateway matrix is now green and the first compact official-SDK vertical test passes, but the one
implementation review correctly returned `NOT READY`. Its four concrete findings are: Phase 8 does
not yet prove all eight frozen e2e cases or the uvicorn/Vite boundary; captured old-session ingress can
be flushed into a replacement binding and kill the live child; active replay accepts a buffer equal to
browser capacity and then evicts the subscriber when global `ready` needs one more slot; and server
startup failure after composition but before the lifespan cleanup scope can leak the composition. The
original implementer is correcting exactly these findings. No second broad review is planned; the
same reviewer/finding ledger will receive a bounded correction disposition.

ACP-04 implementation and its bounded correction are now COMPLETE. The final proof-only blocker is
closed in the official-child e2e file itself: the two-browser generation-2 flow injects stale
old-session ingress and keeps the replacement live; the automatic runner is interrupted through the
exact synchronous gateway; protocol rejection and capture failure prove collector/queued-successor
ordering; and active replay proves both independent slow-consumer eviction and byte-overflow
fail-closed behavior. The settled focused gate passes Ruff, strict Mypy across 24 source files, 164
Python tests, all five ACP web suites, Svelte with zero diagnostics, and scoped diff check. The
implementation report and exact output ledger are current. No canonical `./verify` or production
frontend build was run; root now performs the promised source/evidence spot-check before ACP-05.

Root spot-check is COMPLETE: the corrected hub filters stale ACP session notifications before envelope
publication (including captured flush), active replay reserves the ready slot, startup-loop failure
closes the composition, the synchronous gateway targets the exact durable active turn, and the Phase-8
assertions hit the named two-browser, worker-first, rejection/capture, permission, slow/overflow, and
DB-context boundaries. Root independently reran `tests/e2e/test_acp_conversation.py`: 9 passed. The
full reviewer output and correction disposition are now persisted in the ACP-04 ticket directory.
ACP-04 is `READY`; ACP-05 real Hermes dogfood and computer-use validation is next.

ACP-05 is now running against the production-served Panels Workspace at `127.0.0.1:8767`; the
discarded Vite `5189` page was an unstyled dev build and is not evidence. Root rebuilt the current
frontend, gracefully replaced the orphaned live application process with `panels serve`, hard
reloaded the real two-column Workspace, and confirmed ACP reset/ready in the existing Panels visual
language. The first real Chief prompt exposed a focused cutover defect before any broader UX claim:
the backfilled binding names a Hermes `planner-chat` session, while the read-only Hermes ACP adapter
restores only source `acp`. Its `session/load` therefore returned JSON null; Panels incorrectly
published the binding ready, rendered human echo + `started`, and received no agent answer. A second
raw ACP attach proved reset -> ready with zero replay, and the Hermes state DB proves the source
mismatch. The correction ticket is cut at
`orchestration/tickets/acp-05-missing-session-cutover/contract.md`: null load is a positive
not-found transition to one CAS-persisted successor ACP session with visible status; raised load
still fails closed. Next: one sub-agent implementation, focused gates, one independent review, then
resume the full real-UI ACP-05 matrix on `8767`.

Owner then made the intended cutover boundary explicit: legacy `planner-chat` sessions do not need
runtime compatibility. The missing-session correction ticket is CANCELLED without implementation.
Using Computer Use on the real `127.0.0.1:8767/#/workspace`, root clicked the existing **New
conversation** control for Chief of Staff. Panels durably replaced generation 1 with Hermes ACP
session `2af393b0-6c8f-4eb1-99fc-c3783541c781` at generation 2; the next UI prompt received the exact
agent response `ACP dogfood ready.` in the existing dark split-pane interface. ACP-05 therefore
continues on this clean binding. Once dogfood passes, ACP-06 will deliberately cut the remaining
legacy bindings over to fresh ACP sessions while removing the old transport, instead of retaining a
permanent legacy-session recovery branch.

The first deeper real-UI queue check exposed a browser-only grouping defect: Hermes live agent chunks
without message IDs were merged across two settled prompts because the optimistic queued human echo
can precede the first prompt's final chunk. ACP-05's focused correction now closes only the active
missing-ID group on terminal activity or an interrupted Send Now receipt. Its state/component/Svelte/
temporary-build checks passed, and one independent implementation review returned `READY` with no
findings. The existing persistent delivery acknowledgement was reviewed and remains intentional.

The real-app Queue cancellation behavior is now independently green. Computer Use started a
20-second terminal turn, queued a second exact-response prompt, and clicked its visible **Cancel**
control while the first turn remained active. The queue item immediately settled as
`interrupted · Queued prompt cancelled`; the first turn later returned `LONG QUEUE TURN DONE.`, the
queue was empty, and the cancelled response never ran. The earlier short-turn attempt was only a
missed human click after its stale accessibility element expired, not a product failure.

Real **Stop** then exposed a separate broker classification defect: successful cancellation followed
by the cancelled prompt await raising was incorrectly surfaced as `Employee connection failed` and
closed the otherwise-live child. The focused correction at
`orchestration/tickets/acp-05-cancel-classification/` is now SETTLED: requested user/Send Now/new-
conversation/shutdown cancellation owns the later prompt exception only after exact cancel and
permission-cancel delivery succeed. Stop reuses the same child and a later prompt succeeds; Send Now
starts one successor; an ordinary exception still fails the generation. Broker 30/30, hub/gateway
10/10, official-child cancellation 3/3, Ruff, and Mypy passed. One independent review returned
`READY` with no findings.

The actual production-served Stop retest now confirms the classification half: the active prompt is
shown as `interrupted · Prompt interrupted`, activity returns to idle, the websocket remains
connected, and no false employee-failure status appears. Immediate reuse uncovered one narrower
Hermes integration race that the scripted child did not model. The follow-up prompt is accepted by
Panels, but the Hermes ACP adapter still considers the cancelled request running, emits its own
`Queued for the next turn. (1 queued)` message, and never drains that private queue. Upstream source
inspection explains the boundary: Hermes has an internal `state.is_running`/`queued_prompts` layer,
while ACP cancel is notification-only and the failed prompt RPC can unwind on the client before the
server-side prompt finalizer clears `is_running`. The current hypothesis is that an exception after
successful requested cancellation must recover to a freshly loaded child generation before Panels
publishes reusable idle; merely reclassifying it as interruption is not enough for the real Hermes
backend. Do not add a timing sleep or depend on Hermes's private queue. This becomes one bounded
post-compaction correction because its broker/runtime rekey surface overlaps the active durable-fork
implementation. The owner-approved one-time clean-session path remains available for continued
dogfood in the meantime.

The matching real **Send Now** retest confirms the same recovery ticket must cover successors, not
only later ordinary prompts. Panels correctly publishes the old receipt as interrupted and accepts
exactly one replacement, but the old Hermes terminal continues to completion and the old and new
agent text arrive concatenated (`OLD TURN SHOULD NOT COMPLETE.SEND NOW REPLACEMENT READY.`). Starting
the successor on the same child is therefore not safe after this requested-cancel exception. The
correction must quiesce/filter the old source, load a fresh generation on the same durable binding,
then start the one frozen successor; late old-generation ingress cannot enter the replacement turn.

Real Hermes **Steer** is independently green on a fresh binding: the active terminal turn accepted
one steer message, rendered Hermes's typed acknowledgement, ignored the original requested final
answer, and settled with the exact steered response `STEERED READY.` while keeping the connection
idle/usable. This path does not use ACP cancellation and is outside the recovery defect.

Real browser image attachment is also green through the complete production path. Computer Use chose
the owner-provided original Panels screenshot in the native file picker; the composer showed one
pending image, the transcript rendered the image block, Hermes vision received it, and returned the
requested exact five-word description: `Dark workspace dashboard displaying tasks.`

The real child-death, respawn, and deliberate-new-conversation boundaries are green. Root terminated
the exact active Chief Hermes ACP subprocess while idle. The UI immediately published the visible
`Employee connection failed` state without minting a session or changing binding generation 5. A
replacement subprocess reattached and replayed that exact durable session; an ordinary prompt then
returned `RESPAWN READY.`. A subsequent explicit **New conversation** cleared the transcript and
atomically advanced the live binding to generation 6 with fresh ACP session
`2dc5f884-fd9c-43ab-9c6b-ba6f90d21c26`. This proves crash/reconnect and deliberate replacement are
distinct transitions in the production-served UI.

The real Ticket chat mount is green as well. Computer Use selected
`t_12sap6vx` (rolling backup/operator restore), used the existing **New conversation** control to
replace its legacy binding with a fresh Hermes ACP session, and sent through the mounted Coding-worker
pane. The typed transcript settled idle with the exact response `TICKET CHAT READY.` while preserving
the Ticket editor and stage layout.

The real Automatic Employee-step route is now green end to end. Root created disposable Ticket
`t_b7sdhtzn`, accepted its constrained Kickoff, and added it to today's Ticket membership; no manual
worker-run command was issued. The discovery loop claimed it into `agent_running_step`, the actual
Ticket pane showed the active ACP worker turn with typed skill/tool activity, and the shared
`AcpStepGateway` settled it to `awaiting_approval`. The visible Success proposal is exactly
`ACP automatic step ready.` with recap `ACP automatic step Success is ready for approval.`. The
disposable row remains temporarily as dogfood evidence and will be removed during cutover cleanup.

The real edit/diff prompt created `data/acp-dogfood-diff.txt`, opened the expected blocking permission,
applied the approved beta→gamma patch, replayed terminal/patch/read as completed after hard reload,
and returned `DIFF READY.` The dogfood also found one disjoint frontend omission: Hermes supplied the
exact old/new `diff` block inside the permission request's `toolCall`, but `PermissionPrompt.svelte`
rendered only its title/options/status, so the approval showed no change. The bounded ticket at
`orchestration/tickets/acp-05-permission-diff/contract.md` reuses the existing `DiffView` for only
those supplied blocks and leaves no-diff permissions unchanged. A frontend sub-agent is implementing
it while the backend compaction ticket continues; scopes do not overlap. The implementation and
focused gates are now green. Its one review found that the first no-diff proof compared two
post-change states and missed an empty Svelte-loop anchor. The correction added an exact pre-change
serialized-DOM fixture, reproduced the anchor red, and moved conditional `DiffView` mounting out of
the template so a no-diff permission emits no new node. Component tests, Svelte diagnostics, and the
production build pass; the original reviewer confirmed that exact correction and returned `READY`.
The permission-diff ticket is settled pending production-served Computer Use validation after the
next bundle restart.

Explicit real Hermes `/compact` also found a durable-capture defect. The live child compressed its
in-memory history, but upstream Hermes did not persist that history before Panels retired the child;
fresh load replayed 17 old rows, found no structural summary, and visibly failed capture. Hermes stays
strictly read-only. The replacement contract is frozen at
`orchestration/tickets/acp-05-durable-compaction-fork/contract.md`: call advertised official ACP
`session/fork` on the exact compacted child, privately load/normalize that durable fork, CAS the
binding to generation N+1, transition all browsers reset/replay/ready, and retarget queued intent
before settlement. The planning sub-agent found no contract blocker but continued analysis after two
explicit stop-and-write requests, so root changed approach and completed the concrete seven-phase
plan from the settled findings. The one focused review approved every named seam and found one real
deadline blocker: the plan asserted a bound without assigning a creator or carrying it across the
whole multi-owner transition. The amendment now makes the broker actor create one absolute deadline
before hub begin and passes it unchanged through fork/load/normalize/CAS/recovery/rekey/hub settlement;
expiry releases every gate/waiter once and fails whichever generation cannot be proven. The review's
narrow check returned `READY`. Implementation is complete on the fresh-ACP-session cutover path: the
official child retains and invokes `session/fork`; the registry privately loads and normalizes that
same-child fork before its one N→N+1 durable CAS; the broker/hub admission barrier atomically rekeys
the actor, resets every browser, replays N+1, and retargets queued intent. Recoverable pre-CAS failure
restores N, while a post-CAS publication deadline invalidates the exact replacement runtime.

Focused Ruff and strict Mypy pass; 122 affected unit tests and all 12 official-SDK conversation e2e
tests pass, including explicit refresh/child-death/process-restart durability, automatic queue
succession, missing fork capability, and a held hub-commit deadline. The browser state/component
suites, Svelte diagnostics (0 errors/0 warnings), and a temporary production Vite build also pass
after the disjoint permission-diff owner settled its overlapping frontend correction. The
implementation report and exact focused output are written; root owns the one independent
durable-fork implementation review, whose result and corrections are recorded below. No canonical
`./verify` has run; ACP-10 still owns the one final gate.

That focused implementation review returned `NOT READY` with two deterministic P1 ownership gaps
despite the green suite. Both corrections are now complete. Hub commit and recoverable abort stage
their selected stream but keep admission closed until the broker publishes the normalized boundary,
settles the tracked turn, publishes idle, advances FIFO, and calls the bounded completion seam. An
explicit generation-fatal registry disposition now reaches the broker when exact runtime restoration
or adoption is impossible before a replacement transition exists; the actor, tracked turn, queued
intent, and hub transition fail without abort, idle, or FIFO advance. Deterministic hub, broker, and
real-registry regressions pass. The narrow correction check then found one remaining exception-
precedence path: an initial normalization error could hide a generation-fatal abort when exact N
restoration failed. `GenerationBoundBackendTurnStrategy` now lets that fatal abort disposition win,
and an integrated registry/broker regression proves actor, tracked turn, successor, FIFO, and hub
failure with no abort or idle. The full affected unit gate is green at 122 tests; correction-scoped
Ruff and strict Mypy are clean. The previously settled 12 official-SDK e2e tests remain unchanged.
The final narrow correction review returned `READY`. The still-running live server has the prior
loaded bundle/process code; it will be gracefully replaced only after requested-cancel recovery is
also settled, so the next production restart validates both corrections together.

The final Hermes behavior ticket is now frozen at
`orchestration/tickets/acp-05-requested-cancel-recovery/contract.md`. It applies only when user Stop
or Send Now has successfully delivered exact ACP and permission cancellation but the prompt RPC then
unwinds exceptionally. The broker freezes successors/FIFO; Panels retires and filters the exact
indeterminate child, privately reloads the unchanged durable binding through a fresh child generation,
transitions browsers with same-binding reset/replay/ready, and only then publishes reusable idle or
starts one retargeted successor. Normal cancelled responses, Steer, ordinary failures, durable binding
generation, public wire vocabulary, and visual design do not change. Source implementation waits for
the durable-fork review because the tickets share registry/broker/hub files.

Its first focused implementation-plan review found two P1 ordering gaps. The plan had installed old-
source suppression only after the exceptional prompt unwind, leaving a real interval for late text to
render, and it had not routed exact old-child death through the recovery owner. The amended contract
and plan now install one bounded exact-source quarantine before ACP cancel is sent; normal terminal
cancellation flushes held updates in order on generation N, while exceptional unwind reuses the token
and discards them. Matching planned-retirement death never enters ordinary death even on close error;
an unexpected quarantined-old death fails the hub transition and actor once. The official-SDK fixture
must audit a unique post-cancel send attempt, not merely assert that its text is absent. The reviewer is
checking only those two corrections before implementation dispatch. That narrow check confirmed both
resolved and returned `READY`; the overlapping durable-fork correction is now settled and source
implementation is active.

Requested-cancel implementation checkpoints 1–8 are now COMPLETE and ready for one independent diff
review. Stop and Send Now install exact-source quarantine before cancel; normal cancelled responses
resume generation N, while exceptional unwind replaces N with a privately loaded fresh child on the
unchanged durable session/binding before reset/replay/ready releases successor or FIFO intent. Close
paths retire the exact exceptional lease without entering recovery. Expiry, stale lease, planned-close
error, private-load/commit failure, unexpected old-child death, two-browser ordering, composition, and
the official-SDK late-send race all have focused proof. Ruff, strict Mypy, the complete named affected
Python unit/e2e gate, all four ACP browser suites, and Svelte diagnostics are green; exact output and
the implementation report are in the ticket directory. ACP-02's short cancellation-settlement timeout
remains the fail-fast path, while one longer actor deadline owns actual recovery. Canonical `./verify`
was not run; ACP-10 still owns it. Next: one independent implementation review, bounded correction only
for concrete findings, then integration/dogfood.

That one independent review returned `NOT READY` with two connected fail-closed defects and one proof
gap. The requested-cancel timeout path created a second budget and could leave indeterminate generation
N reusable if retirement resisted; exceptional New Conversation/shutdown retirement failure could be
swallowed before the close owner reused that same old record. Those corrections are now COMPLETE.
Timeout retirement reuses the actor's original absolute deadline, including employee-gate acquisition
and planned-death settlement; inability to prove exact retirement invalidates the record and detaches
child shutdown. Exceptional New Conversation/shutdown retirement error or timeout now invalidates the
exact lease and propagates to the close owner, so replacement cannot proceed on N. The expanded matrix
proves admitted-ingress quiescence, fresh initialize/death, durable-binding drift, hard deadlines,
reverse cleanup, invalid replacement, hub commit, and unexpected old-child death. The official-SDK
test now requires reset/ready/queue before successor start, one exact successor answer, and empty real
worker collectors on both quarantined old generations. Final focused Ruff, strict Mypy, 119 affected
Python tests, all four ACP browser suites, and Svelte diagnostics pass. Evidence and finding-by-finding
dispositions are in the ticket directory. No second broad review round or canonical `./verify` ran.
The requested narrow finding check returned `READY`; no reviewed item remains unresolved.

Owner clarification for ACP-07/08 is now explicit in the program and `decisions.md`: “backend
definition” means a functional Panels worker backend. Codex and Claude must each be assignable through
one supported runtime seam and complete both a real Ticket conversation and an actual Automatic
Employee step. A definition file or test-only swap is insufficient; no backend-specific UI is added
unless a generic selector proves necessary.

The owner then selected that generic seam: each Worker type declares a default employee backend; new
Tickets copy it as the already-selected Kickoff choice; Kickoff may override it before approval; and
the accepted Ticket value drives both chat and Automatic Employee work. Codex and Claude Code must each
prove a real disposable Ticket through that path. Gemini is removed from the delivery scope entirely;
ACP-09 now has no work.

The generic selection ticket is now cut at
`orchestration/tickets/acp-07-worker-backend-selection/contract.md`. It freezes an explicit
`default_employee_backend` on each Worker type and stored `employee_backend` on each Ticket, one
registered-definition catalog, a compact preselected Kickoff control editable only before any worker
status/session/binding exists, and exact reuse by both human Ticket chat and automatic steps. Existing
rows migrate explicitly to Hermes; the binding repository fails closed on selection/binding mismatch.
A disjoint sub-agent is writing its exact implementation plan while ACP-05 cancellation recovery owns
the shared conversation-runtime source. Separately, a read-only ACP-06 legacy-chat ownership inventory
is running now; deletion and the authoritative classification still wait for Hermes dogfood to pass.

Source tracing found and corrected one contract-level mismatch before the selection plan was written:
fresh Ticket creation already enters `awaiting_approval` with its Kickoff proposal, so an `empty`-only
writer could never serve the visible Kickoff selector. The exact pristine boundary is now
`needs_kickoff` plus either `awaiting_approval` or `empty`, with no employee session and no ACP binding;
all later states remain frozen. Existing Ticket status semantics do not change.

The ACP-07 implementation plan's one focused review then found two real pre-source defects. First,
`TicketRoute` currently attaches on observation, so merely opening the fresh Ticket would create the
binding and make its Kickoff selector unusable. The corrected plan keeps the same rail/composer but
defers transport attach only while Kickoff is pristine; first prompt is retained and delivered exactly
once after ready, while Kickoff advance enables ordinary attach. Second, the app catalog and the
module-configured Worker registry could diverge. One immutable configured pair now supplies both to
every app, Ticket, binding, discovery, and automatic-work consumer, with atomic test install/restore.
The review's remaining migration/writer/CAS/restraint/integration boundaries were sound; the corrected
plan is `READY` after ACP-06 and needs no second broad plan review.

The ACP-06 read-only inventory is provisionally complete at
`orchestration/acp-migration/chat-ownership-inventory-working.md`. It classifies the legacy chat,
gateway, relay, file, schema, event, API, frontend, and test surfaces for deletion/rehome. One hidden
legacy responsibility was found before deletion: `SharedGateway` prepares `pending_worker_context`
into the actual Automatic Employee prompt and acknowledges it after admission, while `AcpStepGateway`
did not. The bounded ACP-05 worker-context ticket is plan-reviewed and ready once the cancellation
correction releases its overlapping composition/e2e files. It changes only gateway delivery/ack;
eligibility, claim, prompt construction, worker lifecycle, and settlement remain unchanged.

The ACP-06 inventory's one independent review found six concrete ownership gaps: pending context was
misnamed as settlement, the non-chat Employee-step record was not frozen, legacy session-identity
fallbacks and the Ticket mirror were incomplete, the Chief cutover still had an unresolved choice,
and shared CSS/test-runner plus mixed-test/docs cleanup were absent. All six are now corrected in the
authoritative conditional `orchestration/acp-migration/chat-ownership-classification.md`. It freezes
`runtime/employee_step_repository.py`/`employee_step_runs`, distinct worker-context delivery ownership,
`PLAN_TICKET_ID` worker-self identity, one global old-session reset, exact frontend/test/doc cleanup,
and no compatibility fallbacks. The review disposition is `READY AS A CONDITIONAL CLASSIFICATION`;
ACP-05 Computer Use remains the deletion-authorization gate.

With requested-cancel recovery settled, the plan-reviewed ACP-05 worker-context delivery ticket is
now implementing in its five exact gateway/composition/test files. This is the final pre-restart
source slice: caller-thread prepare, exact ACP model text, post-admission off-loop acknowledgement,
failure retention, and the real automatic-step prompt proof. It does not change discovery, claiming,
runner lifecycle, proposal/status settlement, or worker-context schema/producers.

That final pre-restart slice is now COMPLETE and `READY`. `AcpStepGateway` owns exact
guard→prepare→schedule ordering, submits only `PreparedWorkerPrompt.model_text`, and acknowledges
non-empty exact receipts off-loop only after tracked admission; every pre-admission failure retains
them, while acknowledgement failure is logged without altering the admitted result. Production and
test composition inject `SqliteWorkerContextService`. One focused review found no behavioral defect;
its naming correction landed and optional structural churn was declined. Post-review Ruff, strict
Mypy, and 49 focused unit/composition/worker-context/official-SDK e2e tests pass. The next action is
the production bundle build and real `8767` restart for the remaining Hermes Computer Use matrix.

Browser reload also reproduced asyncio's destroyed-pending-task warning. Root traced it to external
writer cancellation interrupting `asyncio.wait` before the writer cleaned up its per-iteration
`Queue.get()` and `Event.wait()`. The tiny direct repair now owns both waits through `finally`; its
red/green regression, full hub/websocket 14/14, Ruff, and strict Mypy passed. A separate independent
review returned `READY` with no findings. No protocol, UI, or lifecycle design changed.

The rebuilt production bundle is now running at the real `127.0.0.1:8767` Panels surface with the
original visual design intact. Real Hermes Computer Use re-proved permission delivery end to end: the
pending edit card visibly rendered the exact ACP diff block and both choices, a prompt approval changed
`data/acp-dogfood-diff.txt` from `gamma` to `delta`, and Hermes replied exactly
`PERMISSION DIFF READY`. Hermes supplies its patch envelope as the diff block's `newText` for the patch
tool, so Panels correctly shows that literal backend payload; this is not a second Panels diff parser.

Real Stop recovery also passed after the fresh restart. A `sleep 20` tool turn was stopped, replay
showed the tool failed plus `Operation interrupted.`, the old unique completion marker never appeared,
and one immediate follow-up replied exactly `STOP RECOVERY READY.` The durable Chief binding remains
the same Hermes session `2dc5f884-fd9c-43ab-9c6b-ba6f90d21c26` at binding generation 6, proving the
fresh-child recovery did not manufacture a conversation or binding.

The Send Now presentation-boundary correction is implemented and focused-green at
`orchestration/tickets/acp-05-send-now-human-boundary/`. Requested-cancel recovery now mirrors the
existing compaction publication order: reset, durable replay, ready, retained FIFO human echoes, the
optional Send Now successor echo, then the FIFO-only queue snapshot. The successor begins only after
that atomic commit. The exact production-shaped browser regression projects old agent, retained FIFO
user, successor user, and successor agent messages separately even when both agent chunks omit IDs.
Ruff, strict Mypy, 74 focused Python tests, four ACP browser suites, and Svelte diagnostics pass. One
focused implementation review and real Hermes Send Now retest remain before this half is settled.

One remaining diagnosed production failure still keeps ACP-05's Computer Use gate closed. Explicit
`/compact` visibly entered `compacting` and Hermes returned
`Context compressed: 30 -> 13 messages ~25,137 -> ~32,873 tokens`, but Panels then published
`Context failed · explicit` with `Conversation runtime generation failed during compaction`; the durable
binding remained generation 6. ACP-06 deletion remains unauthorized until compaction is corrected and
the Send Now plus hard-reload/server-restart compaction Computer Use proofs pass.

The compaction deadline correction and its focused-review P1 are now implementation-complete and
focused-green pending owner spot-check and real Hermes retest. Compaction has a dedicated five-minute
emergency deadlock breaker while shutdown remains 10 seconds; a compaction taking a couple of minutes
is normal and is not itself a failure signal. The actor still creates one absolute
deadline; exact phase, configured budget, and stayed-N/committed-N+1/unresolved durable-binding
disposition now reach the visible failed boundary and server log. Expiry invalidates an uncertain
child without creating a second recovery budget. Immediate backend/protocol failures now retain their
exact phase, exception type/message, and both primary plus required restore/abort causes where
applicable; no generic capture or generation text replaces a concrete error. CAS settlement is not
followed by a false stale abort. A narrow internal timer-won exception now separates actual Panels
deadline expiry from a backend/protocol/persistence operation's own immediate `TimeoutError`; only the
former receives five-minute budget wording, while the latter exposes its phase and concrete cause.
A deterministic clock advances 16 seconds across fork, private replay, CAS,
and browser commit—past the old shutdown budget—and still commits N→N+1 under the same five-minute
deadline. Scoped Ruff and strict Mypy pass, and all 134 named registry/broker/hub/composition/
Hermes-strategy/official-SDK e2e tests pass. Canonical `./verify` and `web/dist` rebuild were not run.

The final narrow correction review is now `READY`: only Panels' typed timer-won signal becomes the
five-minute breaker expiry; a backend-raised `TimeoutError` and every other concrete backend/protocol/
persistence failure surface immediately with exact phase, type, message, and required recovery cause.
The production server remains healthy on the real `127.0.0.1:8767` Panels surface, but Computer Use
cannot yet run the Send Now/compaction/reload/restart proof because the Mac is still locked. This is an
external interaction gate, not an ACP failure; no additional timeout mechanism is being added.

Backend qualification planning continued only where it did not depend on that interaction. The Codex
plan's focused source review is `READY` as a qualification-gated plan, but the locally available
`@agentclientprotocol/codex-acp@1.1.4` is not eligible for production registration: it lacks the Panels
capture-required ACP fork behavior, compaction-summary replay, and typed stored-plan replay, and the
installed Codex CLI exposes app-server rather than an ACP command. Claude's first review rejected a
false reverse-terminal blocker and requested three bounded plan corrections. Those corrections are
now incorporated and the post-amendment plan is `READY`: Claude truthfully declares
filesystem/terminal false and permission true, runs one initialize-only pre-advertisement capability
preflight, and owns one exact-generation compaction observation state machine. Neither backend plan
authorizes source work before the ACP-05/ACP-06 gates.

The latest-official Codex qualification is now authoritative for 2026-07-20. Both npm `latest` and
the official repository release remain `@agentclientprotocol/codex-acp@1.1.4`; exact tarball/source
inspection reconfirms no ACP fork handler, no inspectable compaction summary, and stored-plan replay
flattened to assistant text. Load ordering and ordinary permission/cancel source look compatible but
cannot overcome those hard contract failures. `codex` therefore stays absent from registration and
selection; no private session parser or second transport is authorized.

ACP-10 is now cut and independently plan-reviewed `READY` at
`orchestration/tickets/acp-10-final-audit-verification/`. It requires a requirement-by-requirement
ledger, real Chrome Computer Use only on `127.0.0.1:8767`, exact backend qualification truth, complete
legacy-absence and live-doc evidence, one focused settled-tree review, and one frozen no-writer
canonical `./verify` with retained full log, input manifest, exit status, and SHA-256 digest. It adds
no feature or compatibility work.

The Mac was unlocked and the goal resumed directly at real Panels. `/new` established fresh Chief
Hermes session `735aea7e-f76d-49bf-a4b9-0576846a5489` at binding generation 10. Real Send Now passed:
the `sleep 60` tool failed, the old turn showed `Operation interrupted.`, the separate successor user
message was accepted, Hermes answered exactly `SEND NOW RECOVERY READY.`, the forbidden old marker
never appeared, and the durable binding stayed byte-identical.

Explicit `/compact` then exposed the real remaining defect rather than a 60-second false timeout.
Hermes completed `Context compressed: 8 -> 7 messages ~20,399 -> ~30,696 tokens`; after the full
300-second emergency breaker Panels failed exactly during `private fork load`, retained generation 10,
invalidated the uncertain child, and reported that exact-original restore also exhausted the same
deadline. The persisted seven-message candidate is
`7667eb9a-63fc-4ff4-a15c-3dc4ea9e9d76`; loading a candidate in a fresh child succeeds in about 1.6s.

A deterministic no-model 2.5-second reproduction now isolates the fault: after a previously loaded
source, Hermes returns `session/fork`, then emits candidate `available_commands_update`, and only then
Panels sends `session/load`. `capture_load_session` has already opened its private epoch, whose request
id is still unset, so ordered ingress raises `AcpSessionUpdateCallbackMismatch: private ACP capture
received an update before its load request` and closes the connection. Waiting for prior load metadata
does not help; the update is a post-fork candidate update. Fresh-child candidate load and a fresh
fork-without-prior-source-load control both pass. Two read-only web/source research lanes are checking
whether Panels' same-child fork/load capture is architecturally wrong or should route that candidate
update by exact session id. ACP-06 remains gated until the corrected sequence passes real compaction,
hard reload, and server restart.

Both primary-source research lanes are now complete at
`orchestration/tickets/acp-05-compaction-private-load-hang/`. They prove two independent Panels defects,
not a Hermes hang: the private epoch incorrectly treats a valid pre-load-observer candidate update as
fatal, and the registry holds its publication/update gate across ACP I/O so an older public FIFO slot can
self-deadlock the private barrier. Raw official-SDK and exact-child controls complete fork/load/restore in
under three seconds. ACP guarantees load replay before the load response but gives no quiet barrier
around the still-draft fork operation; Hermes intentionally schedules candidate metadata after its fork
response.

The correction contract is now frozen and the obsolete durable-fork contract is explicitly marked
superseded where it claimed same-child load/publication. Private capture becomes exact-session-aware;
lifecycle exclusion separates from the short publication gate; the source child forks, a fresh
unpublished child loads and validates the fork as a cross-process durability proof, durable CAS publishes
that child as a new runtime generation, and exact-source pre-publication updates drain in wire order into
the N+1 browser transition. The 300-second emergency breaker remains unchanged. An implementation plan
is now complete at `orchestration/tickets/acp-05-compaction-private-load-hang/implementation-plan.md`.
Two planning subagents were stopped after they continued inspecting without producing the bounded
deliverable; root finalized the plan directly from the completed research and live code audit rather
than waiting further. The one focused plan review found one concrete blocker: the hub's general
pre-binding capture drops non-current sources while N is ready, so it could not own candidate updates or
clear them on loser paths. The blocker is accepted and resolved in the contract/plan with a dedicated,
aggregate-bounded compaction-transition FIFO that retains exact source/session identity, admits both
legal candidate origins, publishes only winning N+1 entries once between replay and ready, and clears on
every failure/loser/expiry/shutdown path. The other reviewed invariants were ready; no second review
round is needed. No product source or tests have changed for this correction yet. ACP-06 remains
hard-gated on the real compact/reload/restart proof.

Implementation is now dispatched in two non-overlapping shared-worktree lanes. The ingress lane owns
exact-session private routing plus the official-SDK post-fork ordering fixture/test. The registry/hub
lane owns the lifecycle/publication lock split, fresh unpublished candidate child, CAS/source retirement,
generation-fatal pre-CAS failure semantics, and transition-owned notification FIFO. Both start with the
named deterministic red tests and run only focused checks; canonical `./verify`, generated distribution,
server restart, and Computer Use remain root-owned after integration.

Both correction lanes are implementation-complete and root has spot-checked the load-bearing path. A
private response epoch carries the exact expected session ID; matching updates route privately even
before the outgoing load observer, valid other-session traffic remains ordinary, and request ID only
freezes the response's already-observed prefix. Compaction now keeps lifecycle mutation separate from
short publication quiescence: the source child only forks, a reserved fresh unpublished child privately
loads the fork as the cross-process durability proof, repository CAS/resolve happens outside the
publication gate, and an exact N+1 child generation/identity is published before the source retires. The
hub's aggregate-bounded transition FIFO owns both legal candidate origins and drains them once between
private replay and ready while exact ordinary N remains before reset; every failure and settlement path
clears it.

Focused evidence is green: the ingress/SDK lane passes 39 tests, the registry/hub lane passes 115 owned
tests, the ACP e2e suite passes 14 tests, and scoped Ruff plus strict Mypy pass. The one independent
implementation review found two P1 failure-path gaps and both are now resolved in one bounded correction:
missing fork capability and every post-validation preparation failure retire exact source N, while all
compaction publication-gate waits use the original deadline and fail-closed settlement never re-waits on
a blocked gate. Five held-gate regressions cover initial validation, final preparation, normal candidate
publication, external-winner publication, and after-deadline abort. The corrected registry suite passes
52 tests with scoped Ruff and strict Mypy clean. No second review round is needed; root spot-check agrees
with the written resolution.

No canonical `./verify` has been run. Root is now restarting the real `127.0.0.1:8767` server for the
Computer Use proof of explicit compaction N -> N+1, expandable summary, hard reload, server restart with
the same durable binding, and a later prompt. ACP-06 remains gated only on that live proof and is already
fully planned and independently plan-reviewed `READY` for two disjoint runtime/schema and frontend lanes.

Real Panels then proved the corrected backend transition: explicit `/compact` committed Chief binding
generation 10 -> 11 and the expandable typed completion appeared. Hard reload and server restart exposed
one narrower publication defect: the live transition showed both Hermes's raw summary message and the
typed boundary, while reload reconstructed only the raw summary. Direct post-restart websocket attach
proved the generation-11 binding and all seven Hermes messages were durable, so this is replay
normalization rather than compaction, persistence, or reconnect failure.

The ACP-05 replay correction is now contract- and plan-settled. Boundary ID/trigger provenance (never
summary text) is stored atomically with the successor binding as a no-version-bump amendment to the
still-unlanded v24 schema. One hub batch helper is the sole classifier for every controlled load; the
Hermes strategy reuses its existing pinned summary parser and replaces the raw item at its exact replay
position with the persisted typed boundary or boundaries. The broker does not publish a second successful
completion. A later compaction stores only that capture's boundaries because the newer summary has
compacted the earlier transcript; replaying an older ID with the newer summary would be false. The one
focused plan review's v24/v25 and single-classifier blockers were accepted; its historical-provenance
append request was refuted on that semantic ground. Two disjoint TDD lanes are now implementing durable
provenance and replay behavior. ACP-06 remains gated on the corrected real compact/reload/restart/later-
prompt proof, and canonical `./verify` remains deferred.

Both replay-correction implementation lanes are now complete and root-spot-checked. The v24 binding row
owns strict ordered boundary provenance and an exact atomic compaction CAS; ordinary replacement clears
it, a loser returns the durable winner without overwrite, and ACP-06's terminal v25 migration is already
contracted to preserve/create the column before its one-time row deletion. Hermes classifies each complete
replay batch once through one exact private-context marker recognizer. One marker is suppressed in place;
a valid marker-free replay remains ordinary and receives the durable completion boundary after it. The hub
uses that one batch helper for ordinary attach/restart, compaction transition, and requested-cancel recovery,
while the broker suppresses a duplicate successful boundary.

The last stale e2e exposed one real lifecycle edge rather than a compaction regression: after an exact
source was retired for missing `session/fork`, the broker published terminal `activity:failed` but the hub
still considered its detached browser stream ready. Terminal failed activity now publishes exactly one
connection error and marks that stream not ready; repeated failure cannot duplicate the error. The corrected
e2e proves the failed boundary, unchanged durable generation 1, fresh-child reload of that same binding,
and a later usable prompt. Final focused evidence is 26 persistence tests, 146 behavior units, and all 14
ACP e2e tests passing, with scoped Ruff, strict Mypy, and diff-check clean. The one independent combined
implementation review found three bounded gaps: ordinary-sqlite transaction nesting, three stale v23
assertions, and the missing second-compaction proof. All three are corrected; the database/binding set is
87/87 and the ACP e2e set is 14/14. No second broad review was used.

Real Panels on a clean Chief generation then exposed one narrower owner-visible failure. Hermes reported
a successful `Context compressed: 2 -> 2 messages`, but Panels emitted `Context failed` because the fresh
fork replay contained the two retained ordinary messages and no displayable summary marker. Durable Hermes
state and source inspection make this deterministic: short history is a legitimate no-op compression, and
Hermes still reports success. The exact live-shaped unit regression is red 3/3 on the old behavior.

The owner has simplified and superseded the product contract: compaction is an opaque lifecycle. Panels
shows started, finished, or exact failure and never exposes or offers backend context. Backend success plus
durable session continuity is authoritative; summary presence is irrelevant. One recognized private marker
is suppressed if present, while marker-free replay stays intact and receives the content-free completion
boundary after it. The Hermes fork remains only because current same-ID persistence can lose the in-memory
compacted history, not for summary extraction and not as a generic backend requirement. The frozen bounded
plan is `lifecycle-only-correction-plan.md`; disjoint Python/public-contract and browser lanes are now
complete. Public `ContextCompaction` has no summary field; the browser row is plain and non-interactive.
The backend aggregate passes 308 focused tests, the exact strategy file 27, and the marker-free official-
child e2e; Ruff and strict Mypy are clean. All five integrated browser suites pass against the regenerated
fixtures, with zero Svelte diagnostics and a clean production build.

Actual Panels completed the gate. Explicit Chief compaction visibly entered `Context compacting`, durably
advanced binding generation 12 -> 13, then hard reload and a full server restart reconstructed the original
two messages followed by exactly one plain `Context compacted · explicit` boundary and no private context.
The same replacement session then answered `Reply exactly ACP REPLAY CONTINUES 20260720.` with the exact
response. ACP-05 is COMPLETE and authorizes ACP-06's already-plan-reviewed legacy deletion. Canonical
`./verify` remains correctly deferred to ACP-10; next is dispatching ACP-06's disjoint runtime/schema and
frontend deletion lanes.

ACP-06 is now active in those two disjoint lanes. The frontend deletion lane is complete: the ACP
composer directly owns commands and ordered inline image blocks; the legacy Chat/neutral UI, APIs,
resources, types, chat-file preview, tests, and classified CSS are gone. All 13 web test groups pass,
Svelte reports zero diagnostics, a temporary production build passes, and closure scans are clean.
The runtime/schema lane has added the exact eight-field `employee_step_runs` owner, cut automatic-work
correctness away from transcript state, rehomed the live conversation configuration/contracts, and
proved a fresh v0 -> v25 schema with clean foreign keys. It is completing the old-v24 atomic migration,
rollback proof, remaining Python caller closure, and focused Ruff/Mypy/pytest gates.

A parallel primary-source audit is complete at
`orchestration/acp-migration/compaction-primary-source-audit.md`. Stable ACP has no compaction method or
summary contract. Codex exposes asynchronous namespaced lifecycle metadata on the same thread; Claude's
ACP adapter exposes exact start/completed/failed controls on the same session; neither requires fork or
client-visible context. The Codex/Claude contracts and plans now use content-free lifecycle completion,
exact failures, and one honest 300-second Panels emergency breaker. Hermes's fork/rebind remains only its
proven persistence workaround. Codex 1.1.4's sole remaining qualification question is typed live/load
plan parity, not compaction. The owner-required functional-worker outcome makes that exact stored-plan
replay difference a tested upstream presentation limitation rather than a registration blocker: Codex
1.1.4 may proceed to real runtime qualification after ACP-06 and the selector, without a prose parser
or private-state shim.

The no-model Codex runtime prequalification is complete at
`orchestration/tickets/acp-07-codex-backend-definition/runtime-prequalification.md`: a disposable
locked install resolves adapter 1.1.4, ACP SDK 1.2.1, Codex 0.144.6 and Node 22.22.3; exact initialize
passes; unauthenticated list/new/load fails closed at `-32000 Authentication required`; and pinned
fixtures/source prove same-thread tagged compaction lifecycle. A final shell status command in that
research lane accidentally executed `./verify` through backtick command substitution. It ran against
the intentionally unfinished concurrent ACP-06 tree and failed on half-converted Python imports/Ruff;
frontend gates passed. The output is not evidence and does not replace ACP-10's one settled canonical
run. Observed generated side effects are the current `web/dist` build plus gitignored verify XML; root
will replace the bundle at planned integration and did not revert concurrent work.

Both ACP-06 implementation lanes are now COMPLETE and integrated in the shared tree. Runtime/schema
replaces legacy Chat correctness with the exact eight-field `employee_step_runs` repository, performs
the one-transaction v25 convert/reset/drop/rollback cutover, routes eligibility/recovery/Ticket guards
through that sole owner, preserves actual ACP worker-context delivery, and removes the old Chat,
Minds, relay, adapter, session-history, day-chat, and managed-chat-file owners. The frontend directly
uses the ACP pane/composer and inline image blocks and removes every legacy Chat/neutral API, resource,
type, preview, component, and classified CSS path. The settled lane evidence is the complete unit
suite passing, current e2e 99/99 passing, Ruff clean across product/unit/e2e, strict Mypy clean across
122 source files, all 13 web test groups passing, zero Svelte diagnostics, a clean temporary build,
and closure scans clean except the intentional legacy vocabulary inside the v25 migration. Root's
load-bearing migration/ownership spot-check found no defect; one direct glue correction changed the
restart prompt from Hermes-specific wording to the generic existing employee conversation. Live docs
are updating against this settled tree, followed by one combined focused review and real-app cutover
spot-check. Canonical `./verify` remains reserved for ACP-10 despite the separately recorded accidental
unfinished-tree invocation.

ACP-06 is COMPLETE. Its single integrated review found four concrete P1s: controlled shutdown made
the still-running Ticket's Employee-step non-resumable; the v25 fixture and rollback proof omitted
several destructive-input shapes; v25 accepted only “JSON array” rather than the binding repository's
exact compaction-boundary provenance shape; and the ACP composer cleared text and images when prompt
admission returned `ok: false`. All four were corrected in the same bounded round. The backend gate
passed 24 focused tests plus Ruff and strict Mypy; the frontend component/image/mount gates, Svelte
diagnostics, production build, diff check, and legacy-bundle scan passed. The written review verdict
is `READY`, with no unresolved P0/P1 and no second broad review.

The authorized live cutover also passed. The old `127.0.0.1:8767` server was stopped and the real
`data/planning.db` migrated atomically from v24 to v25. It preserved 451 terminal worker-step records
(410 complete, 14 errored, 27 interrupted), removed every legacy Chat table and the Day chat column,
cleared old bindings/session mirrors, left no running Ticket/Employee-step, and passed foreign-key
inspection. Actual Computer Use against the restarted Panels app showed the intended restrained
Panels layout, not the Vite fallback. A fresh generation-1 Chief session answered the exact prompt
`ACP06 LIVE CUTOVER READY 20260720.`, retained both sides after hard reload, and a real Ticket pane
created a fresh generation-1 ACP binding whose Ticket session mirror matched exactly. The live server
remains running. Canonical `./verify` remains reserved for ACP-10.

Next: implement the generic Worker-type default and per-Ticket Kickoff backend selector, then register
and dogfood functional Codex and Claude Code worker backends against that seam. Gemini remains out.

The generic selector implementation is now active with no contract blocker. In parallel, both backend
runtime qualifications are COMPLETE without model prompts or global auth/config mutation. Codex remains
READY on adapter 1.1.4, ACP SDK 1.2.1, locked Codex 0.144.6 and its matching native package. Claude's
current official adapter is 0.60.0 rather than the planned 0.59.0; the contract/plan now pin ACP SDK
1.2.1, Claude Agent SDK 0.3.215, and embedded Claude Code 2.1.215. Exact initialize and no-prompt
new/load behavior pass, including visible pre-session auth/resource failures. One combined exact
`agent_backends` manifest installs cleanly with a single deduplicated ACP SDK 1.2.1 and Zod 4.4.3.
The manifest/lock is integration-owned and will change serially after the selector lands; the two
backend definition lanes may then implement disjoint modules/tests in parallel before serial catalog
registration and real authenticated dogfood.

The generic Worker-backend selector is now COMPLETE. Schema v26 stores a required backend on every
Ticket, each Worker type supplies its registered default, and pristine Kickoff exposes the restrained
preselected Worker pill without creating a session merely by observation. First human demand or
Kickoff advance attaches once and freezes the choice. Binding CAS, permission settlement, Ticket chat,
and Automatic Employee work all use that same stored backend and durable session. The settled gates
pass Ruff, strict Mypy across 44 source files, 356 focused Python tests, three real-route selector/
runtime e2e tests, two CLI e2e tests, all focused frontend gates, zero Svelte diagnostics, and a
temporary production build. The single independent implementation review returned `READY` with no
findings; one apparent duplicate rollback was conclusively a duplicated read-output boundary and no
fake correction was made. Canonical `./verify` remains reserved for ACP-10.

Next: root creates the one exact combined `agent_backends` manifest/lock serially, then Codex and
Claude implement disjoint backend modules/tests in parallel. Production catalog registration and real
authenticated Ticket/Automatic-Employee dogfood remain serial integration steps.

That serial package gate is now complete. `agent_backends/package.json` and its generated lock install
with `npm ci` on Node 22.22.3: Claude ACP 0.60.0 and Codex ACP 1.1.4 deduplicate to ACP SDK 1.2.1 and
Zod 4.4.3, with Codex 0.144.6. The two provider-specific module/test lanes are active in disjoint
files; a read-only lane is mapping the smallest later catalog/preflight integration. No provider is
registered or served yet, and no model prompt or canonical verification has run.

The generic same-session compaction seam is now COMPLETE. After exact runtime-lease acquisition,
Codex or Claude may expose one optional in-place capture hook; the generation-bound wrapper owns the
existing absolute 300-second breaker, distinguishes its own timer from a concrete backend
`TimeoutError`, accepts only terminal content-free lifecycle results, propagates cancellation, and
never enters Hermes fork/private-load/CAS/rebind. Nine new deterministic proofs plus all 27 unchanged
Hermes strategy tests pass with Ruff and strict Mypy. Root also mechanically updated the five stale
selector-era registry test fixtures to the paired catalog/materialization constructor; both complete
affected files pass 61 tests and Ruff.

The shared provider activation contract and implementation plan are frozen under
`orchestration/tickets/acp-07-08-backend-activation/`. Its read-only mapping found no additional
runtime rewrite: the existing N-backend registry, binding, permission, Ticket chat, and Automatic
Employee paths already fit. One serial integration lane now owns only build-context repository root,
optional ordered startup preflights, production lifespan ordering/failure cleanup, and the exact
`hermes, codex, claude` catalog. Claude's provider-local slice is green on 14 focused tests and is
waiting only for that shared preflight field; Codex resumed against the settled in-place hook. No
provider is registered or served yet, and live v25 remains untouched.

Codex, Claude, and shared activation source are now COMPLETE before independent review. The sole
production catalog is ordered `hermes, codex, claude`; Chief and all shipped Worker defaults remain
Hermes. Codex is lazy and its exact locked `--version` probe sends no session/model work. Claude is
the sole startup preflight and now proves cancellation-safe spawn/initialize/close/force-close under
one absolute deadline before application admission. Provider gates pass 27 Codex and 33 Claude/
generic tests with Ruff and strict Mypy; shared catalog/composition/lifespan acceptance passes 37,
and its pre-correction combined provider gate passed 51. No authenticated model prompt has run.

Pinned Codex source exposed one final bounded broker edge before review: `/compact` rejection is an
ACP prompt exception, not a tagged failed tool update. The provider strategy now exposes a concrete
display-safe `TypeName: message` only for exact-session `/compact` or an active exact-binding automatic
compaction and has removed the invented failed-tag shape. The completed generic broker seam consults
that optional reason only for an uncancelled prompt exception with a pending compaction boundary,
using the provider strategy frozen into the exact acquired runtime lease; invalid or absent reasons
fall back to the existing generic failure and ordinary prompt failures remain private. The focused
broker/provider/compaction set passes 100 tests.

Root's settled current-tree integration gate passes 193 focused tests across provider activation,
catalog/lifespan, Worker selection, Codex, Claude, the generic in-place seam, the turn and permission
brokers, and the unchanged Hermes provider/strategy; scoped Ruff, strict Mypy, and diff check are all
green. The one independent combined implementation review returned `READY` with no P0/P1 finding;
it specifically confirmed the exact locked definitions, lazy Codex and initialize-only Claude
startup behavior, cancellation/cleanup, same-session provider compaction, exact prompt-failure
privacy, sole ordered catalog, pre-admission lifecycle, and shared human/Automatic-Employee binding.

The real schema-v26 activation and authenticated backend dogfood are now COMPLETE. Startup migrated
the live DB cleanly and admitted the application only after Claude's initialize-only preflight. In
the actual production-served Panels UI, fresh Codex Ticket `t_nkq3108b` and Claude Ticket
`t_1xbdpkq0` each proved first human demand, a real backend reply, naturally discovered Automatic
Employee work, a worker-authored Success proposal, and a later human reply through one unchanged
generation-1 ACP binding. The stored Automatic Employee run session IDs exactly equal their Ticket
conversation session IDs. Typed thought/tool activity stayed distinct in the transcript; Codex also
surfaced real permission choices. Both backends compacted on the same session with visible,
content-free lifecycle, and hard refresh preserved the typed transcript.

Dogfood found one exact Claude replay defect after successful compaction: pinned
`claude-agent-acp` 0.60.0 drops `isCompactSummary` while translating its private generated summary
into an ordinary user chunk during `session/load`. Panels therefore exposed that private summary and
its transcript path after refresh. A red regression captured the exact pinned template; the minimal
provider-local replay classifier now replaces only that synthetic chunk with a terminal content-free
`ContextCompaction`, leaving ordinary user replay untouched. The complete Claude strategy suite is
25/25, scoped Ruff and strict Mypy pass, and live refresh now shows only `Context compacted` with no
summary, path, or instructions.

A full server restart then proved both durable bindings continue rather than cut over: Claude replied
exactly `CLAUDE RESTART OK`, and Codex replied exactly `CODEX RESTART OK` through Safari. The durable
bindings remain Claude `e2562875-0b3f-482e-8f75-52e0d0e46731` and Codex
`019f81a8-7041-7bb3-b7da-4445912fc3d0`, both generation 1, matching their completed Employee-step
runs. Backend feature work and dogfood are complete.

ACP-10's entry audit found that the older Hermes human-Ticket and Automatic-Employee observations
were on different Tickets and that schema 25 had intentionally reset the pre-cutover run's binding.
Rather than infer continuity, root closed the literal experiential gap on fresh Hermes Ticket
`t_x2f5up6e`. Opening pristine Kickoff created no binding; the first UI prompt created session
`f5b57fd7-7873-4285-9f6c-0e6d0ee219d4` generation 1 and returned `HERMES HUMAN OK`; natural
discovery then completed `run_1wcjzguy` on that exact session with typed worker activity and a Success
proposal; the later human prompt returned `HERMES AFTER AUTO OK`. Binding, Ticket mirror, and run
session are byte-identical. All three production backends now have the same current live continuity
proof.

The final provider-specific UI matrix then closed Hermes permission rejection and Codex permission
rejection, Queue, Send Now, disabled Steer, and live-plan/recovery presentation. Claude proved disabled
Steer and FIFO Queue. Its first Send Now probe exposed one real defect: although the old turn was
visibly interrupted and the successor completed exactly once, the pinned Claude adapter later emitted
the forbidden old final answer from provider-side background work. ACP updates carry no prompt epoch,
so `acp-10-claude-requested-cancel-isolation` added one provider capability that routes every terminal
Claude requested cancellation through the existing same-binding fresh-child recovery; Hermes and
Codex keep their normal terminal-response reuse path. Focused broker/hub tests passed 18/18, provider
definition tests passed 3/3, and Ruff plus strict Mypy passed. Root spot-checked the exact boundary.

The live retest then replaced Claude child PID `2477` with PID `7324` while retaining Ticket
`t_1xbdpkq0`, ACP session `e2562875-0b3f-482e-8f75-52e0d0e46731`, and binding generation 1. The
successor returned exactly `CLAUDE ISOLATION RETEST SUCCESSOR OK 20260721`, and the forbidden old
completion remained absent beyond the previously observed late-output interval. Claude's native
`ExitPlanMode` path also produced the real `Ready to code?` ACP permission card with all five provider
choices; selecting `Yes, and use "auto" mode` settled it once and completed the read-only probe. The
Hermes, Codex, and Claude experiential matrix is now complete.

The one ACP-10 settled-tree independent review then inspected the exact owner brief, proof artifacts,
current docs, and all named load-bearing seams. Its verdict is `READY` with zero unresolved P0/P1
findings. Root accepts that report in full; there is no correction to make and no second broad review.

The no-writer freeze is now established. All sub-agents are complete; production server PID `2412`
and its ACP child shut down cleanly; port 8767 is empty; no workspace test/build/server/backend process
remains; and `git diff --check` passes. `freeze-git-status.txt` and the sorted
`verified-input-manifest.sha256` retain the exact pre-run worktree and verification-input state.

Frozen snapshot attempt 1 ran once from `2026-07-21T00:09:19Z` to `00:12:12Z` and is retained as a
failure, not a completion claim. Ruff found four formatting violations; unit reported 14 failed and
1,005 passed, all caused by old test imports/SQL layout or fixtures/assertions that omit the new
`employee_backend` Ticket/Worker-manifest field. Mypy, build, all 13 frontend scripts, and 103 e2e
tests passed. The 958-line, 43,424-byte log has SHA-256
`daa64377bf6ba1c89be8f33b80b7c82ca49dfb602cce246fea228813a55441bf`; the 1,154-entry frozen input
manifest remained byte-for-byte unchanged. No immediate full rerun is authorized.

Next: reopen only the exact formatting/test-fixture failures, run focused checks, refresh the affected
audit/review evidence, establish a fresh no-writer snapshot, and only then make the next full-gate
decision.

The bounded correction is now settled. Two disjoint sub-agents handled Ruff formatting and
Worker-manifest fixtures while root made the two-line direct-`Ticket` fixture repair. Root inspected
the combined diff: it changes no production behavior and weakens no assertion. Ruff passes on all 11
implicated files; the 10-file unit gate passes 76/76 with two existing warnings; and `git diff
--check` passes. `focused-checks-and-disposition.md` marks the correction ready for a fresh no-writer
snapshot without another broad review.

Next: preserve the failed attempt under an attempt-specific log name, establish a fresh exact
status/input manifest with zero writers, and then run the one clean canonical gate for that new
snapshot. The failed snapshot remains part of the final verification report.

The failed log is now preserved byte-identically as `data/verify/acp-10-final-attempt-1.log`. A fresh
corrected freeze began at `2026-07-21T00:17:42Z`: all implementers are complete, no workspace writer
or port-8767 server exists, exact Ruff and `git diff --check` pass, and the final status/input snapshots
are `freeze-git-status-attempt-2.txt` and `verified-input-manifest-attempt-2.sha256`.

The final corrected frozen snapshot then passed its one clean canonical gate from
`2026-07-21T00:18:51Z` to `00:21:43Z`. Ruff, Mypy, build, and frontend are green; unit is 1,019/1,019;
e2e is 103/103; exit status is 0; and the final line is `VERIFY: PASS`. The 157-line, 9,051-byte log is
`data/verify/acp-10-final.log` with SHA-256
`d66e7bfd939d9622ac8673b6cba00937f1124c0b3a95d0d6a37bcda4513a8963`. The final 1,162-entry input
manifest remained byte-for-byte unchanged at SHA-256
`d7265946efa9bac6a5cee8130ba020dd4827dba8b25a1963491e58f6cd67c611`.

Safari then loaded the unchanged verified build at the real `8767` Ticket route. It showed Panels'
restrained dark Ticket/proposal layout, `Connected`, the narrow Claude conversation rail, durable
typed replay/content-free compaction, and an idle Worker. The final frame is recorded in
`verification-report.md`; temporary server PID `58655` shut down cleanly afterward.

ACP-10 is complete: zero unproved requirements, zero unresolved review findings, exact production
catalog `hermes, codex, claude`, PASS backend qualification and real Computer Use matrix, PASS legacy
absence/current docs, one retained failed snapshot, and one clean final frozen-tree verification.

## Current work cycle (2026-07-19): Workspace left-panel information hierarchy exploration

The owner selected the three-level image direction, refined in
`orchestration/workspace-redesign/project-worker-stage-hover-gutters.png`: project → worker →
non-empty stage → ticket, with stage absent from ticket rows. All labels stay flush. Chevrons are
hidden at rest and reveal only at a hovered grouping header’s right edge. A generous, thicker divider
ends worker blocks; thinner dividers separate stage groups. No product code changed; awaiting owner
review before a contract-scoped implementation ticket is cut.

Interactive fake-data version added at `orchestration/workspace-redesign/interactive-three-level-grouping.html`.
Project, worker, and stage headers collapse independently; ticket selection changes locally; running
tickets animate. This is a standalone design mockup, not production UI.

Latest owner refinement: each worker type is now a transparent bordered card with its title and
non-empty stages inside. Project headings grew further; worker and stage headings also grew. The
old thick worker divider is removed because the card edge now defines the worker boundary.

Card borders are now lower-contrast. Worker-type names use uppercase at their existing scale; stage
names are normal-cased headers, larger than the tickets they head.

Stage headers were enlarged again to 21px after owner feedback that they still read too much like
ticket text. Their original muted color is preserved.

Owner approved moving the mockup into production on branch
`codex/workspace-three-level-grouping`. The contract-scoped implementation ticket is cut at
`orchestration/tickets/workspace-three-level-grouping/contract.md`; scope is the existing Workspace
route, shared CSS, and focused board e2e coverage. The board and manifest APIs remain unchanged.

The first implementation pass is complete and focused checks pass. Independent diff review found four
corrections before integration: reconcile one stale paired-work row assertion, restore Hide done route
persistence coverage, use keyboard-only `:focus-visible` for chevrons, and match the approved 40px/44px
project hierarchy. These findings are accepted; full `./verify` waits for their correction.

All four review corrections passed focused checks and the second review returned CLEAR. The canonical
`./verify` then exposed one further Workspace-specific stale E31 reload assertion for the removed
pending-proposal row mark; that integration repair now snapshots the Implementation group nesting and
title-only non-running row instead. The same verify run also failed broadly in unrelated in-progress
ACP/shared-registry work already present in the owner worktree (64 unit failures, unresolved ACP
frontend packages/types, and 36 chat/ACP e2e failures), so the repository-wide gate is not green.
The repaired E31 reload test passes in isolation; the five hierarchy/ownership focused tests also pass.

Owner correction after the first commit: structural stage grouping replaces repeated worker/stage text,
not the existing per-ticket condition circle. The production row now restores the full waiting, running,
approval-needed, paired, errored, and completed StageMark states while retaining title-only row text.

Latest owner typography correction: worker types return to normal-case 20px section headers; stage names
become compact 11px uppercase labels. The production CSS, interactive mockup, contract, and focused visual
assertions move together.

## Current work cycle (2026-07-19): shared employee-child registry — port legacy to per-child

Owner decision (2026-07-19): port the legacy (flag-off) path to one-child-per-employee — the
same topology the live relay (flag-on) path already uses — by extracting a **shared per-employee
child registry both paths ride**. Keep both paths (A/B the DB-translate transport against the
relay on an identical child substrate). Payoff: with both paths per-child, the worker-identity
leak the three local Hermes patches fix is impossible by construction in BOTH flag states, so
S3b (revert to stock Hermes) becomes safe without deleting legacy or flipping the committed
default. Full design + constraints: `orchestration/hermes-relay-redesign/shared-registry-plan.md`.

Reshaped picture (from two ground-truth investigations): the relay path does NOT use
`GatewayChild` — it uses `RawFrameChildTransport`, a peer reader on the same `spawn_popen`/
`ChildProcess` seam. Two per-child readers each duplicate spawn+identity+lifecycle; the reader
half (raw frames vs typed JSON-RPC) is what legitimately differs. Extract the duplicated half
into `minds/employee_child_registry.py::EmployeeChildRegistry` (keyed by employee), depending on
an injected `ChildReader` interface both readers satisfy.

Stages (serial, per-wave commits): **P1** extract registry, relay rides it, flag-on unchanged
(zero test edits = the proof) → **P2** legacy onto the registry — `GatewayChild` becomes a
spawn-decoupled `ChildReader` AND `SharedGateway`/`EntityRoutingGateway` rekeyed
per-role→per-employee, one ticket, flag-off unchanged (old P2+P3 folded; the split was
artificial — same green guard) → **S3b** `git revert -m 1 047ba8298` in Hermes (orchestrator
does this directly; confirmed clean isolated bubble, pre-patch parent `3a1a3c7e6`), verify both
flag states. Load-bearing
constraints C1 arming-order, C2 request/reply unification, C3 identity-env-as-strategy, C4
lifecycle move, C5 fail-closed persist, C6 on_frame weld — see plan doc.

Current state: P1 contract cut (`orchestration/tickets/hermes-relay-p1-child-registry/contract.md`).
P1 plan at rev 2: rev-1 (Opus) got a Codex plan review that found 8 blockers — 2 architectural
(the "pool builds the reader callback" shortcut didn't establish the real shared seam) + 6 concrete
(monkeypatched `_PoolSessionResponder`, mypy `ChildReader` vs `RawFrameChildTransport` mismatch,
cyclic `RawFrameTransportError` in a `minds/` module, coarse retire timing, session-param owner,
missing cleanup constant). Orchestrator directed a re-architecture: registry OWNS each child via an
injected `reader_factory` + emits frames to a `ChildFrameSubscriber` in exact order
`sink1(deliver)→sink2(reader responder settle)→sink3(fold)`; neutral `ChildReaderError` base with
`RawFrameTransportError` subclassing it; composition-injectable optional `reader_factory` seam. Rev-2
`plan.md` resolves all 8; orchestrator independently re-verified the two load-bearing invariants
(sink1-before-sink2 FIFO, relay id-space coupling). Codex round 2 confirmed all 8 resolved + found
one blocker (a `request_on` re-resolution that reopened the N/N+1 rebind race — fixed by having
`submit_step_prompt` issue on the captured `record.transport`) + two mechanical should-fixes, all
corrected in the plan.

**P1 is COMPLETE and verified** (commit pending below). `EmployeeChildRegistry` (`minds/`) owns each
child via an injected `reader_factory` and emits frames to a `ChildFrameSubscriber` in exact order
sink1(deliver) → sink2(reader responder settle) → sink3(fold); `RawFrameChildTransport` gained
`request()`/reply + the pre-send id hook + a neutral `ChildReaderError` base; the pool is a thin
subscriber over the registry, relay behavior byte-unchanged (zero existing-test edits). The Codex
implementation-diff review found the production diff faithful (no blocker) + 4 test-strength
should-fixes, all applied and RED-verified. One `./verify` FAILED on a PRE-EXISTING flaky e2e
(`test_chief_neutral_pane::test_neutral_chief_compact_4009_surfaces_failure`, ~1-in-5 on base with P1
fully reverted — not P1's fault; flagged for a separate fix); the clean re-run PASSES all gates
(ruff, mypy, 1053 unit, build, frontend, 135 e2e — `VERIFY: PASS`).

**P2 (legacy onto the registry) — in plan, not yet implemented.** Owner rulings (2026-07-19) that shaped
it: (a) DROP the shared-process multiplex outright — no dual-mode/vestige — keep BOTH communication
methods (relay flag-on untouched in behavior; legacy flag-off keeps typed→LiveSessionManager→DB→ChatPanel,
transport per-employee). (b) DELETE day chat — it's dead code (no UI; `DayRoute` renders only the day
overview). Keep the day planning overview; remove only the chat entity + `days.chat_session_key`. (c)
Skills/catalog PER-WORKER (Option B) — zero shared processes; the "/" menu's Hermes skills come from the
worker's own child (built-ins always; skills after first message); this + day-removal means the residual
shared worker gateway is removed from BOTH flag states (its only jobs were catalog + days). (d) Interrupt
stays on `LiveSession` flag-off (Codex-confirmed leak-safe; interrupt isn't a session-identity op). P2 plan
went dual-mode → drop-shared re-plan → Codex round 1 (7 blockers) → revision → round 2 (5 blockers, 5
resolved) → this amendment (day-removal + B + residual-out-both-states + mechanical fixes: stderr shutdown
lifecycle, history-id coherence on concurrent /new, same-entity winner replacement, ownership-same-registry,
catalog adapter-contract/cache/dedup). Next: final Codex round on the amended plan → implement → diff review
→ `./verify` BOTH flag states → commit. Branch note: this session (113c8e88) is a /branch off the original;
the P2 planner is `p2-replan`, the status-loop cron did NOT carry over.

Process note: an investigation sub-agent ran `git reset --hard` in the
Hermes checkout and
destroyed a pre-existing uncommitted edit to `plugins/platforms/matrix/adapter.py`
(unrecoverable — unstaged, never in the object store); owner accepted the loss (stock-Hermes-only
anyway). All sub-agents now carry strict read-only-on-Hermes instructions; the S3b revert is
orchestrator-run, not delegated.

Prior redesign state (still true): S0–S3 + the chat-UI polish are committed to main; the live app
runs relay-on (`config.yaml` uncommitted `true`; committed default stays `false`). Ticket-pane
label now shows the worker name (e.g. "Coding worker") not the ticket title (uncommitted).

## Current work cycle (2026-07-17): Hermes integration restructure — S0 protocol spike

Exploration note (2026-07-18): the VPS agent-GUI survey found a possible simplification
that must be tested before adding provider-specific translators. Hermes 0.18.2 has native
`hermes acp`; maintained ACP adapters exist for Claude Agent SDK and Codex app-server; Emdash
uses all three behind one ACP runtime. The open gap is Hermes `clarify`/ACP elicitation.
Research and the proposed three-provider conformance spike are recorded in
`orchestration/vps-agent-gui-research-2026-07.md`. This is a finding, not an owner decision;
the active S2b work remains unchanged.

Current build stage:

- Owner approved the relay architecture (see `D-hermes-relay-architecture` and
  `D-runtime-neutral-pane-vocabulary` in decisions.md): Panels' server keeps the single Hermes
  connection but relays native conversation frames to the browser instead of translating them
  through SQLite; the pane speaks a runtime-neutral ACP-shaped vocabulary so future Codex/Claude
  CLI workers plug in as adapters.
- Staged plan: S0 live spike → S1 relay foundation (`hermes serve` + WS client + relay endpoint)
  → S2 native-vocabulary chat pane → S3 worker steps on the shared backend → S4 deletion of the
  demux/DB-streamed turns/polling plus the transcript-ownership ruling → S5 optional profile
  registration for desktop access.
- S0 PASSED live: authenticated WS connect (`gateway.ready`), session.create, prompt.submit
  with real streamed frames, hard socket drop, reconnect + session.resume with full history and
  proven conversational continuity, plus the concurrent-resume probe (server does not lock —
  relay must stay sole upstream owner). Record: `orchestration/hermes-relay-redesign/s0-spike.md`;
  staged plan: `orchestration/hermes-relay-redesign/plan.md`. Scratch backend stopped after the
  run; the live Panels server and the user's Hermes processes were never touched.

What just passed:

- Investigation complete (three parallel sweeps + direct source verification). Key verified facts:
  `tui_gateway` is the official surface with stdio and WS transports over one wire format;
  `hermes serve` is the headless backend; `apps/shared/src/json-rpc-gateway.ts` is the shared
  client; events route to the owning transport and carry `session_id`; WS disconnect has a
  grace-windowed reaper cancelled by quick reconnect/resume; Panels today uses ~10 RPC methods and
  rebuilds the client half (~4k+ lines) with the DB in the conversation path (500 ms poll confirmed
  at `web/src/components/ChatPanel.svelte:155-161`).

- Owner redirected the upstream topology mid-S1: one child process per employee
  (`D-child-per-employee`) supersedes the shared `hermes serve` backend and the prompt-carried
  credential identity (spawn env `PLAN_TICKET_ID` + actor is the carrier). No idle reaping in
  v1 — provider-side prompt caching gives keep-alive no token benefit; respawn costs seconds
  plus a fresh shell env. Plan and S1 contract revised accordingly; the in-flight S1 ticket
  agent was paused before the redirect and resumed on the revised contract.

- The S1 ticket completed its full pipeline: multi-round-reviewed plan, implementation,
  and an implementation-diff Codex review that converged 9 → 3 → 2 → 0 findings
  ("the ticket is complete"). 14 new files under `src/planner/hermes_backend/` +
  `tests/unit/` and three minimal edits (`core/server.py`, `core/config.py`,
  `config.yaml`); 56 focused tests green; backend off by default; nothing under
  `minds/`/`chat/`/`runtime/` touched. Mid-pipeline rulings are recorded as
  `D-relay-raw-frame-transport` (raw stdio frames on the spawn seam; pool owns session
  binding; 9-method denylist) and `D-s1-concurrency-scope` (trigger-bound limitations).
- First canonical `./verify` FAILED at the skip-scan gate: the registry-completeness test
  carried a forbidden `pytest.skip` for a missing Hermes checkout. Orchestrator-direct
  integration repair: the test now hard-asserts the checkout resolves (skips would
  silently disable the drift trip-wire); unused import removed; focused file green;
  skip-sweep across all seven new test files clean. Failed transcript retained at
  `data/verify/s1-relay-foundation.log`.

- The second canonical run passed every suite (919 unit, all frontend, 114 Playwright,
  mypy across 129 files, build) but failed the ruff gate on scratch-grade lint in the
  copied S0 spike script; orchestrator-direct lint repair applied, repo-wide Ruff clean.
  Transcript retained at `data/verify/s1-relay-foundation-2.log`.
- The third canonical `./verify` passed all gates, but during its e2e phase the ticket
  agent's ordered R3-round3-1..4 code audit landed one genuine one-line fix (the
  transport's started-flag was set before `Thread.start()`, so a failed start would make
  shutdown join a never-started thread) — flagged immediately, inspected and accepted.
  Because that pass no longer described the settled tree, it is not the claim; transcript
  retained at `data/verify/s1-relay-foundation-3.log`.
- The fourth canonical `./verify`, against the frozen settled tree, PASSES all gates:
  Ruff, mypy (129 source files), 919 unit tests (nine existing warnings),
  compile/static/CSS checks, Svelte, production build, frontend tests, and 114
  Playwright tests — final `VERIFY: PASS`. Transcript
  `data/verify/s1-relay-foundation-4.log`, SHA-256
  `6c9713e992947827a4d8bf948e2e286d65ed1951b9cc2975698544013ef362a5`. S1 is complete;
  nothing is committed pending the owner's word.

- S1 is committed to main as `052a968` (green-wave practice; owner said continue). The
  owner's unrelated `skills/panels-chief-of-staff/SKILL.md` edit remains uncommitted.
- Sequencing correction while cutting S2 (`D-chief-first-cutover`): a ticket's chat and
  steps share one stored session with one owning process, so ticket chat cannot move
  before steps — the Chief (no automatic steps) cuts over first; ticket employees move
  chat + steps together in S3. S2 splits into S2a (neutral vocabulary + Hermes
  translator + mediated commands + transcript-mirror tee, backend) and S2b (Chief pane
  cutover, frontend). During transition the tee keeps the Panels transcript mirror fed
  so `D-transcript-ownership-open` stays open for S4.

- S2a (`hermes-relay-s2a-neutral-translator`) is COMPLETE and verified. Final scope per
  the owner's free/not-free rulings (`D-only-free-hermes-features`,
  `D-native-turn-concurrency`): the neutral conversation core (attach/history, send with
  resolved image refs, clarify answer, approval response, interrupt) plus compact and
  the as-is catalog read; model machinery cut; cut/unknown request kinds rejected. Six
  source + eight test files; additive wiring only in `core/server.py` and
  `composition.py`; chat/ call-only; S1 files unchanged. Pipeline: one plan review and
  one diff review (per `D-codex-loop-cap`); the diff review surfaced six genuine
  defects, plus the image-reference defect found by S2b's planning review — all seven
  fixed RED-first. Canonical `./verify` PASSES all gates: Ruff, mypy (135 files), 956
  unit tests, build, frontend, 114 Playwright. Transcript
  `data/verify/s2a-neutral-translator.log`, SHA-256
  `e1984e0cfe9940302e4e9e0764cf03d3d07837395caff04603102732b798b2cb`.
- S2b (`hermes-relay-s2b-chief-pane`) is cut and mid-planning: contract locks the
  ownership handoff (flag-on composition never starts the legacy Chief child; pool
  adopts and persists the Chief binding), pool-owned new-conversation, the neutral pane,
  and Playwright re-anchor in both flag states. Its plan review produced 13 internal
  findings (being folded) and three cross-boundary rulings (image refs → fixed in S2a;
  central flag-on Chief guard incl. recovery settle → in-scope; Chief binding
  persistence → in-scope). Implementation stays gated until this S2a commit lands.

- S2a committed to main as `fe9e2b5`; S2b's implementation gate opened against it.
- S2b implementation was interrupted mid-run by a usage outage (~17:20): Waves 1–2
  (vocabulary/new-conversation seam; Chief adoption + binding persistence) reported
  complete, Wave 3 (composition split + two-owner assertion + central Chief guard)
  partial on disk, Wave 4 (scripted-child e2e composition) and all frontend waves not
  started. A fresh resume orchestrator was dispatched: ground-truth the tree against the
  reviewed plan first (`resume-ground-truth.md`), complete implementation, then the
  single Codex diff review. Non-ticket files in the tree (owner's SKILL.md edit;
  live-system `initiative_planning` worker-type output) are explicitly out of its scope.

- S2b (`hermes-relay-s2b-chief-pane`) is COMPLETE and verified. The resume orchestrator
  ground-truthed the outage-interrupted tree, completed the central Chief crossover
  guard, the scripted-child e2e composition, the frontend (neutral client, capabilities
  probe, `ChiefNeutralPane`, route swaps), and both Playwright suites. Two Codex diff
  rounds (cap) surfaced and closed seven findings — notably fail-closed binding
  persistence (publish only after the write lands; failure tears the child down) and
  the invented-catalog-shape defect (picker + scripted child now parse the native
  `pairs`/`skill_count` shape). Canonical `./verify` PASSES all gates: Ruff, mypy
  (137 files), 984 unit tests, build, frontend, and 129 Playwright (15 new flag-on
  Chief scenarios + legacy flag-off unchanged). Transcript
  `data/verify/s2b-chief-pane.log`, SHA-256
  `7c01892dbf156b5111a9866be00d35697c51390ceee9be0f893bac8cdd9a9f46`.
- Meanwhile the live Panels system committed its own `initiative planning worker`
  (`f75ddfc`, 19:33) through its normal flow; the canonical run covers the combined
  state. The owner's in-progress skill edits remain uncommitted.
- S3 (`hermes-relay-s3-ticket-employees`) is cut and mid-planning (plan written, review
  round next); implementation gated on this S2b commit.

- S3 (`hermes-relay-s3-ticket-employees`) is COMPLETE and verified. Every active ticket
  employee runs as its own pool child flag-on: steps submit through the pool with
  settlement correlated by prompt.submit ACK disposition (arm-on-own-ACK; pre-ACK
  terminals buffered and counted against the skip, pre-ACK deltas dropped; id-match
  guard against false-arm), ticket panes speak the neutral vocabulary (step turns
  stream live), `panels` CLI identity reads `PLAN_TICKET_ID` first with the
  transitional Hermes-env fallback, the crossover guard and two-owner assertion extend
  per-ticket, and the history route rejects pool-owned tickets. Flag-off is exactly
  today's system. Evidence beyond the review rounds: 400x pre-ACK stress (0 failures),
  mutation-kill proofs on the settlement tests, a full disposition case-matrix trace.
  Accepted trigger-bound limitation: composition-level interleaved tests survive the
  buffering mutation (unit tests are the honest, mutation-proven guard).
- The build crossed a usage outage and a three-agent coordination failure (out-of-order
  inboxes produced parallel writers; all work converged and was reconciled, phantom
  scope surgically removed, the tail completed inline by the orchestrator under a
  hash fence). Full trail: the ticket dir's wave-log, resume-ground-truth, and review
  artifacts; process lessons recorded in orchestrator memory.
- Canonical `./verify` PASSES all gates: Ruff, mypy (139 files), 1038 unit tests,
  build, frontend, 135 Playwright — final `VERIFY: PASS`. Transcript
  `data/verify/s3-ticket-employees.log`, SHA-256
  `f0e054686c69f0fb18c10e4702998a3554ba9b6f900b23c9580d517152c0d6c2`.

Next step:

- Commit the S3 green wave. Remaining in the redesign: S3b (revert the three local
  Hermes commits — stock Hermes; flag-on as the operating mode is the precondition),
  the `relay_chief_enabled`→backend-named rename (`D-relay-flag-rename-debt`), and S4
  deletion (legacy machinery; owner's transcript ruling lands there,
  `D-transcript-ownership-open`).

Blockers:

- None.

## Current work cycle (2026-07-16): fail-closed Employee session ownership

Current build stage:

- Ticket `t_8vww58fj` is integrated and in final closeout verification. Panels fetches every durable
  owner deterministically and rejects any ambiguous effective binding before returning or persisting it.
- Hermes now restores every current-turn session ContextVar over the shared local terminal snapshot and
  exports those values to the command's child processes. Shell functions stored in the snapshot cannot
  intercept that restoration.
- Production and the available compatible backups had zero duplicate durable owners. The existing
  transactional ownership gate is sufficient, so no schema migration was added.

What just passed:

- RED for direct durable lookup returned an arbitrary `200` Ticket when two Tickets shared a session;
  after the read hardening, the same focused test passes with validation and sorted Ticket ids.
- RED for both ordinary and force-fresh second-Ticket claims did not raise; after the writer gate, both
  focused cases pass and prove the claimant row and event stream remain unchanged.
- A Codex review found the idempotent already-corrupt ownership edge; RED reproduced it, and the writer
  now rejects it before the ordinary idempotent return.
- Final review found the same issue on compare-and-swap winner adoption; RED reproduced that path, and
  the writer now validates the one effective session before either returning or persisting it.
- The complete focused worker lookup, Ticket engine, and human Chat binding set passes (`56 passed`, one
  existing FastAPI deprecation warning). Focused Ruff reports `All checks passed!`, and
  `git diff --check` is clean. Canonical `./verify` was deliberately not run in this branch.
- Hermes' exact cross-session, all-mapped-variable, snapshot-function, snapshot-poisoning, and
  CLI-rotation regressions pass (`18 passed`); focused Ruff and `git diff --check` are clean.
- Panels merged as `b8ee189`; Hermes merged as `047ba8298`. Both repositories retained their unrelated
  pre-existing workspace changes.
- `panels restart` resumed this conversation as durable session `20260715_195950_a2e173` with new live
  id `9fabc812`; `panels worker my-ticket --json` resolved `t_8vww58fj`, not `t_4bps5bwm`.

Current hypothesis:

- Confirmed. Panels owns unambiguous durable Ticket binding; Hermes owns authoritative per-turn terminal
  identity. Fixing both boundaries removes the demonstrated leak and fails closed on inconsistent data.

Next step:

- Run one canonical `./verify` against this settled tree, then supersede the stale failed Closeout
  proposal with the merge, review, restart, and verification evidence. No further source change is planned.

Blockers:

- None.

## Current work cycle (2026-07-15): controlled Panels restart supervisor

Current build stage:

- The verified `codex/panels-restart-supervisor` branch is approved and integrated into
  `main`. `panels serve` is now the stable foreground owner of one replaceable
  application process, and `panels restart` requests replacement through a local
  versioned control exchange. The server launch root, interpreter, environment, and log
  streams come only from the original `serve` process; a Ticket worktree cannot choose
  the replacement.
- Stopgap commit `430cf92` separately makes the server operator-owned in the shared and
  new-worker skills. Workers never discover or signal a PID, run `panels serve`, or launch
  a worktree replacement. Until the controlled command is available, they report that a
  restart is required and stop.
- The verified landing includes `main` through merge `ddaf5c1`. No live server was
  stopped or restarted as part of implementation or integration.

What just passed:

- Focused lifecycle acceptance passes 12 real-process cases. They cover duplicate
  ownership, same-socket safety, acknowledgement ordering, operator shutdown during
  incomplete and accepted requests, unexpected-child priority over queued restart,
  serial generations, captured-root restart, unexpected exit, and cleanup.
- The integrated control/shutdown/provisioned-skill bundle passes 95 tests. Ruff is
  clean, mypy reports no issues across 120 source files, and `git diff --check` is clean.
- The corrected implementation reviews report `NO FINDINGS` on both Standards and Spec.
  Two read-only Codex implementation reviews report `NO VIOLATIONS`.
- The clean canonical completion run passes Ruff, mypy across 122 source files, 854
  unit tests with nine existing warnings, compile/static/CSS checks, Svelte with zero
  errors or warnings, the production build, all frontend tests, and 114 browser tests.
  Every gate is `ok` and the run ends `VERIFY: PASS`. The full transcript is
  `orchestration/tickets/panels-restart-supervisor/verify-final.txt`, SHA-256
  `2ccfe2a325214e276abd717cc1e6116fb9d8c05cbb7ec500d5c997b7f00e5ce3`.
- The first verifier attempt exposed only that the borrowed main-worktree virtualenv's
  editable install resolved the old main source. Running the same gate with this
  worktree's `src` first on `PYTHONPATH` corrected the test environment; no product
  change was needed.

Next step:

- The next operator-started `panels serve` process will use the landed supervisor. Do
  not start, stop, or restart the live server merely to complete this integration.

Blockers:

- None.

## Current work cycle (2026-07-15): paired new-worker Understanding Stage

Current build stage:

- Ticket `t_gn7x278u` is verified on isolated branch `ticket/t_gn7x278u-understanding` from
  `main` at `5f5ea6f`. The completed diff inserts paired `needs_understanding` immediately after
  Kickoff, backfills existing `new_worker` field maps without rewinding Tickets, updates the shipped
  specialist and live Worker-type documentation, and adds backend, gateway/session, and Playwright
  coverage. An accepted High review finding is corrected: historical typed-ticket schemas that preserve
  `worker_type = 'new_worker'` with coding-shaped field JSON now gain every missing new-worker-specific
  slot (`understanding`, `stages`, `thinking`, `drafting`) in lifecycle order while preserving all
  original top-level slot values as legacy extras.
- Parent-review production corrections are applied in this worktree only. Reconciliation uses entered
  status only when the Stage actually changes; current paired reconciliation preserves `paired_work`.
  Ownership override writes now no-op only on identical override maps, emit
  `previous_effective_ownership_mode`, preserve status for same-effective current-Stage changes, and never
  change status for future-Stage overrides. Takeover/release now persist or clear explicit current-Stage
  overrides even when the default has the same effective mode. Drop remains on resting status, and
  auto-accepted proposal writers use entered status only for real Stage advances.
- The corrected plan and completed diff passed fresh read-only Codex reviews with `NO VIOLATIONS`.
  Accepted findings made Understanding the explicit `first_worker_stage`, separated deterministic
  protocol assertions from claims about model judgment, completed the Chief external-work prefix,
  hardened second-turn session coverage, and closed the historical migration gap.

What just passed:

- Focused registry, migration, external-work, eligibility, shared Ticket Chat/session, provisioned-skill,
  and browser progression tests are green in the isolated worktree. Chromium's Codex sandbox failure
  was rerun outside the sandbox and both focused e2e cases pass.
- The High review regression was replayed RED against this worktree's `src` with the pre-fix migrator:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py::test_create_schema_backfills_historical_new_worker_coding_fields_for_audit -q`
  failed at `tickets_data.audit_ticket_registry_integrity()` for `t_legacy_new_worker` with corrupt fields
  JSON. After the correction, the same regression plus the existing proper-shape/idempotence migration
  test pass, and the affected DB/new-worker suite passes:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py tests/unit/test_worker_type_persistence.py tests/unit/test_new_worker_type.py -q`
  (`91 passed, 2 warnings`). `PYTHONPATH=src .venv/bin/ruff check src/planner/core/db.py tests/unit/test_db.py`
  and `git diff --check` are clean.
- The canonical corrected-tree `./verify` passes Ruff, mypy across 116 source files, 823 unit tests
  with nine existing warnings, compile/static/CSS checks, Svelte with zero errors/warnings,
  production build, all frontend tests, and 102 Playwright tests. Every gate is `ok` and the run ends
  `VERIFY: PASS`. The full transcript is
  [verify implementation](/files/tickets/t_gn7x278u/artifacts/verify-implementation.txt), SHA-256
  `115f678308f4bf6970f5eb824a0d891e3646035a8362f32638944cd4c517e735`.
- Parent RED was reproduced with the expanded command: the stale discovery-loop test still expected no
  automatic paired dispatch, two human chat tests expected immediate `paired_work` without an automatic
  opening precondition, and two e2e tests waited for background loops that test-mode servers deliberately
  do not compose.
- After corrections, focused ownership/external-work/eligibility regressions pass:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_stage_ownership_backend.py tests/unit/test_chief_external_work.py tests/unit/test_automatic_employee_step_eligibility.py`
  (`78 passed`, one existing warning). The broader focused unit set also passes:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_stage_ownership_backend.py tests/unit/test_chief_external_work.py tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_human_chat_turn.py tests/unit/test_automatic_employee_step_eligibility_actions.py`
  (`139 passed`, two existing warnings).
- The parent command's unit-test portion passes:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_employee_step_runner.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/unit/test_human_chat_turn.py`
  (`167 passed`, two existing warnings).
- The expanded parent command now passes all selected unit tests plus both new-worker public-flow e2e
  tests. The e2e tests explicitly invoke the production `EmployeeStepRunner` with the shared fake gateway
  against the server DB because test mode omits background loops, then continue through the public Chat
  and browser surfaces. Ruff and `git diff --check` pass.
- Independent correction review found one redispatch edge: accepting a non-gating proposal on an already
  opened paired Stage used entered-stage status and reset it to `empty`. Acceptance now uses entered status
  only when the Stage advances; a regression keeps unchanged paired Stages at `paired_work`.
- Corrected review found the compatibility check still required an earlier worker-step event when a later
  paired Stage reused a session created by ordinary Ticket Chat. Compatibility now keys only on whether a
  worker-step opening started after the current Stage marker; an earlier human turn/session no longer
  blocks the one opening. The expanded focused gate, Ruff, stale-wording search, and diff check pass.
- Corrected implementation commit `8a24cfe` passed final independent review with `NO VIOLATIONS` and is
  integrated into current main as merge commit `d4867fe`. Fresh owner edits were backed up, stashed for the
  merge, and restored without conflict; the nested frontend worktree remains untouched.
- The first canonical post-merge `./verify` exposed three stale expectations of the retired silent-paired
  behavior: two probe genericity tests expected immediate `paired_work` on paired Stage entry, and one
  browser marker fixture did the same. Test-only commit `d6d5d1a` now expects `empty` on entry and seeds
  `paired_work` only for the post-opening rendering assertion. All three focused tests pass; independent
  post-verify review reported `NO VIOLATIONS`.
- Final canonical post-merge `./verify` passes all gates: Ruff, mypy, 850 unit tests, build checks,
  frontend tests, and 102 Playwright tests. The transcript is
  [verify closeout](/files/tickets/t_gn7x278u/artifacts/verify-closeout-paired-opening.txt), SHA-256
  `d11ecba7eaf5976f1125aecdf5a66556723ae9d51f22d6ffa6219b69d033e979`.
- Both temporary ticket worktrees/branches and their two temporary owner-state stashes are removed. The
  Closeout proposal is filed and the Ticket now rests at `awaiting_approval`.

Next step:

- Await Closeout approval. No deployment, restart, or live database operation is part of this closeout.

Blockers:

- None.

## Current work cycle (2026-07-15): exploration Worker type

Current build stage:

- Ticket `t_8vfx962r` has landed on `main` through feature commit `4f7916b` and merge
  commit `5e8c9fa`. The production registry now carries `exploration`; startup provisions
  `panels-worker-exploration`; Worker and Chief front doors and live docs name the type.
- The approved lifecycle is Kickoff → paired Understanding → Research Plan → Research →
  paired Answer → Follow-up → Closeout → Done. Research owns source discovery and corpus
  curation; there is no separate Corpus Stage. The specialist ends with the approved
  transferable-problem discipline for cross-domain research.
- Codex review found stale probe validation lists and stale live-doc references; those were
  corrected. Two later review rounds drove the remaining doc/comment cleanup and the final
  review reports `NO VIOLATIONS`.
- Panels restarted on the merged code. The live manifest serves the exact `exploration`
  lifecycle and `data/hermes-home/skills/panels-worker-exploration` resolves to the shipped
  specialist.
- Four real exploration Tickets now carry grounded Kickoff proposals: durable user memory
  (`t_pszubyxa`), Vylo onboarding (`t_jj25dbnj`), Panels VPS workflow (`t_4bps5bwm`), and
  existing-Worker management (`t_z9upbzf0`). The first three sit under their relevant
  sprint items; the fourth is a loose current-sprint Panels Ticket. All four are on today
  and await Kickoff approval. No exploration work was performed.

What just passed:

- Canonical `./verify` with this worktree's source pinned passes Ruff, mypy across 117
  source files, 823 unit tests with nine existing warnings, compile/static and CSS checks,
  Svelte check with zero errors/warnings, production build, frontend tests, and 100
  Playwright tests. It ends `VERIFY: PASS`; the transcript is
  `data/files/tickets/t_8vfx962r/artifacts/verify-closeout.txt` with SHA-256
  `26abe805a176f8d473a59fcc26d8560f4c3f63418514ecae2e24186a694cc0c7`.
- Canonical readback passed every onboarding check: title, priority, Worker type, Stage,
  approval status, exact Kickoff body, sprint item, effective project/sprint, and today
  placement. The record is
  `data/files/tickets/t_8vfx962r/artifacts/exploration-onboarding/onboarding-results.json`.

Next step:

- Propose Closeout for approval with the merge, verification, live activation, and four
  onboarding Ticket IDs. Do not start or monitor the explorations.

Blockers:

- None.

## Current work cycle (2026-07-14): live Panels connection status

Current build stage:

- Ticket `t_ycpcb619` has approved Implementation commit `fe7f0c1`. The current-main integration is
  complete on `integrate/t_ycpcb619` through feature commit `e3d77c4` and checked-in configuration
  correction `a1c00f2`; it retains newer main work and serves combined bundle `index-BKICWA5O.js`.
- The existing event WebSocket emits configured quiet heartbeats and owns the browser's
  Connected/Reconnecting/Offline lifecycle. Recovery preserves cursor replay and keyed invalidation,
  then performs one catalogue-owned refresh of currently subscribed resources. Navigation keeps this
  signal separate from worker presence on desktop and mobile.
- Current-main integration review found the missing checked-in heartbeat default and stale bundle
  evidence; both were corrected, the config gap has a RED/GREEN regression, and the final fresh review
  reports `NO VIOLATIONS`.
- Post-integration canonical `./verify` passes Ruff; mypy across 116 source files; 817 unit tests with
  nine existing warnings; compile/static and CSS checks; Svelte with zero errors/warnings; production
  build; the complete frontend suite; and 100 Playwright tests. The run ends `VERIFY: PASS`; transcript
  `data/verify/t_ycpcb619-closeout-pass.log` has SHA-256
  `ada673c7a925d66fb26bd6cd4529197b504c9038622121a840004563ea9c301e`.

Next step:

- Merge the verified integration into `main`, preserve unrelated local main edits, remove only this
  Ticket's temporary branches/worktree, and propose Closeout. No deployment, restart, migration, or
  follow-up applies.

Blockers:

- None.

## Current work cycle (2026-07-14): Workspace Hide done as the only visibility filter

Current build stage:

- Ticket `t_834r0tz6` is integrated with current main through merge commit `fb67232`; verified
  implementation commit `b0cc921` is its second parent. No deployment or restart applies.
- A fresh Workspace now starts with **Hide done** on. Turning it off reveals done tickets, and the
  existing app-level state preserves the choice across in-app navigation. The Ticket-status control,
  local state, filtering branch, status-only styles, and orphaned label helper are removed; every
  Ticket status remains visible. Grouping, Worker-type/Stage/activity ordering, collapse, selection,
  URL navigation, and row presentation stay on their existing paths.
- Live frontend documentation, the standing decision, and current Workspace redesign intent now describe
  the same one-toggle contract.
- The merge retained current main's full-page managed Markdown preview source and rebuilt one combined
  production bundle.

What just passed:

- The focused browser regression was RED against the old fresh default, then passed with the new default,
  absent status control, reveal-done behavior, and both navigation-persistent toggle states. Existing
  Chief, ownership, routing, collapse, row, and marker scenarios pass after removing only obsolete
  status-filter setup; two marker tests explicitly reveal done rows before asserting their terminal marks.
- The first independent Codex review found stale redesign intent and the now-orphaned `ticketStatusLabel`;
  both were removed. Corrected implementation reviews and the post-merge integration review with model
  `gpt-5.5`, read-only sandbox, and high reasoning report `NO VIOLATIONS`.
- The post-merge canonical `./verify` passes Ruff; mypy across 116 source files; 799 unit tests; compile/static
  and CSS checks; Svelte check with zero errors and warnings; production build; frontend tests; and 96
  Playwright tests, ending `VERIFY: PASS`. The full transcript is
  `[closeout verification transcript](/files/tickets/t_834r0tz6/artifacts/verify-closeout.txt)`.

Next step:

- Propose Closeout for approval. No deployment, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): full-page managed Markdown previews

Current build stage:

- Ticket `t_9fqnvpbc` is integrated into `main` by merge commit `cf0ea3f`; verified implementation
  commit `9c90622` is its second parent. The ticket branch and isolated worktree are removed.
- The centralized preview route fetches managed Markdown and renders it directly through the shared
  `MarkdownBlock` surface in a full-page document layout. Ticket and chat targets share that branch;
  bounded embedded Markdown, HTML sandboxing, and other file kinds are unchanged.
- The merge combined current main's managed-HTML base preparation with the Markdown route: HTML still
  resolves relative and root-relative sibling assets before Blob rendering, while Markdown keeps its
  canonical source and shared renderer contract.

What just passed:

- The two conflict-sensitive browser cases and the complete file-preview browser file pass 14/14. They
  prove the real Markdown popup URL and geometry, nested Markdown/image previews, self-link bounds,
  hash target switching, chat targets, absent duplicate top-level controls, retained HTML sandboxing,
  and managed HTML sibling stylesheets/images in both surfaces.
- Independent merge review found one low documentation gap for the chat full-preview hash. The live
  frontend doc now names both ticket and chat routes; corrected-diff review reports `NO VIOLATIONS`.
- Post-merge canonical `./verify` on `main` passes Ruff; mypy across 116 source files; 799 unit tests;
  compile/static, CSS, frontend check/build/tests; 96 Playwright tests; final `VERIFY: PASS`. The full
  transcript is [verify closeout](/files/tickets/t_9fqnvpbc/artifacts/verify-closeout.txt).

Next step:

- Propose Closeout for approval. No deployment, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): shutdown `0.0s` repair

Current build stage:

- Ticket `t_s0q8d2ln` is cut on branch `codex/fix-shutdown-zero-budget` from `main` at
  `bf2ac9b`. The implementation and all accepted review corrections are complete. A fresh final
  Codex full-diff review reports `NO VIOLATIONS`; the final standards re-review reports no code-smell
  findings and only requested this memory cleanup. The final spec re-review found one public-shape
  overreach and one inaccurate role label; both are corrected. The closing spec review then found a
  remaining SQLite busy-timeout deadline leak; it is reproduced and corrected. The final SQLite-
  aware Codex review reports `NO VIOLATIONS`, and both closing review axes report no findings.
- Shutdown now closes Employee admission, interrupts only running turns bound to their Ticket's
  durable Employee session, settles unfinished visible turns before process exit, and preserves the
  Ticket/session for restart recovery. Every interrupt, drain, gateway, child-process wait, and reader
  join consumes the same absolute deadline. A zero-time router observation is inconclusive, while a
  positive-time stuck router still errors. Every unique role gateway is attempted even after failure.
- No configuration, durable state, Ticket status, scheduler, retry queue, public adapter contract, or
  new lifecycle owner was added.
- The first canonical gate invocation failed because the shared `.venv` editable install imported
  `/Users/khushaljagota/.hermes/planning-v2/src` instead of this worktree. Its 39 unit and five e2e
  failures are older-source/new-test mismatches, including missing Chat recovery fields and the
  pre-fix shutdown behavior. The full failed transcript is
  `data/verify/t_s0q8d2ln-environment-fail.log`. With `PYTHONPATH` pinned, Python resolves this
  worktree's `src/planner`.
- The corrected-source canonical gate exposed an acceptance-fixture contention contradiction, not
  a production deadline failure. The process fake's 5,000-delta flood had produced 2,241 update
  events and 51,520 characters before shutdown, then the log reported `database is locked`. That
  flood manufactured the same SQLite contention which the separate locked-database regression
  deliberately owns. The process fixture now emits one nonempty delta, still requires the visible
  turn to settle `interrupted`, and also rejects any shutdown-settlement SQLite lock error. The
  locked-database regression is unchanged.
- A fresh read-only Codex review of the complete corrected diff, including the fixture/lock-boundary
  distinction, reports `NO VIOLATIONS` with model `gpt-5.5` at high reasoning effort. Recording that
  review is trivial integration glue performed directly by the orchestrator. No implementation changed.
- The corrected tree passes the complete canonical gate with this worktree's `src` pinned in
  `PYTHONPATH`: Ruff; mypy across 115 source files; 791 unit tests; compile/static and CSS checks;
  Svelte check with zero errors and warnings; production build; frontend tests; and 94 Playwright
  tests. Every gate is `ok` and the run ends `VERIFY: PASS`. The full transcript is retained at
  `data/verify/t_s0q8d2ln-pass.log`; the earlier genuine fixture-race failure is retained separately
  at `data/verify/t_s0q8d2ln-process-race-fail.log`.

What just passed:

- The production-process regression was RED with the exact `0.0s` router error and Uvicorn shutdown
  failure. Its corrected acceptance fixture keeps one nonempty partial output and still requires exit
  code zero, clean application shutdown, visible worker-turn settlement as interrupted, no SQLite
  settlement lock error, and an unchanged Ticket Employee session. It passes 10 consecutive process
  invocations. The deliberate locked-settlement regression and the ordinary unlocked settlement
  regression both pass; Ruff on the changed test and the complete diff check are clean.
- The expanded focused suite passes 169 tests with two existing warnings. It covers exact-once
  concurrent stop, missing and parked session guards, lock and reply deadline consumption, child
  cleanup, all-role cleanup, zero-versus-positive router waits, first-wins Chat and Ticket settlement,
  shutdown late completion, and ordinary-Pause late completion.
- Ruff is clean, mypy passes across 113 source files, and diff and whitespace checks are clean.
- Three Codex implementation-review rounds found and drove corrections for process-exit settlement,
  late Ticket completion, and their combined races. The final fresh implementation review and the
  final post-two-axis full-diff review report `NO VIOLATIONS`; the fresh post-fixture full-diff review
  also reports `NO VIOLATIONS`.

Next step:

- Hand the committed `codex/fix-shutdown-zero-budget` branch back to the owner. No merge, push,
  deployment, restart, or live-database action is authorized in this ticket.

Blockers:

- None.

## Current work cycle (2026-07-14): durable failed Chat turns

Current build stage:

- Ticket `t_f9ue37gz` is integrated into `main` by merge commit `bf2ac9b`; the verified
  implementation commit is `499b5be`. `main` has since advanced to `8fbf648` with the independently
  verified shutdown repair and remains checked out in `/private/tmp/panels-main-shutdown-closeout`.
- Failed and interrupted `chat_turns` now project into Panels Chat with partial output preserved. A
  guarded Continue action can resume only the latest eligible human turn in its exact current Hermes
  session, without replaying the original prompt.
- The shared Ticket and Chief Chat panel renders quiet, distinct Failed/Interrupted outcomes. Day Chat
  keeps the same backend state contract; no Day Chat screen was invented.
- The live v20 database was backed up with SQLite integrity `ok`, then migrated through canonical startup
  to schema v21. The live database reports integrity `ok`, the recovery column and unique index exist,
  and `/api/chat/t_f9ue37gz/state` serves the new `outcomes` contract. Panels now runs from the `main`
  worktree on port 8767.

What just passed:

- Focused backend tests cover ordinary and partial failures, interruption, session/turn/worker races,
  duplicate and stale recovery rejection, Ticket/Day/Chief eligibility, and the v20→v21 migration.
- Svelte check, frontend unit tests, production build, and four focused Playwright cases pass, including
  refresh, exact-once partial output, continuation, no-action failure, Chief interruption, and startup
  restart recovery.
- Independent implementation review and the post-merge integration review both report `NO VIOLATIONS`.
- The post-merge canonical gate passes Ruff, mypy across 115 source files, 775 unit tests, compile/static
  and CSS checks, Svelte check with zero errors and warnings, production build, frontend tests, and 94
  Playwright tests, ending `VERIFY: PASS`. The
  [closeout transcript](/files/tickets/t_f9ue37gz/artifacts/closeout-verify.txt) is retained with the ticket.
- A [real implementation screenshot](/files/tickets/t_f9ue37gz/artifacts/implementation.png) shows the
  preserved partial response, compact Failed outcome, safe Continue action, and unchanged composer with
  no clipping.

Next step:

- Await Closeout approval. No merge, deployment, restart, live migration, or ticket-owned cleanup remains.

Blockers:

- None.

## Current work cycle (2026-07-14): stage-level ownership

Current build stage:

- Ticket `t_ue4pt9ru` is integrated into `main` by merge commit `1595cf7`. During Closeout the user
  rejected the remaining Ticket-level Execution route, so its removal is now being completed directly
  on `main` without touching unrelated owner edits in the checkout.
- Stage ownership and scope remain separate. Worker type now selects the specialist skill; Tickets no
  longer persist, serialize, edit, prompt with, or display a separate execution route.
- Schema v23 removes the retired column and legacy audit/history exposure while preserving Ticket state and
  the legacy `khushal` coding ownership mapping. The UI retains only Owner and Continue/Stop controls.

What just passed:

- Focused RED/GREEN coverage passed for schema creation and migration, Ticket contracts and API rejection,
  worker prompts, and the Owner-only Ticket facts UI.
- Canonical `./verify` passes Ruff; mypy across 116 source files; 799 unit tests; compile/static and CSS
  checks; Svelte check with zero errors and warnings; production build; frontend tests; and 96 Playwright
  tests, ending `VERIFY: PASS`.
- Independent read-only review found no blocking logic or security issues. Its two suggestions were resolved:
  the worker-prompt docstring now names Worker-type skill selection, and legacy `khushal` mapping is covered
  through the full `create_schema` migration path.
- A delayed second review then found that legacy route audit events remained externally readable. The v23
  follow-up now removes those events, redacts old generated worker-step prompts in stored chat and Hermes
  history views, and removes the obsolete private v21 route-writing migration. Independent follow-up review
  found no blocking logic or security issue; its coverage suggestion for `implementer` and corrupt payloads
  was added before the final canonical pass.

Next step:

- Commit only the scoped v23 follow-up and refresh Closeout evidence.

Blockers:

- None.

## Current work cycle (2026-07-14): Workspace ticket ordering

Current build stage:

- Ticket `t_mw2vegjw` is integrated into `main` by merge commit `e53bfe0`; the ticket branch is
  deleted. The product change remains limited to the Workspace sorter, its focused browser regression,
  the served bundle, and the matching frontend decision record.
- Within each existing project category, rows now sort by served Worker-type manifest position,
  that type's served Stage position, newest `activity_at`, then the existing board sequence. Workspace
  waits for both board and manifest resources so the retired activity-first order never flashes first.

What just passed:

- The focused Playwright regression failed first against the activity-first implementation, then
  passes with `coding` and `new_worker` tickets proving type order, per-type Stage order, and
  newest-first activity ties while retaining category, status-filter, and Hide done assertions.
- Svelte check reports zero errors and warnings; the complete frontend unit script passes.
- Initial independent Codex review found the pre-manifest fallback/timing gap. Workspace now treats the
  manifest as a required resource, the browser test waits on a manifest-backed project section, and
  corrected-diff review session `019f60c1-64fe-7270-8baf-499a4b3a42e6` reports `NO VIOLATIONS`.
- The branch-level `./verify` passed before integration. Main advanced with unrelated Chat work, so the
  generated bundle was rebuilt during the merge; focused Svelte and browser checks passed, and fresh
  integration review session `019f621d-46b1-7913-a11b-ba8d7ba18da4` reports `NO VIOLATIONS`.
- The one post-merge canonical `./verify` passes Ruff; mypy across 115 source files; 791 unit tests;
  compile, static, CSS, and frontend checks; production build; frontend tests; and 94 Playwright tests,
  ending `VERIFY: PASS`. The full transcript is
  `[post-merge verify transcript](/files/tickets/t_mw2vegjw/artifacts/verify-closeout.txt)`.

Next step:

- Propose Closeout for approval. No deploy, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): managed HTML sibling assets

Current build stage:

- Ticket `t_z79gfuuf` is integrated into `main` by merge commit `5772978`; the ticket branch and isolated
  worktree are removed. Unrelated owner note edits and the nested frontend worktree were preserved across
  integration and remain uncommitted.
- Both managed HTML preview lifecycles now prepare fetched HTML through one shared absolute managed-base
  helper before assigning it to embedded `srcdoc` or the full-page Blob URL. The iframe sandbox remains
  exactly `allow-scripts`; fetch cancellation, source cleanup, and Blob revocation are unchanged.
- The focused Playwright fixture covers relative and root-relative images and stylesheets in both preview
  surfaces. The frontend system doc describes the same contract.

What just passed:

- The new focused browser case passes after the shared base helper was added, including the corrected
  HTML-aware insertion case with a false `<head>` token before the real document head. The complete frontend
  unit script and Svelte check also pass.
- The first independent review found that raw string matching could mistake `<head>` text inside a comment
  for the real document head. The helper now parses HTML, prepends the managed base to the actual head, and
  serializes the document; the new false-token regression passes. Fresh corrected-diff Codex review reports
  `NO VIOLATIONS`.
- The canonical branch gate passes Ruff; mypy across 115 source files; 760 unit tests; compile/static, CSS,
  and frontend checks; production build; all frontend tests; and 91 Playwright tests, ending `VERIFY: PASS`.
- The post-merge canonical gate on `main` passes the same complete set with 760 unit and 91 Playwright tests,
  ending `VERIFY: PASS`. The retained transcript and ticket artifact are
  `data/verify/t_z79gfuuf-main-pass.log` and
  `[full verify transcript](/files/tickets/t_z79gfuuf/artifacts/verify.txt)`.

Next step:

- Propose Closeout for approval. No deploy, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): slate-blue brand accent

Current build stage:

- Ticket `t_bbzgswpj` is integrated into `main` by merge commit `b479e5b`; the ticket branch and worktree are
  removed. The four-token replacement and its focused regression were small enough to implement directly
  rather than dispatching a second ticket. Unrelated active owner edits were restored after the merge.
- The owner selected Option A — Soft Steel. The shared brand family is now `#9aadd2` bright,
  `#222a38` surface, `#dce6f8` text, and `#111318` ink. Green done and red error tokens are unchanged;
  no component layout or interaction styling changed.

What just passed:

- The focused Playwright regression failed first against the amber token family, then passes 2/2 on desktop
  and mobile after the shared-token change. It checks the exact family, navigation, waiting, pending,
  approval, link, hover, focus, selected, done, and error treatments.
- Real ticket screenshots were captured at 1440×1000 and 390×844 with no browser console or page errors.
  Contrast ratios are 7.75:1 for bright-on-base, 8.21:1 for ink-on-bright, and 11.48:1 for accent text on
  the accent surface.
- Independent Codex review found two concrete gaps: the focused test omitted the explicit current-waiting
  stage mark, and live CSS comments still named the retired amber accent. The waiting assertion was added and
  mutation-checked across both viewports; every stale live comment was corrected. Final fresh review reports
  `NO VIOLATIONS`.
- The first canonical gate passed mypy across 115 files, 760 unit tests, compile/static checks, the complete
  frontend gate, and 90 Playwright tests. It failed only Ruff on seven overlong lines in the new test. The
  test was reformatted, the two embedded JavaScript strings were wrapped, and focused Ruff plus both browser
  cases are green.
- The corrected branch passes the complete canonical gate: Ruff; mypy across 115 files; 760 unit tests;
  compile/static and CSS checks; Svelte check with zero errors and warnings; production build; frontend
  tests; 90 Playwright tests; final `VERIFY: PASS`. The transcript is
  `data/verify/t_bbzgswpj-pass.log`.

Next step:

- Await Closeout approval. No merge, deploy, restart, migration, or follow-up work remains.

Blockers:

- None.

## Current work cycle (2026-07-14): architecture deepening integration

Current build stage:

- The reviewed architecture head `9e51df2` is integrated with restart-recovery main `49660f5` through merge
  commit `2384159`; the import-order repair is `b574046`, and the verification record is `c5279d6`. Main is
  fast-forwarded to `c5279d6`. No push or pull request was created.
- The public and domain shape follows the architecture branch: required immutable Worker type, direct
  stored Stage reads, Automatic Employee-step eligibility, Ticket-focused Review, canonical Panels Chat,
  and explicit Employee session history. Retired readiness names, Ticket Chat identity, generic Chat
  transport bags, old routes, and live `coding` defaults remain deleted.
- The source integration is complete. Restart recovery now uses the same
  architecture owners: canonical first-wins Chat settlement, strict typed gateway resume, Employee session
  id transitions, and the renamed discovery/eligibility runtime. Startup recovers ordinary human Chat and
  running Employee steps before automatic discovery; post-handoff worker turns settle without re-prompting;
  one absolute deadline is shared through runtime and gateway shutdown.
- Focused backend tests for Chat, Employee recovery, loop composition, gateways, session history,
  discovery, eligibility wake, config, the closed Chat ingress, and Worker-type/migration boundaries are
  green. Both named Playwright cases pass; compile, Ruff, mypy, the retired-surface scans, conflict-marker
  scan, and diff check pass. Independent corrected-diff review reports `NO VIOLATIONS`.
- The first fresh merge-diff review found one High omission: no-session Employee restart recovery errored
  the Ticket but left its stale visible worker turn running. The accepted repair now settles that worker turn
  through canonical Chat settlement with the same exact error before the canonical Ticket transition, with
  no replacement turn or gateway call. Its focused regression and the complete Employee-runner/core-loop
  pair pass; Ruff, focused mypy, compileall, and diff check pass. Fresh corrected-diff review session
  `019f5ffb-6b3b-7310-9f2f-c8e95b908436` reports `NO VIOLATIONS`. The merge commit and canonical gate remain.
- Merge commit `2384159` was created. Its first canonical gate completed all suites and found only one Ruff
  import-order failure in `tests/typing/tt02b_field_seam_cases.py`; mypy passed across 115 source files,
  760 unit tests passed, the complete frontend gate passed, and 88 Playwright tests passed. The import-only
  repair was applied directly as trivial integration glue and committed at `b574046`.
- The corrected integration head `b574046` passes the canonical gate: Ruff; mypy across 115 source files;
  760 unit tests; compile/static and CSS/Markdown checks; Svelte check with zero errors and zero warnings;
  production build; frontend tests; 88 Playwright tests; final `VERIFY: PASS`. The full transcript is retained
  at `data/verify/architecture-merge-pass.log`.
- The actual main checkout initially retained ignored Python bytecode under the deleted `ticket_types`
  directory. Removing only that stale cache made the deletion boundary honest. The final main checkout then
  passed the same complete canonical gate with 760 unit and 88 Playwright tests; final `VERIFY: PASS`. Its
  transcript is `data/verify/architecture-deepening-merged-pass.log`. Owner note edits and the unrelated
  nested frontend worktree remain uncommitted; the exact pre-merge note snapshot remains in the named stash.

- Shared understanding is confirmed for all six architecture candidates. Implementation is isolated on
  branch `codex/architecture-deepening` in
  `/Users/khushaljagota/.hermes/planning-v2-worktrees/architecture-deepening`, based on current `main` at
  `b7ca44a`.
- The resolved domain model is Worker type, stored Stage, Review, Panels Chat, Employee session history,
  and Automatic Employee-step eligibility. Worker type is required and immutable; reading Stage is direct.
- Landing order is Worker workflow → Automatic Employee-step eligibility → Review → canonical Chat turn →
  managed Markdown → resource catalogue → Employee session history. The last item is an isolated final
  commit because its restart/session behavior is the most finicky.
- AD01 is complete on the branch at `f9246d5`. The independently reviewed Worker-type/stored-Stage
  replacement follows the program-memory commit `aa1f29d` and the separately isolated test stabilization
  `afa57fd`.
- AD02 is complete on the branch at `05e2fda`. Its delegated implementation and corrected diff passed
  independent review with `NO VIOLATIONS`, and the committed checkpoint passes the canonical gate.
- AD03 is complete on the branch at `d3dcc29`. Membership-only discovery and the final
  `BEGIN IMMEDIATE` claim call the same complete seven-factor function; direct revision stays separate;
  wake, lock, and shutdown ownership follow the frozen contract. The accepted missing no-gated-field stale
  regression is fixed, the qualifying read-only corrected-diff review reports `NO VIOLATIONS`, and the
  committed checkpoint passes the canonical gate.
- AD04's Ticket-only Review implementation plan is complete and contract-locked. Three review rounds found
  and corrected the file-preview selector omission, overbroad event and mutation invalidation, missing
  current-day/cold-start handling, and the Ticket-creation companion-event contradiction. The final fresh
  read-only re-review, session `019f5eca-4772-7771-8727-515fd60e285e`, reports `NO VIOLATIONS`.
- AD04 is complete on the branch at `0037933`. The initial independent diff review found two
  frontend naming/type violations: the Ticket detail still used `AnyRecord`, and a Worker-count helper still
  said Agent. Both are corrected through the delegated path. The fresh corrected-diff review, session
  `019f5ede-7f24-78a2-b59a-4a6c51223672`, reports `NO VIOLATIONS`, and the committed checkpoint passes the
  canonical gate.
- AD05's canonical human Chat-ingress ticket is defined. It deletes the three retired HTTP routes, their
  parallel service functions, synchronous adapter methods/result shapes, and the unused browser SSE client.
  The gateway `stream` method remains because it is the one transport consumed by the canonical server-owned
  turn for both messages and commands; exact `/new` remains on that path.
- AD05's delegated implementation plan is corrected and contract-locked. The first independent review found
  one omission: its focused commands did not run the existing unit and browser image suites. Both are now
  explicit unchanged preservation contracts outside the edit allowlist and run in full. Fresh re-review,
  session `019f5eed-a837-7601-820e-f0ca50188e40`, reports `NO VIOLATIONS`.
- AD05 is complete on the branch at `754ff27`. The product diff removes every retired Chat
  ingress/result/method shape, keeps the single server-owned turn, and preserves images, live turn state,
  commands, `/new`, Employee-step separation, and session-key rules. Review session
  `019f5f04-3ebd-7a50-a26c-c0a072b8738f` reports `NO VIOLATIONS`, and the committed checkpoint passes the
  canonical gate.
- AD06's deep canonical Chat-turn ticket is defined. It gives one human-turn owner the request, atomic
  admission, causal session-key binding, typed observations, transcript projection, Pause, and idempotent
  settlement. It explicitly leaves Employee delivery and AD09 history separation outside the boundary.
- AD06's first independent plan review found four accepted violations: a claim-local Chat rule would split
  AD03 eligibility; a human-only owner cannot honestly own worker-origin Pause; ordinary CAS could defeat
  literal `/new`; and the private execution value was not declared. The ticket and decisions now require one
  eight-factor eligibility rule, cross-origin visible Pause control, one-use force-fresh `/new` binding, and
  an exact private execution dataclass. A corrected delegated plan and fresh re-review are required.
- A read-only audit of the owner's explicit Worker-type invariant found no executable violation: all live
  creation paths require a stored Worker type and reads return it without fallback. Two stale docs still
  called `coding` the default; those sentences were corrected as a small integration repair. Historical
  migration/cutover classification remains the only allowed implicit coding assignment.
- AD06's corrected delegated plan passed a fresh independent Codex review with `NO VIOLATIONS` (session
  `019f5f2a-63c2-7231-bd29-038e29ca4159`). The four original findings are fully resolved, and the exact
  owner, transport, transaction, session-binding, settlement, test, deletion, and changed-path contracts
  are ready to lock before implementation dispatch.
- AD06's delegated implementation is committed at `e32dd47`. `ChatTurnLifecycle` now owns human admission
  and execution plus cross-origin visible Pause; human admission and the final Employee claim exclude one
  another under SQLite's write lock; the sole automatic decision has eight factors; causal binding makes
  Panels and the actual Hermes write use the same session; exact `/new` forces only its first fresh bind;
  and generic first-wins Chat settlement is idempotent across completion, failure, and Pause. Employee
  delivery and Ticket settlement remain separate. Independent implementation review session
  `019f5f48-af76-7030-b9d5-1939bf6ba749` reports `NO VIOLATIONS`.
- AD06 is complete. Its committed checkpoint at `70d4291` passes the canonical gate with 714 unit tests and
  80 Playwright tests; the full transcript is retained at `data/verify/ad06-pass.log`.
- AD07's Managed Markdown ticket is defined. It preserves the current continuous `contenteditable` with
  atomic previews and exact source-token serialization; one new frontend owner will concentrate hardened
  rendering, preview reconciliation, serialization, and teardown while `FilePreview` keeps target-specific
  behavior. No visual, parser, backend, resource-cache, AD08, or AD09 change is in scope.
- AD07's delegated implementation plan is complete and contract-locked. Independent review session
  `019f5f5e-5233-7741-8777-cee8b0322dda` reports `NO VIOLATIONS` after checking same-source dirty reset,
  failed-save retry, final-DOM move/deletion reconciliation, read-only preview identity, exact serialization,
  teardown, docs/assets, and the bounded allowlist. Delegated implementation is the next step.
- AD07's delegated implementation is committed at `30edca4`. One `managedMarkdown.ts` owner now holds
  hardened rendering, atomic preview islands, exact serialization, reconciliation, and teardown; the two
  wrappers keep product presentation/edit-save state; `FilePreview` keeps target-specific behavior; and the
  two old lifecycle modules are deleted. Independent implementation review session
  `019f5f6d-4892-7273-8343-51122fe517b4` reports `NO VIOLATIONS`. The canonical gate is next.
- AD07 is complete. Its committed reviewed checkpoint at `05e62a0` passes the canonical gate with 714 unit
  tests and 82 Playwright tests; the full transcript is retained at `data/verify/ad07-pass.log`.
- AD08's corrected delegated Resource Catalogue plan is complete and contract-locked. The first independent
  review found one High omission: Project-name updates were mapped only to Projects even though six cached
  aggregates embed the name. The accepted correction covers Projects, Board, today's Day, backlog Sprint
  items, Ideas, and current Sprint, plus conservatively selected opened Ticket details through a private
  catalogue-owned index. The plan also freezes the temporary Ticket-plus-Chat `chat_session_created`
  dependency. Fresh review session `019f5f81-89a3-7ba2-883b-bb68ce4022b1` reports `NO VIOLATIONS`.
- The AD08 lock retains exactly 13 cached projections, nine named mutation effects, entity-first ordinary
  invalidation, narrow Review inputs, and a semantic-free cache engine. It explicitly forbids live
  Worker-type inference/defaults; only the existing migration may rewrite a historical pre-Worker-type row
  to `coding`.
- AD08's delegated implementation is committed at `d2cc125`. One catalogue now owns all 13 cached reads,
  nine immediate mutation effects, and event dependencies; the generic cache remains semantic-free; phantom
  identities and forwarding modules are deleted; and the served bundle is current. The focused type, Node,
  unit, and 68-test browser evidence is green. Independent implementation review session
  `019f5f98-5fd1-7610-9cd5-92d2cf2a32a8` reports `NO VIOLATIONS`.
- AD08 is complete. Its committed reviewed checkpoint at `4e267c6` passes the canonical gate with 714 unit
  tests and 86 Playwright tests; the full transcript is retained at `data/verify/ad08-pass.log`.
- AD09's delegated Employee-session-history plan is corrected and contract-locked. The first independent
  review found that the plan still blessed the retained live seed importer as a `coding` default. The
  correction makes Worker type a required seed command/programmatic argument and passes it through exactly;
  only the terminal v20 migration may classify a genuinely old row with no stored Worker type as `coding`.
  Fresh review session `019f5fa7-9f49-7322-b33d-a91ccee643f1` reports `NO VIOLATIONS`.
- AD09's isolated product implementation is committed at `0e0bf06`. The first implementation review found
  two real violations: legacy Ticket `chat_session_created` still reached Ticket aggregates, and the
  required browser proof against silently repopulating deleted Panels rows from retained Employee history
  was missing. Both were corrected through delegation. Fresh review session
  `019f5fc8-c093-7163-8bee-2b15db5d19ea` reports `NO VIOLATIONS`; the canonical gate is next.
- AD09 is complete. Its committed reviewed checkpoint at `acf93cd` passes the canonical gate with 738 unit
  tests and 87 Playwright tests; the full transcript is retained at `data/verify/ad09-pass.log`. All nine
  architecture-deepening stages are now complete on the branch.
- The independent whole-program review of `b7ca44a..191f14a` is complete. It inspected the AD01–AD09
  contracts and full branch diff, including live Worker-type ingress, migration-only historical rewrites,
  resource dependencies, generated assets, API deletion seams, Markdown ownership, and Employee-history
  separation. Session `019f5fcf-cfa9-73d0-8741-27e7f687900a` reports `NO VIOLATIONS`; the exact record is
  `orchestration/tickets/architecture-deepening/final-review.txt`.
- The complete reviewed branch passes its final canonical gate at `2982197`: Ruff; mypy across 115 source
  files; 738 unit tests; compile/static and CSS/Markdown checks; Svelte check with zero errors and zero
  warnings; production build; frontend tests; 87 Playwright tests; final `VERIFY: PASS`. The full transcript
  is retained at `data/verify/architecture-deepening-final-pass.log`.

What just passed:

- The architecture report passed the prior canonical `./verify` with 654 unit and 70 e2e tests. The design
  interview resolved deletion posture, domain language, behavior-preservation constraints, refresh policy,
  catalogue scope, and the explicit separation between Panels Chat and Employee session history.
- The new branch and worktree were created without carrying unrelated dirty files from the original
  worktree.
- AD01 now has a delegated implementation plan covering the exact public vocabulary, a v18 all-shape
  migration, historical event rewriting, RED/static tests, and a bounded implementation allowlist. The
  worktree was moved outside the source tree before implementation so it does not appear as an untracked
  child of `main`.
- Independent read-only plan review found two violations: the plan still passed ancient schemas through
  pre-lock lifecycle/kickoff rebuilds, and it omitted the live coding-worker skill from its allowlist. Both
  findings are accepted. The delegated revision is consolidating every recognized Ticket schema into one
  lock-held terminal rebuild and adding the missing skill as a vocabulary-only edit.
- The corrected plan passed the independent read-only re-review with `NO VIOLATIONS`. Its exact naming map,
  consolidated migration, behavior-preservation tests, and implementation allowlist are now the locked AD01
  contract.
- The orchestrator generated the AD01 contract skeleton in the five Python/TypeScript contract files plus
  the canonical v18 SQLite DDL and indexes. `contract-lock.md` records the exact declarations; consumer and
  migration implementation is now ready for delegated work. The tree is intentionally RED until consumers
  are rewired, so no canonical `./verify` has been run.
- The delegated implementation has made the new Worker-type/Stage contract test green (3 tests) and wired
  the core Ticket data, API, view, resolution, machine, manifest, event, and v18 migration paths. A bounded
  allowlist gap was found in two direct-creation test fixtures; those two test paths are now explicitly in
  scope for only `worker_type="coding"` fixture arguments. No production boundary or locked contract changed.
- A broad unit pass found two further legacy HTTP-create fixtures in the project and worker-command tests.
  Their paths are added for only the old create key → `worker_type` update; no tested behaviour changes.
- Orchestrator spot-checking found the Ticket-list boundary still planned a Worker-type parameter solely to
  interpret a Stage filter. This conflicts with the owner ruling that Stage reads use the stored value
  directly. The plan is corrected: list filtering accepts only `stage`; the list query/CLI Worker-type
  disambiguation surface is deleted. Creation remains the Worker-type choice point.
- A frontend boundary scan found `ApprovalBlock.newState` feeding only Ticket lifecycle scope. The
  component is added to the bounded allowlist for the exact `newState` → `newStage` prop rename; no alias
  remains and its rendering/approval behaviour is unchanged.
- Focused implementation evidence is now green: the Worker-type/Stage contract, manifest, ingress, and
  persistence set passes 37 tests; `tests/unit/test_db.py` passes 37 migration tests; mypy passes across
  120 files; and the frontend passes `svelte-check`, its Node tests, and a production build (three existing
  Svelte warnings only). The canonical `./verify` remains intentionally unrun until independent diff review.
- Migration spot-checking added byte-preservation for corrupt post-kickoff field JSON, explicit rollback on
  NULL required inputs, both one-column partial shapes, real `new_worker` values, historical event recovery,
  event rollback, repeated-open idempotence, and an assertion that no Ticket row is read before the v18 lock.
- A final direct-read audit found `_row_to_ticket` still resolving Worker type to decode fields. The codec is
  now explicitly in scope: generic no-definition decoding reads stored slots as stored, so plain Ticket reads
  need no Registry lookup. Definition-supplied audit/interpretation and every write door stay validated.
- The live-vocabulary scan found one unused exported sprint blocker helper whose interface still spoke in
  Ticket States. It duplicates the canonical link summary and has no callers, so it is deleted rather than
  renamed; one day-membership comment is corrected to say the Ticket itself is untouched.
- The delegated AD01 implementation is complete. Pre-review checks pass: 699 unit tests, Ruff, mypy over
  119 source files, frontend `svelte-check` (three existing warnings), frontend tests, production build,
  documentation rename assertion, and `git diff --check`. Generated `web/dist` output is clean. These are
  focused/pre-review checks, not the canonical completeness claim.
- Independent implementation review found two migration violations: an existing `stage` in a partial
  schema was still passed through the legacy `state` conversion, and nullable legacy `status` was rejected
  instead of taking the reviewed `else empty` path. Both are accepted and delegated for focused regression
  fixes. The review's path-scope finding is also accepted: orchestrator-owned domain and memory files will
  be committed separately from the bounded AD01 implementation diff.
- Both migration findings are fixed. Lifecycle Stage, ceiling, and field conversion now runs only from a
  legacy `state` source; an existing `stage` and ceiling are copied exactly. Legacy NULL and unknown
  `status` values map to `empty`, while canonical NULL `ticket_status` remains rejected. The expanded
  migration suite passes 38 tests, Ruff passes on both touched files, and `git diff --check` is clean.
- The domain and current-cycle memory files are isolated in an architecture-program commit, removing
  them from the bounded AD01 implementation diff. The independent corrected-diff re-review is running from
  that base.
- Corrected-diff re-review confirmed both migration fixes, then found one High shipping violation: FastAPI
  serves checked-in `web/dist`, but the delegated frontend build had reverted its generated output, leaving
  the old route and response contract live. The finding is accepted. Generated `web/dist/index.html` and
  its hashed JavaScript add/delete are now explicitly build-only paths in the bounded allowlist and are
  regenerated from the reviewed Svelte source.
- The final corrected-diff review reports `NO VIOLATIONS`: the migration fixes, immutable Worker type
  boundary, direct stored-Stage reads/filtering, retired public vocabulary removal, served bundle, and
  changed-path allowlist are clean.
- The first canonical `./verify` execution passed Ruff, mypy over 119 source files, 700 unit tests, compile
  and static checks, Svelte check/build/tests, then failed 13 of 77 e2e tests. Every failure is the same
  missed fixture rename in two already-allowed files: direct test setup still executes `UPDATE tickets SET
  state = ?` after the canonical column became `stage`. No product assertion ran or failed in those cases.
  A fixture-only repair is delegated; the gate must be rerun after focused review.
- The two stale-Stage e2e helpers are repaired and independently reviewed with `NO VIOLATIONS`; their
  focused browser set passes 14/14. The next full gate passed every prior layer and those 13 cases, then
  exposed one unrelated chat-follow fixture race (`scrollTop` returned to 1436 instead of staying 0).
- Bug-diagnosis proved the chat failure is test synchronization, not AD01 product behavior: the unchanged
  exact test passed 10/10 naturally; delaying native scroll delivery reproduced the exact failure 3/3;
  synchronously dispatching the scroll event passed 3/3 target-reaching controls. `ChatPanel.svelte` is
  unchanged. The one-line fixture fix passes the focused test, Ruff, diff-check, and independent review
  with `NO VIOLATIONS`; it is committed separately as trivial test stabilization at `afa57fd`.
- The post-fix canonical `PYTHONPATH="$PWD/src" ./verify` is clean: Ruff; mypy across 119 source files;
  700 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three existing
  warnings), production build, frontend tests; 77 Playwright e2e tests; final `VERIFY: PASS`. The full
  transcript is retained at `data/verify/ad01-pass.log`.
- AD02's delegated plan mapped the behavior-bearing definition, narrow registry, complete call-site
  threading, deletion set, schema-v19 rebuild, tests, and bounded allowlist. A separate read-only runtime
  audit found the hidden seed, CLI, field-codec, creation, read-model, and import-graph edges before work.
- Independent plan review found two High violations: seed still duplicated coding's complete field set,
  and Chief's CLI could not carry novel Worker-type fields. Both are accepted and corrected. Seed now
  derives its complete slot map from the resolved definition; Chief gains generic repeated field input.
- The corrected AD02 plan also deletes the shallow guard instead of renaming it and puts Ticket-position
  validation on `WorkerTypeDefinition`. Independent re-review reports `NO VIOLATIONS`; the review record
  and exact contract lock are in the AD02 ticket directory.
- Implementation found one bounded allowlist omission: the required extra-definition-field seed proof
  belongs in `tests/unit/test_seed.py`. That test-only path is added; no product boundary or locked shape
  changes.
- A final static wording scan found `tests/support/__init__.py` still used “ticket type” and claimed
  production was coding-only. It is added for that docstring-only correction; executable support stays
  unchanged.
- The delegated AD02 implementation replaces the forwarding stack with one immutable
  `WorkerTypeDefinition`, a narrow registry, and explicit production/test configuration. All semantic
  rules require a resolved definition; direct stored Stage and field reads remain registry-free. The old
  `ticket_types` package, coding bridge, guard, coding enums/tables, and optional-definition paths are
  deleted.
- Schema v19 rebuilds recognized historical Ticket tables under the existing lock-held migration envelope,
  preserves stored field JSON, and removes SQLite's coding-shaped `fields` default. Sanctioned live creation
  must supply Worker type and derives every slot from that definition. A migration may classify an old row
  without a stored Worker type as `coding`; that is historical data conversion, never a live default.
- Focused implementation evidence is green: Ruff and diff-check pass; mypy passes across 114 source files;
  650 unit tests pass; the focused database suite passes 60 tests; the affected browser suite passes; and
  the production frontend build is byte-identical. These are pre-gate checks, not the completeness claim.
- Independent implementation review found one High transport violation: generic Chief `--field-file`
  entries could overwrite fixed request keys. The accepted correction rejects command-specific reserved
  keys before reading a file or sending a request, while leaving Worker-type field validity with the API.
  Focused CLI browser tests pass 6/6, including every reserved key and a create-only name reaching the
  reconcile API. Corrected-diff re-review reports `NO VIOLATIONS`.
- The committed AD02 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 650 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad02-pass.log`. An initial invocation exited before
  all gates because the external worktree's `.venv` link was absent; its separate startup transcript is
  `data/verify/ad02-startup-failure.log`, and restoring/removing the local link changed no tracked file.
- AD03's delegated plan inventories the complete eligibility, discovery, wake, runner, transactional claim,
  runtime composition, action, test, typing, instruction, and documentation surfaces. Independent review
  found one Medium allowlist omission: `config.yaml` still used “Ticket readiness” in a live line-9
  comment. The correction adds only that comment, includes it in the static vocabulary guard, and makes
  wake-specific test-double naming explicit. Corrected-plan re-review reports `NO VIOLATIONS`; the exact
  interfaces are frozen in the AD03 contract lock.
- AD03 implementation pre-review evidence is green: the affected integrated unit set passes 232 tests;
  Ruff, strict mypy over 114 source files, compile checks, docs consistency, the exact changed-path audit,
  `git diff --check`, and the empty-index check all pass. These are focused checks, not the canonical
  completeness claim. Independent reviewer session `019f5ea0-46f2-76e1-a78f-cae334227de9` found only the
  missing stale no-gated-field downstream-side-effect regression; no production-path violation was found.
- The accepted AD03 test gap is corrected without production changes. The stale-claim matrix now supplies a
  test-only ungated non-terminal definition at the final claim seam while leaving the exact shared function
  untouched, and proves no status/event, Panels Chat message/turn, gateway/prompt, or wake. The focused
  correction plus function-identity proof passes 2 tests; Ruff and diff checks pass.
- The qualifying fresh read-only corrected-diff review, session
  `019f5ea6-d03f-7bc1-b527-f334a3f594d5`, reports `NO VIOLATIONS`. It reconfirmed all seven factors,
  transaction timing, direct revision, wake/composition/shutdown ownership, deleted compatibility names,
  bounded paths, timer backstop, and docs parity.
- The committed AD03 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 687 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad03-pass.log`.
- AD04 now has an exact backend/TypeScript response, Ticket-only UI, event/mutation invalidation, deletion,
  preservation, test, and changed-path contract in `ad04-ticket-only-review/contract-lock.md`. Its final
  correction explicitly allows only migration-time historical Worker-type classification: live Ticket
  creation and all canonical storage continue to require an explicit Worker type with no coding default.
- AD04 focused implementation evidence is green: the affected Python set passes 90 tests; frontend checks,
  tests, and production build pass with zero errors and the three existing Ticket-route warnings; the
  affected browser set passes after its stale selector was corrected; Ruff, mypy over 114 source files,
  CSS syntax, and `git diff --check` pass. These remain pre-gate checks, not the completeness claim.
- The independent AD04 implementation review's accepted `AnyRecord` and Agent-helper findings are fixed.
  The regenerated served bundle points to `index-DzbUvGPe.js`, and the corrected full diff has no review
  violations.
- The committed AD04 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 692 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad04-pass.log`.
- AD05's inventory covers every legacy route, service, result type, protocol method, production/test adapter,
  browser helper, and affected test. The reviewed replacement preserves the exact `/turns ->
  start_human_turn -> GatewayAdapter.stream` path, session-key rules, Panels state, command behavior, and
  literal `/new` transition without retaining a synchronous compatibility shape. The orchestrator generated
  the two contract declarations and exact lock; consumer code is intentionally RED until delegated rewiring.
- AD05 focused implementation evidence is green: its static contract passes 4 tests; the affected unit
  bundles pass 212 and 131 tests; Ruff and mypy over 114 source files pass; frontend checking, tests, and
  production build pass with the three existing Ticket-route warnings; the complete unit/browser image
  preservation suites, live Chat-state suite, and affected message/command browser flows pass. The
  independent review also accepted the narrow lost-first-write winner correction as required preservation,
  not AD06 scope.
- The committed AD05 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 687 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad05-pass.log`.
- AD06 focused evidence is green: 284 affected unit tests, Ruff over every changed production/test path,
  targeted mypy over all ten changed production modules, compileall, and `git diff --check`. The focused
  contract tests include real two-connection settlement races in both orders, both human-admission /
  Employee-claim lock orders, and actual Hermes session-id assertions for initial and dormant message,
  image, command, and alias writes. These are pre-gate checks, not the completeness claim.
- The independent AD06 implementation review inspected the complete changed path set plus the new untracked
  test before commit and returned exactly `NO VIOLATIONS`. Its transcript and disposition are recorded in
  `orchestration/tickets/architecture-deepening/ad06-deep-canonical-chat-turn/implementation-review.txt`.
- The committed AD06 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 714 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad06-pass.log`.
- AD07's exact owner API, lifecycle state, wrapper boundary, failed-save retry, serializer preservation,
  preview reconciliation, deletion set, test sequence, docs/bundle work, and changed-path allowlist passed
  independent plan review. The lock is recorded in
  `orchestration/tickets/architecture-deepening/ad07-managed-markdown/contract-lock.md`.
- AD07 focused implementation evidence is green: frontend tests pass; Svelte check has zero errors and the
  three existing Ticket-route warnings; both new browser regressions pass; all 13 file-preview browser tests
  pass; and `git diff --check` is clean. The independent reviewer inspected the complete source, test, docs,
  and served-bundle diff and returned exactly `NO VIOLATIONS`. These are pre-gate checks, not the canonical
  completeness claim.
- The committed AD07 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 714 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 82 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad07-pass.log`.

Next step:

- Merge the clean, reviewed, canonically verified branch serially to `main`, then verify the integrated
  checkout.
- The owner has authorized merging only after AD09 and the complete branch pass final review and canonical
  verification; no partial program merge or push is authorized.
- The owner confirmed AD02 has no implicit live defaults at any layer. SQLite's coding-shaped `fields`
  default is removed by the lock-held v19 forward migration. Migrations may rewrite old tables and assign
  `coding` only when converting a historical row that predates stored Worker type; existing field JSON and
  related data remain preserved.

Blockers:

- None.

## Current work cycle (2026-07-13): safe interactivity in ticket HTML previews

Current build stage:

- Ticket `t_7uphhqfj` is integrated into `main` at `6f36d72`; the ticket worktree and branch are
  removed. No deploy, restart, migration, or follow-up ticket applies. Both embedded and full HTML
  previews consume one `allow-scripts`-only sandbox contract; same-origin and every other sandbox
  privilege remain absent.
- The copied Workspace-row reproduction is now a deterministic browser fixture. Its script-cloned rows
  paint at non-zero geometry and its selector changes the visible variant in both preview surfaces.

What just passed:

- The new Playwright test failed first against the empty sandbox, then passed after the shared policy
  change. The complete ticket-file preview browser file passes 11/11.
- Independent Codex review reported `NO VIOLATIONS` against the approved sandbox, interaction, paint,
  and regression contract.
- Canonical pre-merge `PYTHONPATH="$PWD/src" ./verify` passed Ruff, mypy across 119 source files,
  655 unit tests, frontend checks/build/tests, and 75 Playwright e2e tests.
- Canonical post-integration `PYTHONPATH="$PWD/src" ./verify` on `main` passed the same complete gate;
  final `VERIFY: PASS`.
- The unrelated active `CONTEXT.md`, `PROGRESS.md`, `decisions.md`, and nested-worktree edits were
  restored and remain unstaged.

Next step:

- Propose Closeout for approval. No repository or operational work remains.

Blockers:

- None.

## Current work cycle (2026-07-13): shared scrollbar polish

Current build stage:

- Ticket `t_ccpejh0c` Implementation is complete and verified in isolated worktree
  `/private/tmp/panels-t_ccpejh0c` on `ticket/t_ccpejh0c-scrollbars`; it is ready to propose.
- The CSS-only shared treatment reserves stable gutters on all seven owned scroll surfaces, keeps
  visible native behavior for touch and forced colors, and hides/reveals the thumb only for fine
  pointers outside forced-colors mode.
- Codex plan review found six concrete constraints; all are implemented in
  [D-shared-scrollbar-treatment]: unwind the existing unconditional chat hide, cover all seven
  Panels overflow surfaces, use `:focus-within` rather than assuming focusable containers, test real
  non-hover and forced-colors contexts, avoid screenshot/pixel assertions, and reuse tokens.

What just passed:

- Focused Playwright coverage passes 3/3 after proving RED against baseline CSS. It covers all seven
  scroll surfaces, fine-pointer rest/hover/focus/active states, a real touch context, and forced
  colors both at rest and during interaction.
- Two Codex implementation-review fix cycles were resolved: add a real forced-colors context, then
  exclude forced colors from the fine-pointer auto-hide media query. The final review has no code or
  behavior violations; its only observation was to ensure the new test file is included in the commit.
- Canonical `./verify` passes: Ruff; Mypy across 119 source files; 654 unit tests; compile/static, CSS,
  Svelte, frontend build and frontend tests; 73 Playwright e2e tests; final `VERIFY: PASS`.

Next step:

- Commit the isolated branch and propose Implementation for approval. Do not merge or deploy before
  Closeout.

Blockers:

- None.

## Current work cycle (2026-07-13): today-scoped Review closeout

Current build stage:

- Ticket `t_4ub5h4sw` is integrated into `main` by merge commit `879205a`; the reviewed ticket source
  remained byte-identical through integration alongside the already-landed scrollbar work.
- Review ticket approvals come only from the current planning day's membership, and day add/remove events
  invalidate `queues` so the Review screen, shell badge, and empty state refresh without a reload.
- The ticket worktree and branch are removed. No deploy, migration, restart, or follow-up ticket applies.

What just passed:

- Post-merge `PYTHONPATH="$PWD/src" ./verify` on the primary checkout: Ruff; mypy across 119 source/typing
  files; 655 unit tests; compile/static, CSS, Svelte check/build, six frontend test groups, and 74 e2e
  tests; final `VERIFY: PASS`.
- The tracked generated bundle still matches its HTML pointer. Unrelated active `CONTEXT.md`, `PROGRESS.md`,
  `decisions.md`, and nested-worktree edits were restored and remain unstaged with their original diff sizes.

Next step:

- Propose Closeout for approval. No repository or operational work remains.

Blockers:

- None.

## Current work cycle (2026-07-13): chat preview stability closeout

Current build stage:

- Ticket `t_svy3xjxj` accepted Implementation. Its current-main artifact is integrated by merge commit
  `4579a6a`, preserving Job B, typed tickets, multi-image chat, preview de-flaking, and the unrelated dirty
  nested frontend worktree.
- The implementation keys chat rows by durable message/turn identity and preserves the shared
  `MarkdownBlock` preview subtree only while source and preview context are unchanged. Browser coverage
  proves all seven preview kinds in Ticket and Chief chat and still proves real-target replacement.
- The branch adopted `6fb2fac`'s compact memory format before integration; old per-ticket process logs were
  not reintroduced.

What just passed:

- Implementation artifact: focused live-chat 10/10, multi-image chat 8/8, shared previews 10/10; independent
  current-main review `NO VIOLATIONS`; canonical `./verify` with 654 unit and 70 e2e tests.
- Canonical post-merge `./verify` on `main`: Ruff; Mypy across 119 source/typing files; 654 unit tests;
  compile/static, CSS, Svelte, frontend build, six frontend test groups, and 70 e2e tests; final
  `VERIFY: PASS`.

Next step:

- Commit this concise closeout record, remove the merged worktree and branch, then propose Closeout. No
  deploy, restart, migration, or follow-up Ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-13): Job B — the `new_worker` production type (verified green, ready to commit)

Current build stage:

- Job B = the create-a-worker worker: a production-shipped `new_worker` type whose worker designs and lands
  ANOTHER worker. Design produced by the `worker-smith` sub-agent (sense-check, owner-driven); IMPLEMENTING now
  on the same agent. Full decision set = [D-ticket-types-new-worker].
- Owner-designed bespoke lifecycle `needs_kickoff → needs_stages → needs_thinking → needs_drafting →
  needs_closeout → done` (one thinking stage = the crux). Skill `panels-worker-new-worker` = coding abstracted +
  owner-lightened; approved draft persisted at scratchpad `panels-worker-new-worker-SKILL.md`, copied verbatim.
- Bundled in: (A) step-runner fix (`employee_step_runner` threads the ticket's own definition — the lone
  straggler that would crash a live new_worker step; VERIFIED LIVE); (B) no transition hooks; (C) structural
  invariant points at the production registry; and the GLOBAL kickoff-ceiling change.
- The kickoff-ceiling turned out to be a DECOUPLING, not a one-liner (worker-smith surfaced; corroborated):
  `default_ceiling` was overloaded as both the fresh-ticket start ceiling and the "first worker stage"
  threshold (recap-writable gate + sprint in-progress). Resolved (my call — Option 1): add `needs_kickoff` to
  `ceiling_range` so `default_ceiling` derives to it; add a `first_worker_stage` view and re-point recap +
  sprint at it, PRESERVING their behavior. Coding's default moves `needs_success` → `needs_kickoff`.

Build progress: IMPLEMENTATION COMPLETE + orchestrator-reviewed. Codex diff review clean after fixes; full
`./verify` PASS twice (68 e2e). Ready to commit — awaiting owner go.

- DONE: the definition, verbatim skill, registration (+facade export), delta-A step-runner fix, the ceiling
  decoupling (Option 1: `ceiling_range` now leads with `needs_kickoff`; new `first_worker_stage` view;
  recap gate + sprint in-progress + external-work seed all re-pointed at it), and all test updates.
- Ceiling decoupling turned up a THIRD hidden `default_ceiling`-as-first-worker consumer during implementation
  (external-work seed, `data.py` create_ticket_from_external_work) — caught by a failing test, re-pointed at
  `first_worker_stage`. Recap-writable, sprint-in-progress, and external-work seed behavior all PRESERVED for
  coding (they key off `first_worker_stage == needs_success`, unchanged); only the fresh-ticket start ceiling
  moved (→ `needs_kickoff`, global).
- Gates (worker-smith, NOT full ./verify): ruff clean whole-repo; mypy clean on `src/ tests/typing/` (119 files);
  654 unit tests pass; 68 e2e tests pass. Nothing committed.
- New: `src/planner/ticket_types/new_worker.py`, `skills/panels-worker-new-worker/SKILL.md`,
  `tests/unit/test_new_worker_type.py` (10 tests: golden manifest + drive-to-done + drive-to-dropped).
- CODEX ROUND (./verify PASSED but Codex found what it can't): fixed P1 — a SECOND coding-default straggler in
  `runtime/readiness.py::is_runnable` (threaded the ticket's own definition into all four machine predicates;
  blast radius was the WHOLE readiness poll, not one ticket). Ran a COMPREHENSIVE sweep of every machine/registry
  call on a non-coding runtime path — is_runnable was the only straggler; all others already thread the
  definition or are intentionally coding (board column seed). Added 2 readiness regression tests. Also: P2a SKILL
  item-2 reworded off the unsupported "default implementer" (now transition-hook framing); P2b stale comments
  fixed (`contracts.py` ManifestDict ceiling_range comment; BRIEF.md ceiling/default).

Next step:

- Commit (awaiting owner go): orchestrator review DONE — Codex diff review (P1 + 2 nits, all fixed), full
  `./verify` PASS twice, scope/ceiling + board-seed spot-checked clean. After commit, the live self-routing +
  novel-stage proof is an owner run.

Blockers:

- None. `main` green at `0595b94`; Job B verified green on top, ready to commit.

Parallel loose thread (not Job B): docs-worker parked on its `docs/ticket-types.md` + stale-doc-audit plan,
awaiting the owner's check/drive.

## Recent context (2026-07-13): Phase 5 done — the type-machinery is complete

The ticket-type machinery is code-complete and end-to-end: define a type → the board renders it → a
worker self-routes to its specialist skill. Phases 4a/4b/5 landed the type-driven backend read-models,
the web app deriving a per-type `Lifecycle` from the served manifest (`GET /api/ticket-types`), and
skill-driven worker realization (`WorkerProfile` active; `panels-worker` split into a type-agnostic base
+ per-type specialists; `skill_view` routing). Production ships coding-only; `probe` stays test-only. The
one thing NOT `./verify`-gated is the live self-routing proof (an owner run against `panels serve`).
Job B above is the payoff built on top of this. Decisions: [D-ticket-types-worker-routing] and the rest
of the ticket-types section of `decisions.md`.

<!-- migrate to docs/ticket-types.md when it lands: the ticket-types build detail below is the
     plain-language account of how the machinery was built and why. Once docs/ticket-types.md exists,
     its substance moves there and this block collapses to a one-line ledger entry. -->

## Ticket-types build detail (2026-07-10 → 07-13, held for docs migration)

The backend ticket-types machinery is N-ary end-to-end and proven. Built to a go/no-go gate through
serial tickets t_tt00–t_tt05b, each committed on green `./verify`:

- **t_tt00** (`824aa58`) — the registry contracts/logic/registry/coding package, additive-only (an AST
  allowlist proves nothing imports it yet), parity golden tests vs the live constants.
- **t_tt01** (`eeef51d`) — correctness-core parameterization: the engine is definition-driven and
  string-id-native (Tier-1) via the one seam `tickets/logic/coding_bridge.py`; Tier-2 scope/field-storage
  stays coding-bound and fails loud on a foreign definition.
- **t_tt02** (`ff8dbc9`) — the DB `ticket_type` column + migration (mirrors kickoff; lock held across
  snapshot→copy→swap). Shared `ticket_type_guard.resolve_and_validate` door; per-type default ceiling.
- **t_tt02b** (`85db3e3`) — generic field storage: `TicketFields` a frozen slot map keyed by field id;
  `Ticket.state`/`ceiling` → `str`; the full propose/accept/scope/drop drive path threaded per row.
- **t_tt02x** (`0b6a0a4`) — the canonical `tests/support/probe.py` fixture + probe type tests.
- **t_tt03** — type-driven CLI/API ingress + external-work genericization + `GET /api/ticket-types`.
  **The go/no-go gate passes:** a `probe` ticket drives `needs_kickoff → needs_alpha → needs_beta → done`
  through the real FastAPI TestClient with exact state/event assertions and no worker session — a missed
  ingress point would fail here (probe's states/fields aren't enum members).
- **t_tt04a** (`2ff4857`) + de-flake (`5c2bd0c`) — backend read-models type-driven.
- **t_tt04b** (`a236fb5`) — the web app derives per-type stages from the manifest; retired the hardcoded
  `ui.ts` lifecycle; coding renders byte-identically.
- **t_tt05** — skill-driven worker realization (see above).

Conscious relaxation (documented): coding ingress error *message* strings became per-type-aware
(codes/flow/data/events byte-identical, no test/frontend asserted the old strings). Flagged for a
possible follow-up: the shipped kickoff migration has the same latent pre-lock snapshot window that
t_tt02's migration closed.

---

## Recently landed

A one-line ledger of completed cycles. Dates are when the work landed; git carries the detail. Decision
references point into `decisions.md`.

- **2026-07-13 — Restart and crash recovery** (`49660f5`): startup continues stranded Ticket Employee
  and ordinary Chat work in the same durable Hermes session without replaying the original prompt; one
  configured deadline bounds runtime and gateway shutdown. The pre-integration gate passed 700 unit and
  78 Playwright tests. [D-runtime-restart-continuation]
- **2026-07-13 — Hosted Panels authentication boundary** (`b7ca44a`): Tailscale Serve identity and
  browser-Origin checks cover UI, APIs, assets, files, and WebSockets; ordinary HTTP clients use the
  canonical asynchronous Chief turn. The post-integration gate passed 680 unit and 77 Playwright tests.
  [D-hosted-trusted-ingress]
- **2026-07-13 — Workspace Worker-type and Stage byline** (`7e67828`): the owner-selected stacked byline
  keeps the registered Worker-type and direct stored Stage label together while preserving `StageMark`,
  grouping, filters, selection, and navigation. The post-integration gate passed 655 unit and 76 Playwright
  tests. [D-workspace-row-byline]
- **2026-07-13 — Type-aware front doors** (skill edits): `panels-chief-of-staff` + `panels-worker` now
  know ticket types and the "new worker → `new_worker` ticket" mapping; `new_worker` closeout keeps the
  lists current. [D-ticket-types-front-doors]
- **2026-07-11 — Blockers-only** (`14b0d5c`, merged): one typed blocker read model, `blocks` the only
  explicit relationship; schema-15 blocks-only migration on the live DB. [D-blockers-one-model]
- **2026-07-11 — Ordinary Kickoff stage** (merge `304734e`): Kickoff became a real gated first field
  (`FieldName.kickoff`), replacing the dedicated kickoff accept route/compound proposal. [D-lifecycle-gates]
- **2026-07-11 — Full-page HTML preview** (`5924b83`) + **chat image attachment** (merge `5ad4265`,
  bundle `9421e9e`, repair `a6b0fed`): the **Open preview** document route and ordered managed chat
  images via Hermes native vision. [D-file-preview-contract], [D-chat-images]
- **2026-07-11 — Bounded Markdown preview height** (`e4a607d`): tokenized max height on the shared
  preview. [D-file-preview-contract]
- **2026-07-10 — Chat scroll-back during expanded activity**: upward wheel = reader intent, separated
  from near-bottom distance. [D-chat-follow]
- **2026-07-10 — Expandable live agent activity** (merge `4820cfa`): the collapsed activity row expands
  into a bounded, transient active-turn timeline. [D-live-activity]
- **2026-07-10 — Sprint-planning workflow** (`t_w5mb4y2x`): the review-first `panels-sprint-planning`
  skill replaced the legacy file-based one; no cron added. [D-sprint-planning-skill]
- **2026-07-10 — Editable implementer assignment** (`t_mkkvq9qz`): one nullable typed `implementer`
  value; `khushal` selects `user_takeover` at the Plan→Implementation transition. [D-implementer-value]
- **2026-07-10 — Chief `/new` + fresh-session startup repair**: `/new` as a native Panels session
  transition; the planning DB directory owns the default Hermes home. [D-new-session],
  [D-hermes-home-default]
- **2026-07-10 — Ticket Implementation/Closeout lifecycle** (`7317ec7`, migration hardening `d8643c5`):
  `in_progress`/`needs_review` → `needs_implementation`/`needs_closeout`; `result` → `implementation` +
  `closeout` fields; the separate result-approve path deleted. [D-lifecycle-gates],
  [D-lifecycle-migration], [D-migration-boundary]
- **2026-07-10 — Rollover** (`panels-rollover`): drafts the kickoff, waits for the user before placing
  tickets; narrow `PLAN_ACTOR=chief` elevation for the scheduled kickoff draft only. [D-rollover-skill]
- **2026-07-10 — UI redesign program** (frontend-shared-components branch, `097d4ec..3dab068`): serif
  voice, UI-only, screen shapes locked; six waves + a token-discipline pass, each Codex- and
  design-reviewed. A post-merge owner fidelity pass fixed deltas the wave reviews had passed.
  [D-ui-redesign], [D-ui-fidelity]
- **2026-07-10 — Frontend consolidation** (frontend-shared-components branch, five serial tickets): one
  shared-component set; one `ApprovalBlock`; the `.select` class dropped. [D-shared-component-set]
- **2026-07-08 — Svelte root cutover**: FastAPI serves `web/dist` at `/`; the classic JS route loop
  deleted; ticket chat reads the full durable Hermes session trace. [D-svelte-canonical]
- **2026-07-06/07 — Runtime redesign (W2/W3a → employee runtime online)**: the dispatch package removed;
  `src/planner/runtime/` (step runner) added; one shared persistent gateway child per role; agent
  identity is a per-turn CLI lookup. [D-gateway-topology], [D-runtime-names]
- **2026-07-06 — Redesign waves** (Sprint Overview, Backlog/Ideas split, Board top-down): UI reshaping to
  the approved mockups. Superseded by the UI-redesign program above.
- **2026-07-04..07-06 — Original build + ticket-redesign** (`SPEC.md`-era): the full planner shipped and
  audited; `SPEC.md` later retired. The one retained impasse is the §12 snapshot contradiction.
  [D-snapshot-contradiction]

## Chat panel redesign — worktree chat-panel-redesign (2026-07-21)
Stage: wave 1 in flight. Design intent: orchestration/chat-redesign/{DESIGN.md,mockup.html,catalogue.html} (owner-reviewed mockup, all rulings applied).
Tickets: orchestration/tickets/chat-redesign/ — T1 header, T2 transcript stanzas, T4 permission (wave 1, parallel, disjoint files); T3 task pill + T5 composer (wave 2); T6 test reconciliation + full ./verify (wave 3).
Venv being built in worktree for final verify. No commits yet — owner commits.
Wave 3 complete (2026-07-21): T6 reconciled all tests (web suite + tests/support/acp_component_runtime.py + e2e selectors) and surfaced a genuine TranscriptView keying regression (duplicate user render-item keys crashed the transcript on first prompt) — fixed by orchestrator (per-part keys). Combined-diff review's blocker (transcript CSS written to main tree by T2) recovered into worktree; main tree restored clean. agent_backends npm ci was needed in the fresh worktree (22 env-only unit failures before it). Final reserved ./verify: PASS — all gates ok (ruff, mypy, unit, build check, frontend, e2e 103 passed). Branch worktree-chat-panel-redesign ready for owner commit; nothing committed.
Committed 66a2d1e on owner instruction; main merged in (b308a7f: employee-configuration work; e2e selector conflicts resolved, dist rebuilt) and fast-forwarded back into main.
Dogfood round (2026-07-21, Claude worker on t_74sa1y1j): three fixes uncommitted in worktree — (1) stanza-internal grids get min-width:0 so wide step output scrolls instead of blowing the pane; (2) failure auto-open deleted at stanza and step level (owner: failures a worker self-corrects are not worth surfacing open); (3) narration-led stanza folding in groupMessageBlocks — Claude sends narration text, not thought chunks, so an all-text content part immediately followed by tool_calls becomes the stanza's collapsed lead (streams as prose, folds when tools arrive; final answer stays prose; Hermes thought path unchanged). ./verify after: PASS (1084 unit, 105 e2e). Markdown underscore-italics mangling seen and deliberately left alone (owner ruled not an issue).

## Frontend dependency tracking cleanup (2026-07-22)
Stage: complete, awaiting commit. `web/node_modules/` is now ignored and its 3,985 generated dependency files are removed from the Git index while the local install remains intact. Focused checks prove the path is ignored, Git tracks zero files below it, and `npm --prefix web run check` passes. Worktrees and their dependencies were explicitly left untouched. Blockers: none.

## t_pz271435 — New Worker Runtime Defaults (2026-07-22)
Stage: Closeout ready for approval. Main contains the implementation and verification record through `0e7369d1`; unrelated nested worktrees remain untouched. The packaged specialist was published to the managed, runtime, and last-known-good New Worker sources, which agree semantically. Managed defaults remain Codex / `gpt-5.6-sol` / `medium`, with Runtime Defaults paired. Prospective commit `c7c56b49` passed the canonical `./verify`: Ruff, strict Mypy across 155 source files, 1,386 unit tests, compile/CSS, Svelte and production frontend gates, and 119 E2E tests (`VERIFY: PASS`). After the documented restart, the live manifest and Workers endpoint confirmed the new field, paired Stage, advance path, managed defaults, and updated specialist are active.
# Current work cycle (2026-07-23): Worker help request implementation (`t_wrdzb9jn`)

Implementation is complete in the isolated Ticket worktree
`/Users/khushaljagota/.hermes/worktrees/planning-v2-t_wrdzb9jn`, branched from current
`staging`. Backend and frontend/skill slices were integrated serially. Independent review
reported no violations. The canonical `./verify` passed all gates: 1,341 unit tests, frontend
checks/build/contracts, and 123 Playwright e2e tests (`VERIFY: PASS`). The branch is clean.
Next: propose the Implementation field for approval; Closeout will handle staging integration.
Blockers: none.
