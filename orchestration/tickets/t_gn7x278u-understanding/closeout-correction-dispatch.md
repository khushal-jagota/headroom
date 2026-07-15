# Closeout correction dispatch — paired Stage opening turn

Ticket: `t_gn7x278u`
Branch/worktree: `ticket/t_gn7x278u-paired-opening-fix`

## Corrected user contract

Paired ownership is not silent waiting. When a transition, approved scope, or current-Stage ownership change makes a paired Stage eligible, Panels dispatches exactly one ordinary automatic Employee step into the Ticket's durable Hermes session. The prompt carries canonical Ticket/Stage context and asks the specialist to open that Stage's paired discussion. A first working Stage starts its bounded questions; a later paired Stage starts or resumes the same Employee conversation for that Stage. After the opening turn completes without a proposal, the Ticket rests at `paired_work`; polling must not redispatch it. User Ticket Chat continues in the same session. The ordinary proposal writer remains the only settlement path.

## Required implementation

Use existing state, not a new flag:

- `empty` represents an eligible worker-owned step or an eligible paired Stage whose opening turn has not completed.
- `agent_running_step` claims the automatic opening.
- finishing a successful paired opening without a proposal writes `paired_work`.
- `paired_work` is not automatically eligible once a worker-step opening event exists after the current Stage's latest entry/paired-ownership marker.
- preserve a compatibility path for already-created silently parked paired rows, including later paired Stages that already carry an Employee session from an earlier Stage. Use existing event history, not a new flag: locate the latest current-Stage entry (`stage_changed`/`ticket_created`) or actual current-Stage transition into paired ownership, then require a later `chat_turn_started` worker-step event before treating that Stage as opened. `paired_work` with no such later opening event is eligible once; `paired_work` with one is not.
- a transient `SharedGatewayBusy` must release the claim back to `empty`, not falsely mark the paired Stage opened. That retryable outcome must not trigger the runner's immediate completion wake; the periodic discovery timer provides the bounded retry so a busy session cannot cause a synchronous redispatch loop.

Entering a Stage (proposal acceptance, manual Stage movement, external-work creation/reconciliation when the Stage changes) must write the entered-Stage status: user ownership rests in `user_takeover`; worker or paired ownership becomes `empty`. Changing effective ownership of the current Stage into paired/worker likewise becomes `empty`, but repeating the same ownership setting must not reopen a completed paired Stage. Normal run/chat completion continues to use the existing resting status (`paired_work` for paired).

Tailor the automatic paired prompt so it opens the discussion and does not demand an immediate proposal. Preserve the existing worker-owned prompt.

## Tests — strict RED → GREEN

Add focused deterministic coverage before production edits for:

1. accepting Kickoff into `new_worker.needs_understanding` leaves `empty`, wakes discovery, runs one real automatic worker turn, creates/reuses the Employee session, emits the opening assistant message, then rests `paired_work`;
2. a later paired Stage (use `exploration.needs_answer`) follows the same one-opening contract and reuses its existing Employee session;
3. a second discovery poll does not redispatch either opened Stage;
4. current compatibility: `paired_work` + no Employee session is eligible once; `paired_work` + session is not;
   for an existing session, prove both a silently parked later paired Stage (no worker-step turn after its entry) and an already-opened Stage (worker-step turn after entry);
5. same-mode ownership writes do not add a new entry marker or reopen; a real current-Stage ownership transition into paired does;
6. `SharedGatewayBusy` leaves the paired opening eligible for a later periodic retry but returns without the runner's immediate wake;
7. ordinary user Ticket Chat after opening continues in the same Employee session and proposal parking remains unchanged;
8. e2e/public-flow expectations wait for the automatic opening instead of manually sending the first message.

Paired opening must reuse the complete existing eligibility decision. Add a paired one-factor-false matrix for: not on today, active chat turn, terminal/no gating field, parked proposal, at-cap Stop, and blocked Ticket. The only eligibility changes are the ownership/opening-status branch; do not fork or weaken the remaining guards.

Update shared worker guidance and live Worker-type docs that currently say paired Stages are never automatically dispatched. Keep automatic polling, canonical proposal authority, later lifecycle ordering, and all exploration/new-worker behavior otherwise unchanged.

Run focused pytest/Ruff/diff checks only. Do not run `./verify`, merge, deploy, restart, touch live data, modify unrelated owner files, or propose/close the Ticket. Write a concise correction report under this ticket's orchestration directory with RED/GREEN evidence.