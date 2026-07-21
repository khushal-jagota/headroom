# ACP-05 lifecycle-only compaction correction

## Root cause and contract

Real Panels reproduced a valid Hermes `/compact` result whose fork replay contained the same two
ordinary messages and no summary marker. Hermes intentionally reports this as
`Context compressed: 2 -> 2 messages`: short history is retained unchanged. Panels failed only because
it made a presentable summary a success condition.

The owner contract is smaller. Panels shows compaction started, finished, or failed with the exact
reason. Backend context is private and never enters the public contract or UI. Successful controlled
backend completion and durable session continuity determine success. The existing Hermes fork remains
only because current same-ID persistence can lose in-memory compacted history; it is not a summary
extraction step and is not required of other backends.

This is a bounded correction to the already-reviewed ACP-05 path. Per the owner review ruling, it uses
the exact red regression, root source spot-check, focused gates, and real Panels dogfood rather than a
new broad review round.

## Backend and public-contract lane

1. Remove `summary` from Python `ContextCompaction`. Require `reason` exactly for `failed`; compacting
   and compacted carry neither context nor prose.
2. Make Hermes capture accept zero or one valid private marker after successful controlled load. A
   protocol-rejected replay and malformed/multiple marker-like private items remain failures.
3. Classify replay once. With durable provenance and one valid marker, replace the marker in place with
   the content-free completion boundary or boundaries. With durable provenance and no marker, preserve
   all ordinary items and append the boundaries. No provenance + no marker remains ordinary replay;
   marker + no provenance remains a fail-closed inconsistent load.
4. Make the broker treat `state == compacted` as sufficient and stop copying summary text.
5. Update the Python conformance subjects, fixture writer/JSON, strategy/broker/hub/e2e assertions, and
   public contract tests. Keep the scripted private marker as a suppression fixture, never a payload.
6. The exact live-shape regression must turn green:

   `.venv/bin/pytest -q tests/unit/test_hermes_acp_turn_strategy.py::test_capture_accepts_a_compacted_replay_without_a_presentable_summary`

## Browser lane

1. Remove `summary` from the TypeScript `ContextCompaction` shape and strict transport admission.
2. Remove compaction expansion state, controller commands, and component callback plumbing.
3. Render a plain, non-interactive row: `Context compacting/compacted · explicit/automatic`; failed
   adds the exact reason inline. Do not import or invoke markdown for compaction.
4. Update fixture-driven state, transport, component, and production-mount tests. Preserve the one
   existing status line while compaction is active; make no shared token/layout/design changes.

## Integration proof

- Focused Python strategy, broker, hub, contract/conformance, and ACP e2e tests pass with scoped Ruff and
  strict Mypy.
- TypeScript contract/state/conformance/component tests, Svelte diagnostics, and production build pass.
- Root restarts actual Panels at `127.0.0.1:8767` and uses Computer Use to prove:
  `Context compacting` -> `Context compacted`, no private context, hard reload, server restart on the
  same durable binding, and one later exact-response prompt.
- Do not run canonical `./verify`; it remains ACP-10's final settled-tree gate.
