# Independent review of committed test pruning

Reviewed `58b161a6..dddea073` in the simplification-tests worktree. This is a
read-only review of the settled E2E, integration, frontend, deployment-script, and
verification-instrument cohorts, with spot checks of protected Conversation and
Worker boundaries. It does not approve later uncommitted unit reductions or claim
repository-wide verification. No suites or `./verify` were run.

Verdict: two focused coverage gaps need resolution below. The broad deletion of
source inventories, repeated presentation mappings, test-harness isolation cases,
and duplicate transport permutations is appropriate. Do not restore the deleted
cohorts wholesale or weaken the user's requirement to remove at least half the
actual cases.

## [P2] Keep one Conversation stream reconnect proof

Deleting all of `web/tests/conversation-feed.test.ts` removes its test at baseline
line 235, `fetches then tails, publishes live work, and reconnects after closing
the old tail`. This is a behavioral recovery boundary, not a label or source-shape
assertion: it triggers the current tail's `onTrouble`, verifies that tail closes,
reads missing events after the last recorded sequence, and opens the replacement
tail at the caught-up sequence. It also proves transient output becomes recorded
output rather than remaining alongside it.

At the reviewed revision no retained test references `createConversationStream`,
`openTail`, or `onTrouble`. `web/tests/change-stream.test.ts:169` exercises the
separate `/api/changes` invalidation stream; it cannot fail if the Conversation
stream stops reconnecting or reconnects from the wrong sequence.
`web/tests/conversation-pane-browser.test.mjs` supplies already-projected rows
directly to ConversationPane. Retained Conversation E2Es load/reload and follow
rows, but do not interrupt and recover the Conversation tail. The actual stream
is still used by `LiveConversation.svelte:319` and implements this recovery in
`web/src/lib/conversation/feed.ts`.

Restore just the existing reconnect scenario, with its minimal fixture, or an
equivalent compact Vitest case. Remove one genuinely redundant retained mapping
case elsewhere if necessary to preserve the firm total ceiling. There is no need
to restore feed timing permutations or add a live-server E2E.

## [P2] Keep a browser proof that managed HTML actually works in its sandbox

The diff deletes all three relevant browser scenarios in
`tests/e2e/test_ticket_file_previews.py`: baseline line 698 (refresh), line 771
(interactive HTML), and line 844 (sibling stylesheet/image loading). No replacement
was added. The retained artifact-open journey at current line 292 opens Markdown;
its only refresh assertion at line 383 checks that Markdown has no refresh
control. The retained hydrated-proposal case at line 765 inspects the HTML
iframe's `srcdoc` property, but never exercises its document or a relative asset.

`web/tests/file-preview.test.ts:350` checks HTML preparation with a hand-written
DOMParser substitute. It proves the prepared base URL, but cannot prove a real
browser resolves it inside the sandbox or that FilePreview sets the effective
sandbox permissions. `file-preview-browser.test.mjs` covers a missing image's
error display. Neither covers the deleted HTML boundary. The old helper functions
left in the E2E file are uncalled and therefore provide no proof.

Preserve one compact managed HTML artifact journey: open it over its Ticket,
exercise one script interaction, verify a sibling asset loads with `sandbox` set
to `allow-scripts`, then refresh changed content while keeping the Ticket open.
This can replace the long retained artifact navigation journey, sharing its one
Ticket and artifact setup rather than adding separate cases. Remove the exact
geometry, mobile/both-surface permutations, and repeated navigation assertions.
The material risk is the real browser's sandbox and URL resolution together with
the managed-file server route; mock DOM serialization and API response bytes
cannot establish that boundary. Keep E2E at 19 or fewer actual cases.

## Protected boundaries confirmed

- `test_failed_markdown_save_retries_exact_pending_source_without_more_input`
  remains at `tests/e2e/test_ticket_file_previews.py:666`, exercising the actual
  editor and retry after failed field PUT without additional input.
- `test_loaded_preview_proposal_approves_without_edited_body` remains at line 765;
  it waits for previews, captures the approval payload, rejects `edited_body`,
  and verifies exact stored source.
- `test_retained_page_offers_and_applies_same_origin_release_update` remains in
  `test_release_freshness.py:11`, retaining the old document through server
  replacement and checking the new boot SHA after the actual update control.
- All five executed script cases remain in `test_deployment_assets.py`: moved
  current-app wrapper, runner registration, ambient Hermes override, user systemd
  control, and recovery of a loaded launchctl job without a process. Deleted cases
  principally inspect checked-in source text.
- `test_trusted_ingress_browser.py` remains, as do the HTTP unsafe-origin and
  WebSocket origin-policy cases in `test_trusted_ingress.py`. Deleting repeated
  duplicate-header transport permutations does not remove the origin boundary.
- Active writable Conversation ownership remains in
  `test_ticket_conversation_history_browser.py:51`; paired initial state remains
  in `web/tests/ticket-conversation-state.test.ts`. Start-wiring tests retain the
  active-pointer race, first-send refusal, mismatched Conversation rejection,
  held/refused configuration timing, and reset/discard cases.
- `test_worker_step_readiness_loop.py` still proves one-winner claims (line 172),
  refusal release (283), queued success (306), acknowledgement after delivery
  (414), and the opener's actual pending context (572). The real-prompt installed
  Worker proof remains in `tests/integration/test_worker_skill_runtime_proof.py`.
- The real-process conformance suite and the named provider adapter/transport
  files are unchanged in this reviewed diff. The removal of the duplicate
  in-memory conformance collection does not remove their execution.
- Notification registration, Review keyboard isolation, and both Worker browser
  cases remain.

## Counts and verifier scope

A static AST recount of the committed trees confirms E2E goes from 39 to 19 test
definitions (20 removed, 51.28%) and integration from 14 to 8. The reviewed E2E
diff removes independent bodies; it does not hide them inside new loops. The
frontend and deployment cohorts likewise predominantly delete actual cases.
Overall half-reduction acceptance still needs the final comparable collected
inventory after the other owners finish; these counts do not certify that total.

The verifier change matches the settled narrow scope: only the retired substring
and empty-test scanner and its synthetic assertions are removed. Mode parsing,
the CSS syntax gate, process gates, timeouts, result reporting, and the
PLAN_FAKE_NOW/PLAN_TEST_MODE clock proof remain. The two removed legacy script
commands correspond to deleted source/geometry-oriented test files. No production
Conversation or backend code was changed by this cohort.

The initial audit's keep/delete map is stale in several places (for example, it
still says to keep the Ticket image E2E and remove the release-freshness E2E,
whereas the final diff does the reverse). The parent is already replacing that
map. Cite final retained cases and their actual boundaries; do not repeat the
initial map as evidence of the settled diff. I do not request restoration merely
to match that provisional map: retained image preparation, transcript, and API
byte-persistence tests establish its core intent without preserving every browser
transport permutation.

## Accepted fixes and focused evidence

The parent accepted both findings and the bounded replacement plan, then delegated
implementation of only these two test files and this record. The parent owns the
independent check of the resulting patch; this author does not independently
approve their own fixes.

- Restored one original `Conversation stream` reconnect/catch-up scenario in
  `web/tests/conversation-feed.test.ts`, with only its imports and deferred-promise
  helper. It closes the old tail, reads after sequence 2, opens after sequence 3,
  clears transient text, and retains the recorded events. This adds one runtime
  case; it does not restore the removed timing and presentation permutations.
- Replaced `test_ticket_artifact_opens_in_place_over_the_ticket` with
  `test_html_artifact_interacts_loads_sibling_assets_and_refreshes_in_place` in
  `tests/e2e/test_ticket_file_previews.py`. One Ticket and one artifact prove the
  canonical in-place address, the real `allow-scripts` sandbox, a sibling
  stylesheet and image, a working script interaction, then changed-file refresh
  without reloading the page or losing the Ticket. Removed the superseded
  geometry, repeated navigation, and uncalled helper bodies. The file still has
  four test cases and the E2E suite still has 19.

Worktree source identity was confirmed through `git rev-parse --show-toplevel`
and the installed `planner.__file__`, both resolving to simplification-tests.
No ambient runtime-path overrides were present. Existing `web/dist` and unchanged
dependencies were reused. The E2E fixture selected an available port, created its
isolated runtime, and stopped its service through fixture teardown.

Focused commands and complete result summaries:

```text
$ npm --prefix web run test:vitest -- tests/conversation-feed.test.ts

> test:vitest
> vitest run --typecheck tests/conversation-feed.test.ts

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/simplification-tests/web

 Test Files  2 passed (2)
      Tests  2 passed (2)
Type Errors  no errors
   Start at  06:49:09
   Duration  897ms (transform 26ms, setup 0ms, import 33ms, tests 3ms, environment 0ms, typecheck 786ms)

$ .venv/bin/pytest -q tests/e2e/test_ticket_file_previews.py::test_html_artifact_interacts_loads_sibling_assets_and_refreshes_in_place
.                                                                        [100%]

$ .venv/bin/ruff check tests/e2e/test_ticket_file_previews.py
All checks passed!

$ git diff --check -- tests/e2e/test_ticket_file_previews.py
(no output; exit 0)
```

That focused Vitest command reported both the runtime and typecheck projects; its
two executions were never used as the inventory count. The restored file contains
one `it` body and therefore adds one runtime case. Final inventory collection used
`vitest list --typecheck.enabled=false`, which expands parameterized cases without
duplicating them through the typecheck project. It found 528 baseline Vitest cases
and 178 in the integrated tree. The first Ruff check caught a 101-character
assertion; wrapping that assertion was the only edit after the passing behavioral
checks. No full suite, build, or `./verify` was run in this review. These focused
passes resolve the two named missing proofs; the parent's independent patch review
and final program gate remain separate.
