The native cancellation checks now establish motion at input delivery and give each Latest scenario fresh state. The reviewed source is `5e76bbf49407798402fdee354eb32281a4b76eb4`.

The worktree started from fetched staging `e38d5988fc29c9c23abf8810266322b72c491df7`, which includes the shared barriers from PR #326. This evidence ref adds documentation only. The source branch remains at the exact checked commit.

## What changed

The gesture and send scenarios each establish fresh answer space through the existing fixture send path. The shared scrollback barrier checks one native input, displacement, scroll completion, and stable geometry. Separate checks require positive reserved tail, an available Latest button, and manual ownership.

For this setup, the barrier uses `min_clearance=0`. Native movement retires the same amount of reserved range, so the physical bottom gap can remain zero. The actual Latest prerequisite is positive reserved tail. The [initial setup failure](initial-setup-failure.json) records this measured distinction. No original assertion or tolerance changed.

A capture listener records trusted wheel delivery before the application handler. The receipt requires the same transcript, the expected owner, active pane motion, and recent displacement between consecutive frames. A subsequent frame checks manual ownership. Native scroll completion replaces the old 220ms pause before the original preservation baseline. This exposes stale travel while it still occurs.

The superseding message is prefilled before Latest starts. The fixture records motion immediately before its deferred acceptance return. Late input or acceptance fails setup with the trace. The new checks use the real clock. The later Home scenario retains its existing clock control.

## Verification

The complete existing catalog ran once on the exact source commit. All 14 catalog cases, the required typed companion, and the compiler pass. Total invocation time is 422.103 seconds. The unanswered-send case takes 49.446 seconds. The receipt includes every catalog entry, its revision, and its invocation identity.

- [Catalog receipt](catalog.json) and [full output](catalog.log).
- [Native receipts from the catalog](catalog-native-proof.json).
- [Real-clock timing summary](delivery-timing-summary.json) and [delivery frame proof](delivery-placement-proof.json).
- [Assertion comparison](assertion-comparison.json): all 86 original assert expressions and all 10 deferred conditions remain. There are 97 assert expressions in the revised case.
- [Independent review](review.json): no unresolved violations.
- [Ruff](ruff.log), Python compilation, and both Node syntax checks pass.
- [Mypy](mypy.log) passes for 644 source files. Its configured inputs remain unchanged by this browser-only patch.
- [Existing gate contract tests](gate-contract-tests.log): three pass.

The five original real-clock duration proofs measure 250.5–266.5ms with 15–16 movement frames. Their exact original limits remain 150–340ms and more than three movement frames.

## Focused controls

All seven controls pass in one final invocation. Their browser execution totals 180.544 seconds, excluding preparation and build. The [report](native-controls.json) includes the expected rejection. The [receipts](native-controls-summary.json) retain source hashes, native events, and motion samples.

| Control | Required result | Result |
| --- | --- | --- |
| Clean | Valid setup and behavior | Pass |
| Zero-tail predecessor | Tail is zero, then fresh independent Latest state succeeds | Pass |
| Positive-tail predecessor | Tail is positive, then fresh independent Latest state succeeds | Pass |
| Late wheel | Trusted input after motion fails the setup premise | Pass |
| Late acceptance | Acceptance after motion fails the setup premise | Pass |
| Ignored gesture cancellation | Valid motion premise, then original frame-preservation condition fails | Pass |
| Disabled send supersession | Valid acceptance premise, then original placement condition fails | Pass |

The wheel mutation suppresses the application handler while it retains native input and its default action. The supersession mutation affects the complete function body in a disposable build. A fixture flag arms it only at the target acceptance, across all call paths. Production source stays unchanged.

The first global supersession mutation invalidated earlier fixture setup. It earned no passing control result. A separate trial showed that the old 220ms pause hid ignored cancellation from the original checks. Native completion now establishes that baseline sooner. The final driver rejects a mutation that fails setup or only the new ownership check.

These controls address a distinct coverage gap: an apparent cancellation can occur after motion ends, or later assertions can depend on leftover tail. A real browser is necessary to prove trusted delivery, native scroll completion, and rendered motion together. The controls reuse the existing fixture host. They add no browser host or catalog identity and emit no catalog receipt. Ordinary runs take no ambient diagnostic selector. No retry admits a failed premise.

## Commands

Run from the ticket worktree with its fresh pinned environment:

```sh
.venv/bin/ruff check web/tests/browser/cases/live-conversation-unanswered-send.py web/tests/browser/diagnostic-native-cancellation.py
.venv/bin/python -m py_compile web/tests/browser/cases/live-conversation-unanswered-send.py web/tests/browser/diagnostic-native-cancellation.py
node --check web/tests/browser/cases/live-conversation-unanswered-send.mjs
node --check web/tests/browser/diagnostic-native-cancellation.mjs
.venv/bin/mypy
.venv/bin/pytest tests/unit/test_deployment_gate.py tests/unit/test_deployment_gate_commands.py
node web/tests/browser/diagnostic-native-cancellation.mjs --report /home/vps/projects/panels/task-tmp/t_ebrebdek/native-controls.json
env -u CONVERSATION_SETTLED_CASE -u CONVERSATION_LATEST_CASE -u CONVERSATION_SCROLL_CASE -u PANELS_CONVERSATION_MENU_ONLY -u PANELS_NATIVE_CANCELLATION_CONTROL node web/tests/browser/run.mjs --revision 5e76bbf49407798402fdee354eb32281a4b76eb4 --report /home/vps/projects/panels/task-tmp/t_ebrebdek/catalog.json
```

The static and pytest commands used an environment without live Panels runtime paths. The browser runners remove those paths themselves. Assertion preservation uses Python AST expressions with multiplicity, against the pinned base. The raw inventory includes each original expression and its source.

After Implementation approval, Consequences will use one staging pull request, require `mypy (merged tree)`, and merge explicitly. No pull request merge, main change, or deployment occurred during Implementation.
