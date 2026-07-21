# ACP-07 generic Ticket employee-backend selection implementation report

Status: **COMPLETE — focused implementation review READY**.

## Outcome

ACP-07 now has one backend-neutral selection path from Worker-type configuration to the durable ACP
binding. Each Worker type declares a registered default Employee backend. Every Ticket stores the
selected key explicitly, and every creation boundary either copies the Worker-type default or accepts
one registered override. No live read or runtime path falls back to Hermes.

The implementation adds no Codex or Claude Code definition. Production still registers only Hermes;
focused tests register a second fake backend and a Worker type that defaults to the second catalog
entry to prove the system does not infer a default from registration order.

## Delivered

- Added the ordered immutable `EmployeeBackendCatalog` as the single registration authority. One
  materialization supplies the exact definition, child factory, and executability probe used by the
  conversation registry and composition.
- Replaced the registry-only test seam with one immutable `ConfiguredEmployeeRuntimeDefinitions`
  pair. Its Worker-type registry must retain the exact catalog instance it was validated against.
- Added required `default_employee_backend` to every Worker profile and exposed it with the stable
  `employee_backends` catalog through `GET /api/worker-types`.
- Added required `tickets.employee_backend` in schema v26 with no SQL default. The v25 migration
  writes literal `hermes` once, preserves every existing Ticket byte, rejects contradictory bindings,
  and refuses to repair a corrupt database already claiming v26.
- Threaded the optional registered override through ordinary API/data/action/CLI creation, Chief
  external-work creation, and the one-time seed importer. Ticket reads, Board/Day/Sprint projections,
  copy text, and creation events expose the exact stored value.
- Added the canonical `PUT /api/tickets/{id}/employee-backend` writer. It is a same-value no-op and
  permits a real change only during pristine Kickoff with no session or binding. Its immediate
  transaction and the binding CAS cannot commit a mismatched selection/binding pair in either race
  order.
- Made Ticket resolution, binding creation/replacement, backend availability, permission settlement,
  human prompts, and Automatic Employee steps use the same stored key. A missing registration,
  selected/candidate mismatch, or selected/existing-binding mismatch fails closed.
- Added the restrained existing-pill **Worker** selector to the Ticket facts line. Its options come
  only from the served catalog. Opening a pristine Kickoff Ticket does not attach; the first prompt
  attaches once through the chosen backend, while Kickoff advance enables the existing eager attach.
  Once demand or stage advancement freezes the choice, the pill is read-only.
- Updated the five live product docs for the default, stored/frozen choice, deferred attach, UI pill,
  and CLI/import overrides without claiming unregistered future backends.

## Load-bearing corrections from orchestrator spot-checks

1. Removed the old registry install/restore compatibility seam and converted its remaining callers to
   the exact atomic catalog/registry pair.
2. Added the pair identity invariant, so a registry validated against a different catalog instance is
   rejected even when the keys happen to match.
3. Tightened schema opening: only an incoming version below 26 may migrate. A database already marked
   v26 but missing, nullable, or defaulting `employee_backend` fails without a table rewrite.
4. Added a latch-controlled two-connection writer-versus-binding proof for both commit orders.
5. Corrected the fake non-Hermes E2E to select its registered override at Ticket creation before
   accepting Kickoff. It no longer uses a post-freeze SQL mutation.
6. Counted real browser requests in the selector path and required exactly one canonical backend
   mutation.

## Evidence

The settled backend/static matrix is green: scoped Ruff; strict Mypy across 44 affected source files;
356 focused unit/API/runtime tests; three real-route backend selector, Kickoff-advance, and fake
non-Hermes E2E tests; and two real-server CLI boundary tests. The second fake backend E2E proves a
human prompt and an Automatic Employee step use the same selected backend and ACP session.

The frontend lane reports all five focused Node suites green, Svelte diagnostics at zero errors and
zero warnings, and a production build to a temporary output directory. The integrated real Vite route
was then exercised by the three green Playwright cases above.

The exact commands and outcomes are recorded in `focused-checks.txt`. The old registry seam scan and
the scoped Codex/Claude definition scan are empty. Canonical `./verify` was not run; ACP-10 owns the one
final frozen-tree gate.

## Bounded caller closure and exclusions

The implementation also made the smallest required caller updates in
`tests/e2e/test_new_worker_public_flow.py`, `tests/support/acp_runtime_subject.py`, and
`src/planner/worker_types/__init__.py` so no deleted registry-only seam survived. The E2E server is
started as `python -m tests.support.acp_e2e_server`, allowing its shared test support imports to resolve
without adding another path mechanism.

There is no Chief selector, backend migration/reset action, Automatic Employee backend field,
backend-name UI branch, shared CSS/token/layout change, generated `web/dist` change, new event kind,
conversation wire change, model selector, or Codex/Claude definition in this slice. `PROGRESS.md` and
`decisions.md` remain under root-orchestrator ownership.
