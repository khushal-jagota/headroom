# ACP-06 legacy conversation deletion — implementation review

Verdict: **READY** after one focused review and bounded correction checks.

The integrated runtime/schema, frontend, generated build, and live documentation were reviewed
against `contract.md`, `implementation-plan.md`, `plan-review.md`, and the authoritative ownership
classification. The review looked only for concrete P0/P1 contract or correctness violations.
Intentional historical vocabulary inside the v25 migration and its tests was not treated as a live
legacy surface.

## Findings and dispositions

### 1. P1 — controlled shutdown made the next restart unrecoverable

The first reviewed tree settled the exact Employee-step row to `interrupted` during controlled
shutdown while leaving the Ticket at `agent_running_step`. Startup recovery could replace only a
`running` row, so the next boot marked the Ticket errored instead of resuming the exact durable
session.

**Resolved.** `EmployeeStepRunner` now preserves the bound running correctness row when shutdown is
in progress. The normal restart path then interrupts that old row and creates one replacement with
the same Employee session. The focused regression starts a real run, performs controlled stop,
asserts the exact running row/Ticket/session remain resumable, then recovers and proves the old row
is interrupted, the replacement completes, and the gateway receives the same session with
existing-session admission.

### 2. P1 — the destructive v25 proof matrix was incomplete

The first v24 fixture omitted an errored worker row, populated human message/activity/clarification
state, a nonmatching start event, Day and Chief legacy sessions, and a running Ticket without a
usable worker row. Its rollback proof exercised only the amended provenance-column shape and did
not prove index absence.

**Resolved.** The fixture now covers complete, errored, and running worker rows with exact
status/error/timestamp preservation; populated human transcript/activity/clarification state;
matching and nonmatching events; Day and Chief sessions; Ticket mirrors and Ticket/Chief bindings;
and an orphan running Ticket. Forced failure is parameterized across both direct pre-column v24 and
ACP-05-amended v24. Each case compares the incoming schema and all affected rows exactly, preserves
the incoming version, and proves that neither `employee_step_runs` nor its partial index survives
rollback.

### 3. P1 — v25 accepted noncanonical compaction provenance before deleting it

The first migration implementation checked only that `compaction_boundaries_json` decoded to an
array. It therefore accepted malformed boundary objects, invalid triggers, and duplicate boundary
ids even though the binding repository rejects those shapes and the cutover requires provenance to
pass canonical validation before row deletion.

**Resolved.** The canonical parser now lives with the conversation contracts and is shared by the
SQLite binding repository and v25 migration. Focused migration cases prove malformed objects,
invalid triggers, and duplicate ids all reject and restore the complete incoming database state.

### 4. P1 — rejected ACP delivery discarded the user's draft and images

The first frontend tree discarded the controller's `ok: false` result, always reported submit
success, and cleared the draft before delivery. Sending while the conversation was not ready, or
requesting unsupported Steer, therefore erased text and revoked image URLs although nothing reached
ACP.

**Resolved.** `AcpConversationPane` returns the controller result, `AcpComposer` routes rejection
through the existing error path, and `ConversationComposer` clears text and images only after an
accepted send. The mounted-browser regression proves not-ready and unsupported-Steer rejection
retain the exact text and ordered images, then proves a successful retry clears them and revokes
each object URL exactly once.

## Remaining contract audit

No unresolved P0/P1 violation remains in the reviewed boundaries:

- `SqliteEmployeeStepRepository` is the sole live SQL owner of `employee_step_runs`; migration SQL
  is confined to the atomic v25 cutover. Runtime settlement remains first-wins.
- `AcpStepGateway` still prepares pending worker context into the actual ACP model prompt and
  acknowledges exact revisions only after tracked ACP admission.
- Production composition, browser routing, configuration, identity, and file handling are ACP-only.
  The authorized Chat/raw-Hermes/relay/neutral/adapters and managed Chat-file surfaces are absent,
  while Ticket-file safety and generic/external ACP preview behavior remain.
- The ACP composer uses `available_commands_update` commands and ordered inline ACP image blocks;
  no upload or managed conversation-file fallback was reintroduced.
- The current docs describe the settled ACP system rather than a legacy alternative. The restart
  prompt's one-line wording is backend-generic (`existing employee conversation`), and the docs
  correctly present Codex/Claude registration as later work.
- The rebuilt `web/dist/index.html` points at the current `index-4-2VbDoa.js` and
  `index-B15RFj9L.css`. The generated bundle closure scan found no `/api/chat`, `/api/relay`,
  `/files/chats`, `ChatPanel`, `ChiefNeutralPane`, `neutralPane`, or `chat-file` surface.

## Focused correction evidence

- Backend correction selection: **24 passed**.
- Scoped Ruff: **passed**.
- Strict Mypy on the four corrected source files: **passed**.
- Frontend component, ACP images, and production-mount tests: **passed**.
- Svelte check: **0 errors and 0 warnings**.
- Vite production build and both scoped diff checks: **passed**.

No broad suite and no `./verify` run was used for this review. ACP-10 retains the final canonical
repository-wide verification gate.
