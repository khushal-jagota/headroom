# ACP-05 requested-cancel recovery implementation report

## Outcome

Requested Stop and Send Now now install an exact-source quarantine before ACP cancellation. A normal
terminal cancellation response drains that quarantine and keeps generation N. If the successfully
cancelled prompt instead unwinds exceptionally, the broker retires N and privately loads the exact
same durable ACP session and binding through a fresh child generation before the hub publishes a
same-binding reset/replay/ready/queue transition.

The replacement keeps the ACP session ID and binding generation exact. Child generation increases,
and the child object and record identity both change. The old source cannot publish or contribute to
worker collection after recovery begins: held N payloads are discarded on recovery commit, and stale
N payloads are ignored after the replacement stream is installed.

## Checkpoints

1. The runtime replacement value, exact recovery token, hub transition port, and registry runtime
   method are frozen and strict-type clean. Broker controls prove quarantine begins before cancel.
2. The registry owns exact planned retirement, waits for its callback settlement, spawns N+1, privately
   loads the unchanged session, and publishes only after the durable binding is re-read unchanged.
   Stale lease, admitted-ingress quiescence, fresh initialize/death, durable-binding drift, hard
   deadline, planned-close error, and private-load failure tests prove no reusable generation leaks.
3. The hub owns a bounded same-binding barrier. Two browsers receive increasing
   `reset -> replay -> ready -> queue_snapshot`; normal cancellation resumes held ingress in source
   order; expiry and unexpected old-child death release/fail once; attach, actions, and new conversation
   wait while compaction cross-rejects the open transition.
4. The broker freezes successor and FIFO intent until registry replacement and hub commit finish.
   Stop advances FIFO once on N+1; Send Now starts one retargeted successor and then preserves FIFO.
   Reverse-service cleanup, replacement/load, invalid replacement, recovery deadline, and hub-commit
   failure reject all frozen intent once with no reusable idle.
5. New conversation and shutdown remain separate close owners. Exceptional prompt unwind retires the
   exact lease under the caller's close deadline, never calls the recovery port, and never publishes
   same-session reusable idle. Retirement error and timeout invalidate the exact handle and propagate
   to the close owner, so New Conversation cannot continue on the exceptional child.
6. Production composition binds the one hub as the requested-cancel transition port. The official SDK
   subprocess test proves both Stop and Send Now with a real post-cancel `session_update` attempt whose
   external audit is written only after the send returns. Real worker collectors installed on both
   old generations remain empty; reset/ready/queue precede Send Now successor start; the successor's
   exact marked answer appears once; later prompts run once on the unchanged session's fresh child.
7. Existing ACP browser state, component, conformance, and contract tests pass unchanged; Svelte reports
   zero errors and warnings. No frontend source or visual treatment changed.
8. The post-review focused gate passes Ruff, strict Mypy, 119 named affected Python unit/e2e tests,
   all four browser gates, and Svelte diagnostics. Full output is in `focused-checks.txt`. Canonical
   `./verify` was intentionally not run.

## Deadline and settlement ownership

The actor creates one absolute requested-cancel deadline before quarantine begin. After cancel and
permission cancellation succeed and the prompt unwinds exceptionally, that same deadline reaches
reverse-service cleanup, exact registry retirement/fresh private load, hub commit, and final actor
settlement. ACP-02's shorter cancellation-settlement timeout remains a fail-fast detector: if cancel
or permission cancellation does not settle, recovery never begins.

The timeout branch reuses that stored deadline instead of creating a second one. Exact retirement is
bounded from employee-gate acquisition through planned-death settlement. If retirement cannot be
proved, the registry invalidates the exact handle immediately and detaches child close. Exceptional
New Conversation and shutdown retirement failure is likewise generation-fatal and is propagated to
the close owner.

Stop and Send Now settle predecessor, successor, and FIFO intent exactly once. New conversation and
shutdown retire exceptional children without reusable idle. Existing compaction fork/CAS/actor-rekey
behavior is unchanged, as is the normal `PromptResponse(stop_reason="cancelled")` path on generation N.

## Scope

Only the contract's allowed conversation runtime, focused test/fixture, ticket evidence, and memory
files were changed. No SDK child protocol, backend definition, public wire schema, frontend component,
shared visual asset, generated distribution, or configuration file was changed by this ticket.
