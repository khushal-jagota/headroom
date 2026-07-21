# ACP-07 generic Ticket employee-backend selection — focused plan review

## Verdict

**NOT READY** — two runtime/composition boundaries must be made exact before implementation.

## Findings

### P1 — Opening the real Ticket route freezes the backend before the selector can save

The plan treats a binding appearing after render as a hidden race
(`implementation-plan.md:207-217`), but current route behavior makes it deterministic. `TicketRoute`
always mounts `AcpConversation` (`web/src/routes/TicketRoute.svelte:417-422`), its pane calls
`controller.attach()` on mount (`web/src/components/acp/AcpConversationPane.svelte:79-85`), and the
browser attach path calls `registry.get_or_spawn` when no binding exists
(`src/planner/conversation/hub.py:346-382`). Registry initialization creates a new session and wins the
first binding CAS (`src/planner/conversation/employee_registry.py:386-405`). Merely viewing an ordinary
fresh `needs_kickoff` / `awaiting_approval` Ticket therefore creates the binding that makes every real
selector change reject. The promised Kickoff choice is unusable.

Required correction:

- Define the smallest explicit lazy/deferred attach boundary so route observation and selector use do
  not create a session. Keep the existing chat rail/layout restrained; do not solve this with another
  settings surface or a backend-specific branch.
- A first explicit human conversation demand may attach and freeze the selection; Kickoff advance and
  automatic demand remain valid freeze paths. Name the exact frontend/controller files needed instead
  of retaining the current prohibition on ACP component changes.
- Extend the real-route proof to create the actual fresh `awaiting_approval` Ticket with its Kickoff
  proposal, navigate to it, assert no binding/session mirror was created, change to the fake backend
  successfully, then trigger first demand and assert the binding uses that selected key and the
  control freezes. An absence-only or server-rejection assertion is insufficient.

### P1 — The plan does not connect the app catalog to the configured Worker registry used by automatic work

The plan says `create_app` creates one catalog instance for routes, startup validation, manifest, and
ACP composition (`implementation-plan.md:46-52`) while `WorkerTypeRegistry` validates defaults against
an injected catalog (`implementation-plan.md:61-65`). Current source, however, constructs the
production Worker registry eagerly before `create_app`
(`src/planner/worker_types/configuration.py:28-38`). Ticket data, the discovery loop, and
`EmployeeStepRunner` continue to read that module-global configured registry; discovery and runner do
so at `automatic_employee_step_discovery_loop.py:60-69` and `employee_step_runner.py:482-488`.
Nothing in the plan makes that registry and its test install/restore seam part of the same catalog
composition that the app and ACP repository receive. Implemented literally, defaults can be validated
against one catalog while selection, binding, and the fake automatic-step runtime use another.

Required correction:

- Name one process composition seam that constructs the backend catalog and Worker registry together,
  then supplies that paired authority to startup audit, manifest, every Ticket creation/edit ingress,
  repository/composition, and the existing configured-registry consumers.
- Make the probe/test install and restore the pair atomically; do not rely on separately coordinated
  `ConversationTestOptions`, a fresh app catalog, and the current registry-only global test seam.
- Prove the manifest default, Ticket override, first binding, human prompt, and real automatic step all
  observe the same injected two-entry catalog. This is dependency plumbing only; discovery,
  eligibility, prompt, proposal, status, and settlement rules stay unchanged.

## Other reviewed boundaries

The real fresh-Ticket `awaiting_approval` writer rule, same-value no-op, immediate-lock
writer-versus-binding CAS matrix, v26 migration placement/contradictory-binding abort, restrained
existing-pill UI, and ACP-06 → generic slice → serial catalog-registration integration order otherwise
fit the contract. No tests were run because this was a plan-only review.

## Orchestrator disposition

Both findings are accepted and the contract/plan are corrected before source dispatch.

- The real Ticket route now has one explicit deferred-first-attach boundary. A pristine Kickoff route
  renders the same rail and composer without opening a transport; first prompt is retained and sent
  once after ready, while Kickoff advance restores the ordinary eager attach. The real-route proof
  begins with the actual fresh `awaiting_approval`/Kickoff row and asserts no binding or mirror exists
  before selection.
- One immutable `ConfiguredEmployeeRuntimeDefinitions` pair now owns the backend catalog and the Worker
  registry validated against it. Production and tests install/read/restore that pair atomically;
  manifest, Ticket ingress, binding, human chat, discovery, and automatic work cannot receive separately
  configured authorities.

These are the two requested runtime/composition corrections. The remainder of the review was already
sound, so no second broad plan review is warranted. The ticket is `READY` after ACP-06 integration.
