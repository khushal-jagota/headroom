# ACP-05 Send Now post-recovery human boundary

## Why this ticket exists

Real Hermes Computer Use interrupted a `sleep 60` turn through **Send Now** and successfully ran the
captured successor. Hermes stored three distinct durable messages: old `Operation interrupted.`, the
successor user prompt, and the successor answer. Panels nevertheless rendered one agent block,
`Operation interrupted.SEND NOW RECOVERY READY.`

The requested-cancel reset clears the successor's pre-reset optimistic/human echo. The recovery commit
replays only Hermes's pre-successor durable prefix, and the broker then starts the captured successor
without re-emitting its user boundary. Because `started` is intentionally non-terminal, the browser's
missing-ID fallback correctly appends the new agent chunk to the open replay agent group.

## Frozen behavior

1. Successful requested-cancel recovery re-emits every reset-erased human boundary through the same
   order already used by compaction: same-binding `reset -> replay -> ready -> FIFO human echoes in
   original order -> Send Now successor human echo when present -> FIFO-only queue snapshot`. The
   successor echo uses its exact `client_message_id` and exact `PromptRequest` and precedes its
   `started` receipt or any successor ACP output.
2. The post-reset human echo is the ordinary existing typed `human_echo`; there is no new wire event,
   browser heuristic, synthetic separator, or backend-specific branch.
3. The old interrupted agent message, successor user message, and successor agent answer therefore
   remain three distinct transcript messages even when Hermes omits message IDs.
4. The original pre-reset echoes may be cleared by reset as today. A browser that observes the whole
   transition sees each retained FIFO prompt and the successor user prompt exactly once in the settled
   epoch, not twice. The queue snapshot still contains FIFO items only, never the Send Now successor.
5. Stop has no successor echo, but its retained FIFO prompts still survive a fresh-child reset. Normal
   cancellation that reuses generation N, ordinary prompts, Steer, requested-cancel failure, and
   compaction ordering do not change.
6. Worker tracking and successor settlement remain exact-once. The durable ACP session and binding
   generation remain unchanged across requested-cancel fresh-child recovery.

## Required proof

- Hub/broker unit regression for the exact successful Send Now recovery order: reset, replay ending in
  old missing-ID agent text, ready, FIFO human echoes, successor human echo, FIFO-only queue snapshot,
  successor started receipt, successor missing-ID agent text. Assert exact source order, one exact echo
  per prompt, and no duplicate delivery.
- Browser reducer/controller regression consumes that production-shaped order and asserts exact
  `agent / user / agent` messages, never one concatenated agent message.
- Existing requested-cancel official-SDK, hub, broker, and ACP browser suites remain green.
- Focused Ruff, strict Mypy, and Svelte/TypeScript diagnostics pass. Do not run canonical `./verify`;
  ACP-10 owns the final run.

## Allowed files

- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/hub.py`
- `src/planner/conversation/turn_broker.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/e2e/test_acp_conversation.py`
- `web/tests/acp-browser-state.test.mjs`
- this ticket's implementation report and focused evidence
- `PROGRESS.md`
- `decisions.md`

Do not change Hermes, the public wire schema, the browser reducer/component, generated `web/dist`,
unrelated runtime code, or the durable binding schema. If another production file is mechanically
required, stop and amend this contract with the reason before editing it.
