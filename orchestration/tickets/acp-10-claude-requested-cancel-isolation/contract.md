# ACP-10 Claude requested-cancel isolation

## Why this ticket exists

Real Safari dogfood on Claude Ticket `t_1xbdpkq0` exercised **Send Now** during an active Claude
turn. Panels published `interrupted · Prompt interrupted`, ran the captured successor exactly once,
and received its exact answer. Claude's old turn nevertheless emitted its forbidden final answer
after the successor had completed. The pinned Claude adapter had returned a terminal prompt response
before all provider-side work stopped, so the existing normal-cancel path released the old source
quarantine and reused a child that was not actually quiet.

ACP session updates carry a session ID but no prompt epoch. Panels therefore cannot truthfully assign
an update that arrives after a requested-cancel response to the old or successor prompt. It must use
the existing exact-generation boundary for the one backend that has now proved a terminal cancel
response is not a sufficient reuse signal.

## Frozen behavior

1. `BackendTurnCapabilities` gains one exact provider fact: whether every requested Stop or Send Now
   must finish through the existing same-binding fresh-child recovery even when `session/prompt`
   returns a terminal `PromptResponse`. The default is false. The locked Claude definition sets it
   true; Hermes and Codex remain unchanged.
2. A requested Claude Stop or Send Now keeps the already-installed source quarantine closed, retires
   the exact old child generation, privately loads the unchanged ACP session through one fresh child,
   commits the existing same-binding reset/replay/ready transition, and only then publishes reusable
   idle or starts the captured successor. The durable ACP session ID and binding generation do not
   change.
3. Held updates from the retired Claude source are discarded by the existing recovery transition.
   Late callbacks from that record remain rejected by the registry. Panels adds no quiet-period
   timer, prompt-text parser, message heuristic, Claude UI, public wire type, or backend-name branch.
4. The existing exceptional requested-cancel recovery remains unchanged. Hermes and Codex normal
   terminal cancellation continue to reuse their current child. Queue order, Send Now human-boundary
   replay, permission cleanup, worker settlement, failure/deadline behavior, and compaction remain
   unchanged. This supersedes ACP-05 requested-cancel recovery frozen behavior item 1 only for a
   backend whose new capability is true; its false-capability control remains the ACP-05 behavior.

## Required proof

- A focused broker regression gives a definition the new capability, returns a normal terminal
  cancelled response after requested cancellation, and proves Stop/Send Now use the existing
  replacement transition, preserve the binding/session, and start any successor only on the fresh
  handle. A false-capability control proves the current reusable generation-N path remains intact.
- Claude definition coverage freezes the capability true; Hermes and Codex definition coverage keeps
  it false.
- Existing requested-cancel broker/hub and provider-definition suites pass with scoped Ruff and strict
  Mypy. Do not run canonical `./verify`; ACP-10 still owns the one final run.
- Restart the live Panels server and repeat the real Claude Send Now probe. The old turn must be
  interrupted, the successor must answer exactly once, and the forbidden old completion must remain
  absent after waiting beyond the provider's prior late-output interval.

## Allowed files

- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/claude_backend.py`
- `src/planner/conversation/turn_broker.py`
- `tests/unit/test_claude_acp_backend.py`
- `tests/unit/test_codex_backend.py`
- `tests/unit/test_hermes_acp_backend.py`
- `tests/unit/test_conversation_turn_broker.py`
- this ticket's implementation report, review, and focused evidence
- `PROGRESS.md`
- `decisions.md`

Do not change provider packages, the SDK child, hub/registry recovery implementation, public wire or
browser code, durable schema, generated distribution, or unrelated runtime/domain files. If the
existing recovery port cannot express the fix, stop and amend this contract with the exact reason.
