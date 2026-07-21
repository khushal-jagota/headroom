# ACP browser replay and live backpressure plan review

## Verdict

**NOT READY**

The two-phase subscription is the right core design, and the active-turn cutoff is correctly assigned to the employee sequencer. The plan still leaves four load-bearing gaps.

## Findings

### 1. Blocking — reset replay for already attached browsers still goes through the live queue

The plan only gives the newly attaching browser immutable bootstrap storage. It explicitly preserves publication to already attached browsers during unloaded/idle refresh and replacement-generation work (`plan.md:26`). In the current hub, `_bind_stream` publishes reset and the complete replay synchronously, and `_publish_envelope_now` immediately puts every envelope into every registered browser's bounded queue (`hub.py:1612-1639`).

That leaves two contract violations:

- a valid large reset replay can evict an actively consuming existing browser as a “slow” live client before its writer gets a scheduling turn; and
- an over-byte-limit reset replay can expose a partial reset/transcript to an existing browser before the hub discovers that the reset epoch is unavailable.

The active-attach and new real-WebSocket tests do not exercise this path, so they can pass while required behaviors 4, 6, and 8 remain broken. The plan must define how every reset/replay transition is integrity-checked and delivered outside live backpressure for browsers that remain registered across idle refresh, compaction, or replacement generation. Add a public-seam test with an existing healthy WebSocket across such a reset, including the byte-limit failure case, or explicitly close/re-attach it before any replay prefix is visible if that is the preserved contract.

### 2. Blocking — permission-detachment ownership is not defined or provable

The plan requires exactly-once permission detachment (`plan.md:33-34`) but does not assign one owner across the existing paths. Today an active replay rejection detaches in `attach_browser` (`hub.py:414-415`), every WebSocket later detaches again in `finally` (`hub.py:1184-1190`), and slow-client eviction schedules another detached task from synchronous fanout (`hub.py:1635-1639`). “Centralize closure” does not by itself prevent duplicate calls or races with finalization.

The proposed `_Permissions.attached` set assertion can prove eventual absence only; it cannot prove exactly-once cleanup or that no unowned detach task remains. The plan must specify a single idempotent ownership mechanism—such as an attachment-owned claim/flag—and remove or route all rejection, overflow, peer-finally, and exception cleanup through it. Tests must count detach calls and cover both attach-time replay rejection and live overflow followed by WebSocket finalization.

### 3. Major — the 1,024 default is placed outside the required configuration module, and the named test does not prove production composition

`PRINCIPLES.md:8` requires tunable limits in an isolated configuration module, and `src/planner/conversation/configuration.py` already owns conversation limits. Defining the capacity in `hub.py` (`plan.md:37-39`) violates that rule. The contract's allowed-file list currently excludes the correct configuration file, so the ticket boundary must be amended before implementation.

The proposed `test_default_browser_capacity_is_1024_and_smaller_capacity_is_injectable` can pin the hub constructor default, but it does not prove the acceptance seam that `ConversationComposition.build(..., test_options=None)` supplies 1,024. The natural composition tests are outside the allowed files. Amend the contract to allow `src/planner/conversation/configuration.py` and a focused composition test, then name a test that exercises the production branch as well as test injection.

### 4. Major — the shutdown test asks for an unspecified behavior change

The new bootstrap phase needs a shutdown/cancellation regression test, but `test_shutdown_closes_subscription_with_pending_bootstrap` also requires removal of browser registration and permission attachment (`plan.md:21`). Current hub shutdown sets close state and cancels sequencers but does not remove registrations or call permission detachment (`hub.py:1117-1150`); composition shuts the permission broker before the hub. The contract explicitly says shutdown behavior remains unchanged, and no implementation step defines a safe new shutdown ownership/order.

Keep the regression test focused on preserving the existing close reason, stopping bootstrap before another send, and awaiting writer cancellation. Remove the new cleanup assertion, or amend the contract and plan the composition/permission-broker shutdown ordering needed to make that broader behavior intentional.

## Adequate areas

- The sequencer-owned snapshot/register cutoff is sufficient for active same-generation ordering if the reset-transition gap above is resolved.
- The cursor plan preserves paired fields, generation rejection, future-sequence rejection, and unloaded sequence floors without inventing a delta protocol.
- Tracking total replay envelope/UTF-8-byte counts after retention stops is sufficient for actionable byte-limit logs.
- The proposed log schema is content-free and specific enough to test exactly.
- The real WebSocket test is the correct acceptance seam for a replay larger than the injected live capacity, followed by a sequenced live update.

No product code or tests were changed or run during this review.

## Second review

**READY**

The amended contract and plan resolve all four prior findings:

1. Reset replay is now built and byte-validated off-stream for every reset-producing path. Existing browsers receive a single ordered cutover marker referencing immutable subscriber-local replay, so historical envelopes do not consume live capacity and an invalid candidate cannot expose a prefix. The unit matrix and real-WebSocket replacement test cover both valid and byte-failed transitions.
2. Permission cleanup now has one subscription-owned, idempotent detach claim/task, with every attach failure, server closure, peer detach, overflow, and WebSocket finalizer routed through it. The coordinated race tests count calls, await completion, and check for orphaned tasks, so they prove more than eventual set removal.
3. The 1,024-envelope default now lives in `conversation/configuration.py`; the contract allows that file and the composition test file. The named composition test covers both the production branch and the injectable test override.
4. The shutdown regression is narrowed to existing close, send-stop, and cancellation behavior. It no longer requires registration cleanup or permission detachment and does not alter composition shutdown ordering.

The revised reset design is compatible with the current employee sequencer and binding rules. Candidate construction and commit occur in one sequenced operation, so ingress admitted before the operation is included through the existing capture/quarantine rules, while ingress admitted afterward follows the cutover marker. Existing queued live entries remain before the marker; candidate replay and `ready` remain contiguous; queued echoes and queue snapshots remain after `ready`; and later live entries cannot cross the cutoff. Same-binding refresh and requested-cancel recovery retain their sequence floors, while compaction and New Conversation retain replacement-generation sequence behavior. Cursor validation and reducer behavior remain unchanged.

No concrete unresolved finding remains. This second review changed only this review file; no product code or tests were changed or run.
