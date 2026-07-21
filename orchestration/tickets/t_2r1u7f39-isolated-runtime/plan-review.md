# Independent implementation-plan review

Found violations.

- **High: test-mode runtime slice cannot be satisfied by the planned scrubbed env.**
  Plan refs: `implementation-plan.md:101-107`, `249-255`.
  The plan says `run` only adds DB/port/log/lock/socket/Hermes home values, but Slice 5 requires test-mode subprocesses. Existing server test mode is only enabled by `PLAN_TEST_MODE=1`; otherwise startup enters non-test runtime composition and may start real Hermes/background loops. Source: `src/planner/core/config.py:202,262`, `src/planner/core/server.py:319`, `tests/e2e/conftest.py:171-183`.
  **Correction:** add an explicit test-only run seam/option used only by tests that sets contract-owned `PLAN_TEST_MODE=1`, deterministic timing/fake-now values, and `PLAN_GATEWAY_ADAPTER=fake`, with tests proving these values come from the CLI/test seam, not ambient env or credential files.

- **High: credential-file path isolation is missing.**
  Plan refs: `implementation-plan.md:71`, `85-99`, `156-160`.
  The contract requires rejecting any staging/preview credential reference nested under live. The plan validates path overlap generally and env-file contents, but has no acceptance test that `credentials_env_file` itself is outside live and non-overlapping.
  **Correction:** include `credentials_env_file` in resolved registry path validation, live-nesting rejection, and unit tests.

- **Medium: lifecycle lease ownership is pointed at the wrong file seam.**
  Plan refs: `implementation-plan.md:27-28`, `222-233`.
  `resolve_server_lifecycle_lease_path` is in `control.py`, but the actual lease class used by `panels serve` is `PortScopedServerLifecycleLease` in `supervisor.py`. Moving stopped-check helpers into `control.py` risks duplicating or circularly importing the lease.
  **Correction:** do not edit `control.py` for lease behavior. Import/use the existing lease from `server_lifecycle.supervisor`, or explicitly widen ownership to `supervisor.py` for a narrow helper beside the class.

- **Medium: destructive lifecycle tests do not require holding the port lease through mutation.**
  Plan refs: `implementation-plan.md:231-233`.
  The plan only asserts reset/remove fail while another process holds the lease. It should also require reset/remove to hold the same lease for the full destructive critical section, avoiding a check-then-delete race with a server start.
  **Correction:** add acceptance/tests proving reset/remove acquire before any filesystem mutation and release only after success/failure cleanup.

- **Medium: fake fixture assumes Worker types are seed data.**
  Plan ref: `implementation-plan.md:124`.
  Existing Worker types/stages are immutable code definitions, not rows to create. Source: `src/planner/worker_types/contracts.py`, `src/planner/worker_types/configuration.py:21-32`, `src/planner/tickets/data.py:492-496`.
  **Correction:** seed tickets using several existing `worker_type` ids and drive representative stages/statuses through current ticket writers. Do not create Worker-type definitions or DB rows.

- **Low: required memory files are omitted from ownership.**
  Plan refs: `implementation-plan.md:15-52`.
  `AGENTS.md` requires `PROGRESS.md` every work cycle and `decisions.md` for delegated/judgment calls. The ticket contract also includes these as scoped files.
  **Correction:** explicitly add `PROGRESS.md` and `decisions.md` to the allowed implementation/memory file scope, with no unrelated orchestration edits.

Review command used model `gpt-5.5`, read-only sandbox, and high reasoning effort. The complete reviewer verdict above is preserved without omission; path links from the terminal rendering were normalized to repository-relative references.

## Corrected-plan review

**NO VIOLATIONS.**

The six findings above are fully resolved, and the independent reviewer found no remaining concrete contract/source violations.
