# ACP migration program — Codex review round 1

Reviewer: Codex CLI, `gpt-5.5`, read-only sandbox, high reasoning  
Date: 2026-07-19  
Verdict: **NOT READY**

## Full reviewer output

### Blockers

1. **ACP-04 under-specifies automatic step cutover.**  
   Plan section: ACP-04, `plan.md:256`.  
   Evidence: current step execution is not just `run_ticket_step`; it depends on session CAS,
   worker-turn persistence, and ticket settlement in `employee_step_runner.py:552`,
   `employee_step_runner.py:702`, and `chat/service.py:667`. Existing clarification safety also
   requires the active worker turn session to match the ticket session in `chat/data.py:376`.  
   Correction: ACP-04 must add acceptance tests for `AcpStepGateway` preserving `on_session_key`
   CAS, worker-turn session attachment, completion/error/interruption ticket settlement, restart
   recovery with `require_existing_session`, shutdown interrupt, and “chat row is visibility only,
   not worker context.”

2. **The common ACP conformance suite is incomplete.**  
   Plan section: ACP-00, `plan.md:213`.  
   Evidence: research requires every backend to pass grouping fallback, durable refresh,
   steer/queue capability, compaction summary, and partial-update failure checks in
   `research.md:184`. ACP-00 only names a subset in `plan.md:215`. Hermes specifically can omit
   message IDs and return partial replay success in `research.md:127`.  
   Correction: ACP-00 should freeze the full backend conformance harness up front, including
   missing-ID grouping, same durable session after refresh, partial replay visible error,
   steer/queue capability declaration, and inspectable compaction summary.

3. **Slash-command semantics are not frozen, leaving room for legacy catalog reuse.**  
   Plan section: browser contracts / ACP-03 / ACP-04, `plan.md:102`.  
   Evidence: owner brief says commands ride ACP `available_commands_update`, not a private
   skills/catalog path, in `direction.md:64`. Research explicitly rejects `SkillView`’s private
   `_acp/skills/list` and says slash commands come from `available_commands_update` in
   `research.md:68`. Current UI hard-codes `/compact`, `/new`, `/interrupt` and adapts a native
   catalog in `ChiefNeutralPane.svelte:171`.  
   Correction: add a contract/test that ACP command palette state is sourced only from typed ACP
   command updates, per employee/session, with explicit handling for Panels-only lifecycle actions
   if any remain.

4. **Permission request contract is missing the request shape.**  
   Plan section: contracts / reverse calls and permission / ACP-02, `plan.md:153`.  
   Evidence: plan defines only `ConversationPermissionOutcome` in `plan.md:68`, while ACP
   permission requests must preserve exact option IDs/labels and settle once across disconnect,
   child death, cancel, timeout, and shutdown per `research.md:44`. Current neutral approval UI
   collapses this to approve/approve all/deny in `ChiefNeutralPane.svelte:245`, which is not
   ACP-safe.  
   Correction: ACP-00 should define `ConversationPermissionRequest` with tool-call/session
   identity, exact ACP options, labels, scope, requester, lifecycle state, and browser-owner rule;
   ACP-02 then implements against that frozen contract.

5. **ACP-06 deletion scope is too broad without a mandatory chat classification artifact.**  
   Plan section: ACP-06, `plan.md:283`.  
   Evidence: plan says not to delete `chat/` wholesale, but the deletion list includes `ChatPanel`,
   legacy API helpers/types/resources/events, and route branches in `plan.md:285`. Current `chat/`
   still owns worker-step visible records, image path resolution, clarifications, and events in
   `chat/service.py:620` and `chat/data.py:520`.  
   Correction: ACP-06 needs a precondition artifact and tests classifying every remaining `chat/`
   use: delete human conversation transport, retain or move worker-step settlement and managed
   image handling, and prove no hidden transcript transport remains.

### Optional improvements

- Pin mechanics are not explicit. `requirements.txt` has no `agent-client-protocol`, and
  `web/package.json` has no ACP donor dependency. ACP-00 should state the exact pin/vendoring
  mechanism for the Python SDK and `acp-components/core`.
- Add a negative frontend regression against the old silent-drop habit. Current `neutralPane.ts`
  ignores unknown kinds; ACP-03 says unknown updates are tested, but the expected visible error
  should be named.

### Verdict

**NOT READY**

The direction is largely correct and respects the `acp-ui` behavior-only ruling, but the ticket
graph still leaves several load-bearing contracts for implementers to invent.

## Disposition

All findings are accepted.

| Finding | Disposition in `plan.md` |
| --- | --- |
| Automatic step cutover | ACP-04 now names the full session-CAS, product worker-turn, ticket settlement, recovery, and shutdown matrix. |
| Common conformance gaps | ACP-00 now freezes all ten probes from `research.md` as a backend registration gate. |
| Command provenance | Browser/runtime contracts now state that palette entries come only from per-session `available_commands_update`; stop/new are separate Panels controls and no legacy/private catalog is allowed. |
| Permission request shape | `ConversationPermissionRequest` and the first-valid-response/last-browser-disconnect rule are frozen under `contracts.py` and reverse-call invariants. |
| Chat deletion ambiguity | ACP-06 now requires an orchestrator-owned classification artifact before its contract is cut, with each symbol/table/event/route marked delete, retain, or move. |
| Pin mechanics | ACP-00 pins `agent-client-protocol==0.11.0` and vendors the exact framework-free donor revision with its upstream license/provenance. |
| Unknown update behavior | The wire/browser contract now freezes `protocol_update_rejected` and a persistent visible error, with no text fallback. |

