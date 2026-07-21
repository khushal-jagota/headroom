# ACP-10 final audit and verification

Status: **plan-only; starts after ACP-05 through ACP-08 are settled**.

## Outcome

Prove the actual settled Panels tree is the owner-approved single ACP system, not merely that its
last implementation tickets passed focused tests. The proof is one requirement ledger, one real
Computer Use pass on the production-served app, one focused independent review, legacy-absence and
backend-qualification evidence, current docs/memory, and exactly one clean canonical `./verify` on a
frozen no-writer tree.

ACP-10 does not make a backend selectable by weakening conformance. A backend is exposed only when
its exact installed adapter passes its frozen provider contract. The current accepted truth is:
Hermes is the baseline; Claude `0.60.0` and Codex `1.1.4` passed no-model runtime qualification and
must both be activated and dogfooded as functional workers; Gemini is out of scope. Codex's exact
stored-plan replay as ordinary `Plan:` prose is an accepted pinned upstream presentation limitation,
not a reason to invent a parser or withhold an otherwise functional worker. Neither provider needs
Hermes's private compaction fork or a client-visible summary.

## Start gate

Before auditing, require all of the following with no unresolved review finding:

- ACP-05's corrected Hermes Stop, Send Now, permission, compaction, reload, server-restart, Ticket
  chat, and Automatic Employee-step Computer Use proof is recorded against `127.0.0.1:8767`.
- ACP-06 has completed the schema-25 one-way deletion, Employee-step rehome, documentation rewrite,
  served bundle rebuild, absence searches, focused checks, and integrated review.
- ACP-07 has completed schema 26, the single backend/Worker-type authority, stored Ticket selection,
  restrained pristine-Kickoff selector, binding checks, and shared chat/automatic fake-backend proof.
- ACP-07 has activated exact qualified Codex through the generic seam and passed its real worker
  proof. A concrete new qualification or authenticated runtime failure is an unresolved delivery
  blocker unless the owner explicitly changes delivery.
- ACP-08 has activated exact qualified Claude through that same seam and passed its real worker
  proof. A concrete new qualification or authenticated runtime failure is likewise an unresolved
  delivery blocker unless the owner explicitly changes delivery.
- The shared activation slice registers exact order `hermes, codex, claude`, completes Claude's
  initialize-only preflight before admission, and has no unresolved review finding.
- No implementation agent is still writing product, tests, docs, or generated assets.

## Requirement audit

Create `requirement-audit.md`. Give every normative item from the implementer brief,
`orchestration/acp-migration/plan.md`, live ACP decisions, and the frozen ACP ticket contracts a
stable requirement ID. Each row records source/heading, exact requirement, authoritative code/test/
Computer Use/runtime evidence, and one verdict: `proved`, `owner-overridden`, `not applicable`, or
`unproved`. Group rows only when the same evidence proves the same invariant. “Looks consistent,” a
green narrow suite, or an intent recorded in a plan is not proof. Any `unproved` row blocks completion.

The ledger must cover at least:

- server-as-ACP-client, official SDK/stdio, thin typed browser wire, pinned typed reducer, one ordered
  ingress, durable bindings, and typed load/replay without thought-to-message flattening;
- persistent activity, typed thought/tool/diff/plan/terminal/usage/commands/images, exact permissions,
  explicit compaction, and visible Normal/Steer/Send Now/Queue/cancel outcomes;
- exact cancellation, FIFO single-delivery, permission first-settlement, five-minute compaction
  breaker and exact failure provenance, content-free compaction started/finished/failure lifecycle,
  crash/reload/restart/new-conversation recovery;
- Ticket claim -> prompt -> `AcpStepGateway` -> same selected child/session -> proposal/status, with
  pending worker context delivered to the worker and no transcript row used as model context;
- one backend catalog/Worker-type pair, Worker default, pristine-Kickoff Ticket override and freeze,
  binding equality, and the same selection for human and automatic work;
- the schema-25 legacy reset/deletion, schema-26 backend migration, sole ACP production path, current
  docs, read-only Hermes boundary, Panels visual restraint, and all owner overrides.

## Exact backend truth

Create `backend-qualification.md` from current artifacts and runtime state, not package reputation.
For each backend record exact key, executable/adapter identity and hash/version, advertised and
Panels-declared capabilities, conformance results, registration/selector presence, and real-worker
evidence.

- `hermes`: registered and proven through human Ticket chat plus an Automatic Employee step on one
  durable session.
- `claude`: exact pinned package, initialize-only preflight, truthful
  `filesystem=false`, `terminal=false`, `permission=true`, no Steer, typed internal Bash, and real
  human -> automatic -> human continuity. If these fail, it must not remain registered.
- `codex`: exact pinned package, truthful `filesystem=false`, `terminal=false`, `permission=true`, no
  Steer, same-session content-free compaction lifecycle, the accepted stored-prose/live-typed plan
  difference, and real human -> automatic -> human continuity. If a fresh runtime check contradicts
  the frozen qualification, stop and record the concrete blocker rather than adding a private-state
  shim, prose parser, fallback, or silent registration removal.
- `gemini`: absent from implementation, registration, selector, tests-as-delivery, and current docs.

## Real Computer Use matrix

Use actual Computer Use in Chrome against only `http://127.0.0.1:8767`; Vite `5189`, TestClient,
Playwright, or API-only seeding is not experiential evidence. Record actions, screenshots, visible
outcomes, backend/session/binding generations, and read-only DB/runtime corroboration in
`computer-use-evidence.md`.

1. Confirm the real Chief and Ticket panes retain the existing Panels split layout and restrained
   visual language.
2. With Hermes, exercise live/collapsed thought and hard-reload typed replay; tool, terminal, diff,
   plan, usage, commands, image, permission allow/reject, working/thinking/waiting/idle/interrupted/
   failed states, Queue pending/cancel/automatic start, Send Now, Stop, native Steer, explicit
   content-free compaction lifecycle, child death/recovery, new conversation, and server restart.
3. On one fresh disposable Ticket, prove a human prompt and a naturally discovered Automatic
   Employee step use the same selected backend/session and produce the normal proposal/status; chat
   remains usable afterward.
4. With registered Claude, create/select through the ordinary Kickoff UI and prove human -> automatic
   -> human continuity, typed internal Bash and permission, unavailable Steer, Queue/Send Now,
   compaction, hard reload, and server restart on the durable binding.
5. With registered Codex, repeat the ordinary Kickoff selection and human -> automatic -> human
   continuity proof, including typed thought/tool state, permission, unavailable Steer, Queue/Send
   Now, same-session compaction, hard reload, accepted stored-plan presentation, and server restart.

Automatic-compaction and multi-browser disconnect races may use their named deterministic fixture
proof when they cannot be induced honestly on demand, but the UI's explicit compaction, permission,
and reconnect affordances must be seen in the real app. Record that distinction; do not relabel a
fixture as Computer Use.

## Legacy deletion and documentation proof

Create `legacy-absence-evidence.md` with commands and complete outputs proving the ACP-06 deleted
packages/files/routes/tables/config keys/event writers/browser resources are absent from `src`,
`tests`, `web/src`, `web/tests`, `config.yaml`, live docs, root architecture docs, and newly built
`web/dist`. Exclude historical `orchestration/**`, `PROGRESS.md`, and `decisions.md`; list the one
sealed migration recognizer exception explicitly. Prove `/api/conversation`, worker-self,
`employee_step_runs`, and the sole binding/composition path remain.

Audit `docs/README.md` and every affected live system page for one ACP end state, exact backend truth,
selection/freeze behavior, operational install/auth needs, unsupported capabilities, and no stale
legacy alternative. Update `PROGRESS.md` and `decisions.md` with the final evidence and judgments;
history may remain history, but their current snapshot must be true.

## Review and sole canonical verification

One independent sub-agent performs one focused review of the settled tree, requirement ledger,
Computer Use evidence, backend qualification, legacy search, and docs. It spot-checks ordered ingress,
binding/CAS, cancel/FIFO, permission settlement, compaction capture/deadline, schema 25/26 migration,
configured backend/Worker authority, and `AcpStepGateway`. Record the full review and dispositions;
no unresolved violation may remain. A concrete material correction returns to its owning ticket and
invalidates this audit snapshot; do not add a ceremonial second broad review.

After review, stop every source writer and the live server, record the exact worktree/input manifest,
and permit no product/test/config/docs/generated-asset change. Run `./verify` exactly once, capture
its complete stdout/stderr with pipeline exit status, require final `VERIFY: PASS`, and retain the
full log plus SHA-256 digest. A failed run is not completion and must not be hidden by an immediate
retry. After success, only the audit report and exact result/digest lines in memory may change; prove
the verified input manifest still matches.

## Completion artifacts

- `requirement-audit.md`
- `computer-use-evidence.md`
- `backend-qualification.md`
- `legacy-absence-evidence.md`
- `independent-review.md` and `review-disposition.md`
- `verification-report.md`, the retained full verify log, and its SHA-256 digest
- current `docs/**`, `PROGRESS.md`, and `decisions.md`

ACP-10 adds no feature, compatibility path, backend shim, UI redesign, Gemini work, or Hermes-checkout
write. Any product correction discovered here is separately bounded and must settle before the final
snapshot and sole canonical verification.
