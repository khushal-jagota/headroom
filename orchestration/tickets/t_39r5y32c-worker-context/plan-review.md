# Codex plan review

Codex reviewed the accepted plan against the current codebase in read-only mode using `gpt-5.5` with high reasoning effort.

## Findings

1. Human per-field Ticket notes must set `ticket_changed`, while worker/agent notes through the same route must not. This requires actor-gated producer coverage.
2. Return-for-revision is a real `run_ticket_step` / `prompt.submit` path and must receive pending context while preserving its hidden `show_prompt_in_chat=False` behavior.
3. Both gateway submit helpers require direct tests: `_submit_and_drain` covers worker steps, sync chat sends, and sync model-backed command messages; `_stream_prompt` covers streaming/background human turns and streaming model-backed commands.

## Resolution

All three findings are explicit implementation and test requirements in the dispatch. None changes the accepted architecture.
