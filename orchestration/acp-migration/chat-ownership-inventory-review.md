# ACP-06 chat ownership inventory — independent review

Verdict: **NOT READY**. The inventory is close, but the following ownership gaps must be resolved
before it can become the ACP-06 classification artifact.

## Concrete omissions

1. **`pending_worker_context` is assigned to the wrong owner and contradicts its own class.** The
   inventory puts it under “product worker-step settlement only,” whose definition forbids submitting
   a worker prompt, and repeatedly says to compose it into `EmployeeStepRunner`. The settled ACP-05
   contract instead retains `planner.worker_context` and makes
   `AcpStepGateway.run_ticket_step`—injected by `ConversationComposition`—perform the exact
   prepare/admit/acknowledge sequence that places the text in the real ACP prompt. Classify this as
   worker-context/ACP prompt delivery, not a transcript or settlement record. Deletion of
   `SharedGateway` must be gated specifically on the ACP-05 gateway ordering, pre-admission retention,
   acknowledgement-failure, composition-injection, and real automatic-step delivery tests passing.

2. **The retained automatic Employee-step record has no frozen non-chat owner.** The rows still say
   “candidate minimum,” “such as,” “if Ticket deletion semantics require it,” and permit a recovery
   link only if later proved. Name the destination module/repository, table and columns, event kind,
   migration/terminal-cleanup behavior, and exact callers before classification. Otherwise ACP-06 can
   delete `chat_turns` without an implementable replacement for admission, restart recovery,
   permission epoch proof, automatic eligibility, Ticket transition/deletion guards, and terminal
   settlement.

3. **The legacy worker-identity fallback surface is absent.** Classify
   `GET /api/tickets/by-live-session/{live_session_id}` (which calls the deleted adapter gateway),
   `GET /api/tickets/by-employee-session/{employee_session_id}`, the Hermes-session fallback in
   `cli/main.py::worker_my_ticket`, and their environment reads. ACP ticket children carry
   `PLAN_TICKET_ID`, so the no-compatibility cutover can delete those fallbacks while retaining
   `/tickets/{ticket_id}/worker-self` and its Ticket ownership check. Rewrite the corresponding cases
   in `test_worker_my_ticket.py`, `test_worker_cli_identity.py`, `test_cli_entrypoints.py`, and
   `test_chat_ingress_contract.py`.

4. **Ticket conversation-binding ownership is only referenced indirectly.** Explicitly classify
   `tickets.employee_session_id`, `EmployeeSessionIdTransition`,
   `write_employee_session_id_in_transaction`, `employee_session_changed`, the worker-self ownership
   readers, and their resource/migration tests as the Ticket product mirror used by the ACP binding
   repository and permission guard. Also replace the unresolved “rename or fold” choice for
   `agent_chat_sessions` with one conversation-owned Chief binding design. Per the live owner ruling,
   ACP-06 should specify a one-time session reset/new-conversation cutover rather than preserve or
   backfill legacy Chief/worker sessions; `conversation_session_bindings` remains the durable owner
   for sessions created after that cutover.

5. **Frontend ownership does not include the shared stylesheet or test runner.** Classify the mixed
   selectors in `assets/app.css`: delete selectors used only by `ChatPanel`/clarification/activity,
   while retaining or moving the composer, image, transcript, Chief layout, and Ticket rail selectors
   used by ACP. Update `web/package.json`, which still invokes the neutral-pane tests the inventory
   deletes. Without both rows, the claimed frontend caller closure and build/test cleanup are false.

6. **Several mixed or legacy test families and the required documentation sweep are unaccounted
   for.** Add dispositions for `test_new_worker_public_flow.py`, the ChatPanel cases in
   `test_flows_a.py`, `test_pool_ticket_adoption.py`, the legacy gateway-wiring case in
   `test_request_identity.py`, and `test_server_shutdown_process.py` (rewrite its product shutdown
   proof against ACP plus the new worker-step record). ACP-06 also requires updates to the live docs;
   at minimum `docs/chat.md`, `docs/employee-runtime.md`, `docs/frontend.md`, `docs/systems.md`,
   `docs/systems.html`, and `docs/tickets-and-gates.md` still describe retired chat/relay ownership.

No tests were run; this was the requested read-only source/schema/test review.

## Correction disposition

1. **Resolved — worker context owner.** The authoritative classification adds a distinct
   worker-context/ACP-prompt class. `AcpStepGateway` owns prepare/exact model text/post-admission
   acknowledgement; `ConversationComposition` injects `SqliteWorkerContextService`. SharedGateway
   deletion is gated on the settled ordering, pre-admission retention, acknowledgement-failure,
   composition, real automatic-step tests, and the outer ACP-05 Computer Use pass.
2. **Resolved — non-chat Employee-step owner.** The destination is frozen as
   `runtime/employee_step_repository.py` / `SqliteEmployeeStepRepository`, table
   `employee_step_runs` with eight exact columns and one partial running index, exact callers,
   `employee_step_started`, terminal migration, restart behavior, and cascade cleanup. No candidate
   field or optional recovery link remains.
3. **Resolved — worker identity fallbacks.** Both session lookup routes, both Hermes-session CLI env
   reads, and their adapter/data ingress are DELETE. `PLAN_TICKET_ID` plus
   `/tickets/{ticket_id}/worker-self` is the sole retained path, with exact dispositions for all four
   named identity/ingress test files.
4. **Resolved — binding/mirror cutover.** Ticket mirror symbols, writer, event, worker-self reader,
   permission guard, and tests are explicit. `agent_chat_sessions` is deleted; Chief uses only
   `conversation_session_bindings`. One versioned reset clears every old binding and Ticket mirror,
   settles old running steps, and makes first later demand create a fresh generation-1 ACP session.
5. **Resolved — frontend closure.** `assets/app.css` now has explicit retained ACP/composer/layout and
   deleted ChatPanel/activity/clarification selector groups. `web/package.json` removes neutral suites,
   rewrites image coverage, and adds the surviving ACP browser suites.
6. **Resolved — mixed tests and docs.** The classification now names exact rewrite/delete behavior for
   New Worker public flow, `test_flows_a.py`, pool adoption, request-identity gateway wiring, shutdown
   process, and the identity tests. All six requested live docs plus `docs/README.md` have explicit
   ACP-06 updates.

Correction outcome: **READY AS A CONDITIONAL CLASSIFICATION**. It is authoritative for ownership and
ticket cutting, but it remains explicitly non-authorizing until ACP-05 Computer Use passes. No tests
were run and no product source was edited for this correction.
