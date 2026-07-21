# ACP-08 Claude backend provider slice implementation report

## Result

The provider-local Claude Code ACP slice is complete and ready for serial production registration.
It adds one locked `claude` definition, a generic-child decorator, an opaque same-session compaction
strategy, and a one-shot initialize-only startup preflight. It does not add another conversation,
session, transcript, or worker-runtime owner.

The catalog-facing symbol is `build_claude_employee_backend_registration()`. It is zero-argument and
import-time inert. Its runtime builder resolves the repository-local adapter and Node executable,
materializes the decorated lazy child factory, and attaches the same preflight instance through
`startup_preflight=preflight.run` and `is_executable=preflight.is_executable`.

## Implemented boundary

- `claude_backend.py` freezes backend key `claude`, adapter identity/version `0.60.0`, absolute
  Node-plus-entrypoint argv, Node 22+, the six-name inherited environment allowlist, no environment
  overrides, first-root cwd, `supports_steer=False`, and reverse capabilities false/false/true.
- The child decorator preserves generic lifecycle ownership. It copies new/load metadata, rejects an
  existing `systemPrompt`, and appends the absolute Panels worker-skill instruction while retaining
  Claude Code's `claude_code` preset. It decorates ordinary and captured load paths alike.
- Only the exact adapter compaction chunks are replaced with Claude-namespaced
  `SessionInfoUpdate` metadata. Every other ACP notification remains the original object, so the
  controls stay out of transcript and Automatic Employee text without a parallel message lane.
- `ClaudeAcpTurnStrategy` keys state by employee, session, backend, and binding generation. It dedupes
  starts, accepts only the first terminal control, preserves exact adapter failure text after the two
  separator newlines, retains ordinals across successive compactions, and exposes
  `capture_compaction_in_place`. The shared exact-generation wrapper owns the five-minute deadline;
  Claude never forks, reloads, or parses context for compaction.
- `ClaudeBackendStartupPreflight` performs only spawn, ACP initialize, capability validation, and
  close under one absolute deadline. It creates no session and sends no prompt. It validates protocol
  1, exact name/version, load, image, and embedded-context support, and stays non-executable until the
  transient child has closed successfully. Outer cancellation settles the in-flight spawn or
  initialize task; a late-created child is synchronously force-closed, and initialize cancellation
  closes the already-owned child. Deadline force-close is awaited rather than left as a background
  task.

## Locked runtime evidence

The implementation consumes the already-installed shared `agent_backends` package and does not edit
its integration-owned manifest or lock. Qualification evidence remains authoritative:

- `@agentclientprotocol/claude-agent-acp@0.60.0`
- shasum `981d5baa477c66d8bef75313f29509e4f9abd29e`
- integrity `sha512-+ZZCJukpKdEY+/O982UCtgGHOY+MKa/JPpZ34v25ITawRyQyg3cqqOGo3M+9TsA4D+T/NXb+kT3zUB1uQZhY+Q==`
- ACP SDK `1.2.1`, Claude Agent SDK `0.3.215`, embedded Claude Code `2.1.215`
- local deterministic probe Node `22.22.3`

The focused test executes the exact locked adapter through its absolute Node/entrypoint pair and
completes the real initialize-only preflight without a session, prompt, auth mutation, or model work.

## Focused proof

The final provider plus shared in-place-compaction set passes 33 tests. It covers exact definition and
environment confinement, metadata decoration/conflict rejection, exact control suppression, two
same-generation compactions, first-terminal-wins, cancellation reset, unsupported Steer, all required
preflight mismatches, deadline force-close behavior, zero-argument catalog materialization, the exact
package initialize probe, outer cancellation during spawn and initialize, and the generic no-fork
deadline owner.

Ruff and strict Mypy are clean. Exact commands and complete output are in `focused-checks.txt`.

## Serial integration handoff

Root owns the intentionally shared edits: add the zero-argument Claude registration after Hermes and
Codex, export the public symbols if desired, run the integrated startup ordering proof, and perform
the authenticated production dogfood/docs work. No generic API change remains necessary; the shared
in-place strategy hook and startup-preflight field used by this slice are already present.

No paid prompt, broad suite, canonical `./verify`, live server restart, user configuration mutation,
or generated frontend change was performed in this provider lane.
