# ACP-00 implementation review — round 1

## Findings

### 1. Blocker — the wire validators accept non-wire field names and coerce invalid browser values

`src/planner/conversation/contracts.py:31-37` enables `populate_by_name` for every contract model, and
the two ingress functions use the permissive defaults at
`src/planner/conversation/wire_contracts.py:277-280,293-295`. Consequently
`validate_browser_action({"type": "attach", "employee_id": "e"})` is accepted even though the
frozen browser wire and TypeScript contract require `employeeId`; likewise a JSON string
`lastSeenSequence: "2"` is coerced to integer `2`. The agent-update ingress also accepts
`session_id` / `session_update` Python names and emits `acp_session_update` instead of rejecting that
non-ACP wire shape. This breaks the closed, cross-language wire and the requirement that invalid
browser actions fail validation before they can affect agent state.

Make the two raw-ingress paths alias-only and strict (while retaining name-based construction for
trusted Python model creation), and add negative tests for snake-case wire keys and string-valued
integer fields. The agent raw-notification test should prove the same malformed aliases become
`protocol_update_rejected`.

### 2. Blocker — the thought probe cannot detect the refresh/replay flattening bug it exists to gate

`tests/support/acp_conformance.py:149-153` checks that replay contains an
`agent_thought_chunk`, but compares thought text against only one undifferentiated
`assistant_texts` collection. The reference subject populates that collection exclusively from
`live_updates` at `tests/support/acp_reference_subject.py:391-410`; it never reduces replay and never
records replayed assistant output. A subject that preserves the typed replay callback but then
flattens that thought into assistant transcript text can therefore pass probe 2—the exact refresh bug
called out by the owner brief and `contract.md:158-160`. The mutation at
`tests/support/acp_conformance.py:336-338` only adds the complete live-derived thought string to that
same collection, so it does not close the gap.

Record reduced live and replay assistant outputs separately, assert that neither contains any thought
content (including chunked/concatenated forms), and make the probe-2 mutation corrupt the replay
reduction path. The reference subject must run the same minimal grouping/reduction logic for live and
load-replayed notifications.

### 3. High — several mandatory probes pass on fabricated evidence rather than the scripted subject

The reference subject hard-codes both sides of durable refresh at
`tests/support/acp_reference_subject.py:459-466`, the steer/broker result at
`tests/support/acp_reference_subject.py:468-474`, and explicit compaction state/summary at
`tests/support/acp_reference_subject.py:482-486`. For callback ordering it assigns
`broadcast_order=reduced_order` at `tests/support/acp_reference_subject.py:489-498` instead of sending
callbacks through the required enqueue-only consumer and an observable broadcast step. These values
make probes 6–9 green without exercising the plan's minimal binding holder, turn broker, explicit
compaction observer, or ordered reducer/broadcaster. Mutating the already-shaped evidence proves only
that the assertion functions notice their own fields; it does not prove the scripted subprocess and
reference subject supply authentic evidence.

Derive these observations from small test-only implementations as permitted by `contract.md:169-170`:
perform the refresh/load against a stored binding, execute declared steer plus broker-owned queue and
send-now operations, drive an explicit compaction switch through live/completed capture, and route the
concurrent SDK callbacks through an enqueue-only consumer whose broadcast order is recorded
independently. Keep the existing one-probe-at-a-time mutations against those observed results.

## Verdict

**NOT READY** — the closed wire accepts invalid shapes, and the reusable conformance harness does not
yet detect the central replay-flattening failure or authentically exercise several mandatory probes.
