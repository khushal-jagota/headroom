# ACP-05 durable compaction fork implementation report

## Outcome

Implementation and the two focused-review corrections are complete and ready for the
orchestrator's narrow correction review.

Compaction capture now stays on the exact leased ACP child long enough to call the official
`session/fork` operation. Panels privately loads the returned session, lets the existing Hermes
normalizer prove exactly one structural summary, and only then installs the N+1 durable binding.
The ordinary same-child winner retains child generation and record identity.

The broker and hub treat that durable change as one transition. Admission waits while the transition
is open. Hub commit publishes N+1 reset, typed replay, ready, queued human echoes, and the queue
snapshot but does not release admission. The broker then publishes the normalized compaction boundary,
settles the tracked turn, publishes idle, advances the queue, and explicitly completes the hub
transition. Recoverable abort follows the same staged settlement rule on N. Two-browser ordering and
both success/failure action/attach races are pinned in unit tests.

Pre-CAS failure restores exact N before returning a visible failed boundary. CAS ambiguity is resolved
from the durable repository; a competing durable winner is privately adopted and is not called a
successful compaction. If N+1 is durable but actor/browser transition cannot complete by the one
actor-owned absolute deadline, the exact replacement runtime is invalidated and its child is closed.
The durable N+1 binding is not rolled back; a later fresh demand can load it into a new child generation.
If the registry cannot prove or restore the exact live runtime before it can return a replacement,
an explicit generation-fatal disposition reaches the broker. The broker fails the actor, tracked turn,
queued intent, and hub transition without publishing a false idle state.
That fatal disposition also takes precedence when normalization failed first but mandatory abort then
discovers that exact N cannot be restored; the earlier recoverable error cannot hide invalidation.

The official-SDK scripted agent now has a test-owned durable session store. Explicit compaction is
proven across refresh, deliberate child death/respawn, and a fresh application process. Automatic
compaction is proven to retarget and run one queued successor. Missing advertised fork capability
fails visibly while generation N remains usable.

Per the owner's cutover decision, this proof starts with fresh ACP sessions. No compatibility path was
added for legacy `planner-chat` sessions. No Hermes-checkout file was written. The retired
retire-child/load-old-session compaction path and its loose replacement-handle side channel are absent
from production.

## Exact implementation scope

Production:

- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/sdk_child.py`
- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/turn_broker.py`
- `src/planner/conversation/hub.py`
- `src/planner/conversation/composition.py`

Tests and fixture:

- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/e2e/test_acp_conversation.py`
- `tests/support/acp_scripted_agent.py`

Memory and ticket evidence:

- `PROGRESS.md`
- `decisions.md`
- this report
- `focused-checks.txt`

No other file was intentionally changed for this ticket. `src/planner/conversation/__init__.py` did
not require an export change. The affected suite also ran the existing
`tests/unit/test_hermes_acp_turn_strategy.py`, but this ticket did not edit it.

## Verification

All required focused gates are green. Exact commands and outputs are recorded in
`focused-checks.txt`.

- Scoped Ruff: pass.
- Strict Mypy over seven affected production modules: pass.
- Affected unit suites: 122/122 pass.
- Full ACP conversation e2e file: 12/12 pass.
- ACP browser state and component suites: pass.
- Svelte diagnostics: 0 errors, 0 warnings.
- Temporary-directory production Vite build: pass, 182 modules transformed.

The first shared frontend run overlapped the disjoint permission-diff ticket's active correction and
failed in its owned `PermissionPrompt.svelte`/component assertion. No durable-fork file was changed in
response. After that owner reported settled green, the exact shared gates above were rerun and passed.

Canonical `./verify` was deliberately not run. ACP-10 owns the single final canonical gate.

## Review handoff

The independent implementation review returned two P1 findings: hub admission released at commit
instead of after broker-owned settlement, and an unrecoverable registry failure was collapsed into a
recoverable capture failure. Its first correction check closed admission ordering and found one
remaining fatal-abort precedence path after an initial normalization error. That path is now corrected
and pinned by an integrated real-registry/broker regression. The orchestrator owns the final narrow
correction review. The implementer did not use the interrupted self-dispatched review output.
