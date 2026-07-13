# Codex architecture audit — before implementation

Model: `gpt-5.5`; sandbox: read-only; reasoning effort: high.

1. Startup/recovery must run before readiness discovery. Today `server.py` calls `start_background_loops()` directly after gateway construction, and `start_background_loops()` starts `TicketReadinessLoop` immediately. Add a recovery coordinator before poller start, and eagerly start both worker/chief gateways before recovery. Test exact order: audit, skills, gateways ready, recovery admitted, readiness started.

2. Do not recover ticket workers through the current `_run(..., revision_guidance=...)` path unchanged. A crashed worker turn will still be `running`; `start_worker_turn()` hits the one-running-turn index, and `_run()` turns that into ticket error. Required design: one atomic chat rollover writer: old running turn → `interrupted` preserving `output_text`, insert fresh recovery turn with same session. Test partial output preserved and fresh recovery turn is the only running turn.

3. Strict same-session recovery exists for ticket steps but not ordinary chat. `run_ticket_step(... require_existing_session=True)` reaches `allow_create=False`; ordinary `stream()` uses `_resume_or_create()` with creation allowed. Add a strict ordinary-chat continuation primitive routed through `EntityRoutingGateway`. Tests: missing session calls only `session.resume`, never `session.create` or `prompt.submit`.

4. Recovery discovery must avoid double-owning the same ticket/session. Ticket recovery should be driven by durable `tickets.ticket_status='agent_running_step'`, not by worker chat rows. Ordinary chat recovery should exclude ticket ids still at `agent_running_step` and worker-origin turns. Test with an `agent_running_step` ticket plus a running worker turn: exactly one continuation.

5. Session ownership needs hard checks. Worker recovery’s authoritative key is `tickets.chat_session_key`; ordinary chat’s key is the owning ticket/day/chief row, not just `chat_turns.session_key`. A null key, turn/entity key mismatch, or `session.resume` not-found must produce fallback error without remint. Tests for null key, mismatch, not-found, and rotated resumed key with no create.

6. Proposal/status atomicity is already correct and should remain untouched. `file_current_proposal_with_recap()` writes proposal, recap, and `awaiting_approval` in one transaction; `finish_run_if_still_running_step()` preserves `awaiting_approval`. Add rollback-focused tests around proposal+recap+status and revision proposal-clearing+`agent_running_step`.

7. Service stop is not bounded today. `EmployeeStepRunner.stop()` waits forever; `BackgroundLoops.stop()` awaits it without timeout; readiness stop has its own ten-second join outside any total budget. Add one `shutdown_grace_seconds` budget covering discovery stop, worker/chat drain, interrupts, and gateway stop.

8. Service stop must not convert recoverable work to `errored`. Current employee interruption marks the ticket errored. During service stop, unresolved work should remain durably `agent_running_step` so startup recovery resumes it. Test: long worker run, grace expires, no `errored`, next startup resumes same session.

9. Gateway/session stop still has blocking waits. `LiveSessionManager.shutdown()` waits indefinitely for concurrent shutdown and blocks acquiring every `command_lock`. Make these deadline-aware. Tests: stuck command lock, stuck request, stuck router, and stuck child wait all return within budget.

10. Ordinary chat threads have no service-level admission/drain owner. `start_human_turn()` spawns daemon threads directly. Recovery/service stop needs a small chat-turn runner/tracker: close admission on stop, bounded wait, then leave running turns recoverable. Tests for ticket/day/chief ordinary turns after restart, including partial output and no original prompt replay.
