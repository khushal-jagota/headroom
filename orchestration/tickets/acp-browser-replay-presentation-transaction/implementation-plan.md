# ACP browser replay presentation transaction — implementation plan

## Success and approach

Keep the server, WebSocket envelopes, reducer, and pane scroll behavior unchanged. Make
`conversationController.ts` hold two state roles while replay is incomplete:

- `committedState`: the only state returned by `snapshot()` or published to subscribers;
- `replayCandidate`: a detached `ConversationState | null` used for protocol admission and ordered
  replay reduction from an admitted `connection/reset` through `connection/ready`.

This restores the missing load-complete presentation boundary. The browser still validates and
reduces every envelope, but Svelte receives the complete replay in one publication.

## State and admission model

1. Rename the controller's current `state` to `committedState` and add `replayCandidate`.
   `publish()`, `snapshot()`, `subscribe()`, ordinary UI actions, attach cursors, and post-ready live
   work read `committedState`.
2. Add one small helper for the protocol head: `replayCandidate ?? committedState`. Admission reads
   this head's cursor so replay sequences remain contiguous while `committedState` deliberately stays
   behind.
3. When an admitted reset starts or replaces replay, derive a fresh candidate from the current
   protocol head (`replayCandidate ?? committedState`): apply the existing `session_reset` transition
   with the already-decided `preserveProtocolRejections` value, then reduce the reset envelope and
   cursor into that candidate. This preserves ordered rejections already admitted before an
   exact-next same-generation replacement reset. Do not publish it. Same-generation,
   newer-generation, duplicate, stale, wrong-identity, and reset-generation checks stay in
   `admitEnvelope` with their current decisions.
4. While a candidate exists, reduce every admitted non-reset envelope and cursor into it only. Do not
   publish. This includes every replay message, tool, activity, permission, terminal, receipt, and
   queue transition.
5. On an admitted `connection/ready`, reduce ready into the candidate. If the existing newer-epoch
   recovery rule applies, clear the recoverable connection error in the candidate. Then assign the
   complete candidate to `committedState`, clear `replayCandidate`, publish exactly once, and only
   afterward deliver the deferred first prompt. A valid ready with no candidate keeps its existing
   incremental recovery behavior.
6. After the candidate commits, all ordinary live envelopes again reduce into `committedState` and
   publish one snapshot per envelope exactly as today.

## Recovery and lifecycle

- Before any invalid-envelope, identity, generation, or gap recovery publishes its error, discard
  `replayCandidate`; apply the existing recoverable error to `committedState`, detach/close, and
  schedule reconnect as today.
- On socket close, socket error, and replay-unavailable close, discard the candidate before the
  existing local/recoverable connection transition. Thus an old complete transcript remains visible,
  while first attach remains the ordinary empty conversation plus the existing error state.
- On dispose, discard the candidate, clear the deferred prompt/subscribers/timer, close the transport,
  and dispose only `committedState`; do not publish.
- Reconnect attach cursors come from `committedState`, never from an abandoned partial replay.
- Do not add a replay flag to public contracts or UI. Candidate ownership is private controller state.

## TDD sequence

### Controller RED, then GREEN

Extend `web/tests/acp-browser-state.test.mjs` (and the focused deferred-prompt assertions in
`web/tests/conversation-controller.test.mjs` where useful) with deterministic fake transport cases:

1. Subscribe, feed reset plus a long sequence of transcript-bearing envelopes, and assert every
   callback and `snapshot()` before ready still has the committed empty transcript/cursor. Feed ready
   and assert exactly one new transcript-bearing publication contains the complete replay and final
   cursor. Then feed one live envelope and assert it publishes incrementally.
2. First establish a complete ready transcript. Feed a same-generation replacement reset and replay;
   assert callbacks and `snapshot()` retain the old complete transcript through every intermediate
   envelope, then swap once to the new complete transcript at ready. Assert preserved protocol
   rejections still follow existing reset semantics. Add the exact ordered case
   `reset -> protocol_update_rejected -> exact-next same-generation reset -> ready` and assert the
   rejection admitted into the first candidate survives the final committed snapshot.
3. For an in-progress candidate, separately exercise sequence gap/invalid data, ordinary close/error,
   and replay-unavailable close. Assert no candidate message appears, the last committed cursor is
   used on reconnect, the existing exact error text/timer behavior remains, and first-attach failure
   exposes no partial transcript. Exercise disposal before ready and assert no final publication.
4. Keep the deferred-first-prompt regression: reset and replay do not send it. Instrument fake
   transport prompt send to capture the latest subscriber snapshot at send time. On ready, assert
   that snapshot already contains the complete committed replay, then assert the optimistic prompt
   publishes after that replay snapshot and the transport sends once. Later ready/reconnect events
   cannot duplicate it.

Split existing controller coverage rather than moving its intermediate actions behind the replay
fixture's ready. Use the reset-bounded fixture only for hidden-candidate and final-commit assertions.
Exercise queue cancellation and permission response with corresponding envelopes admitted after an
already committed ready (or an explicit committed initial state), preserving their current action/send
assertions. Rewrite recovery continuations by committed cursor: an existing committed conversation
reattaches with that cursor, while an abandoned cursorless first-attach candidate must receive a new
reset before ready. Do not weaken reducer-only tests, which remain envelope-by-envelope.

### Browser presentation RED, then GREEN

Extend `web/tests/acp-browser-components.test.mjs` with a second mounted runtime using the real
`createConversationController`, `AcpConversationPane`, and a manual fake transport. Feed reset,
enough uniquely identified human/message envelopes to overflow the transcript, and ready one envelope
per animation frame. Sample `[data-chat-messages]` after each frame and record each increase in
`scrollHeight` plus its bottom distance.

The current controller must fail because it produces multiple transcript height increases. The fixed
controller must show zero transcript height increases before ready, exactly one increase after ready,
the complete expected transcript, and final bottom distance at most one pixel. Then feed one live
message and prove the ordinary second incremental height/bottom update still occurs. This tests the
actual controller-to-Svelte seam without timing a production backend or changing the pane. The probe
must load production token/app styles, assert the final transcript genuinely has
`scrollHeight > clientHeight`, and await Svelte's post-tick scroll work after every animation frame;
failure of any precondition fails the test rather than allowing a false pass.

## Documentation and gates

Update `docs/chat.md` and the conversation paragraph in `docs/frontend.md` to say browser replay is
reduced privately and committed to the pane once at ready; individual wire envelopes and post-ready
live rendering remain incremental. Record the settled judgment in `decisions.md` and update
`PROGRESS.md` with RED/GREEN evidence.

Focused gates during implementation:

1. `cd web && node tests/conversation-controller.test.mjs`
2. `cd web && node tests/acp-browser-state.test.mjs`
3. `cd web && node tests/acp-browser-components.test.mjs`
4. `cd web && npm run check && npm run build`
5. `git diff --check`

After independent diff review reports no unresolved contract violation, run the repository's single
canonical `./verify` once on the settled tree.

## Owned files

- `web/src/lib/acp/conversationController.ts`
- `web/tests/conversation-controller.test.mjs`
- `web/tests/acp-browser-state.test.mjs`
- `web/tests/acp-browser-components.test.mjs`
- `docs/chat.md`
- `docs/frontend.md`
- `decisions.md`
- `PROGRESS.md`
- this ticket directory's plan/review/report files

## Non-goals

No Hub, queue, adapter, ACP contract, WebSocket frame, reducer shape, pane markup/scroll code, visual
state, cache, pagination, virtualization, replay limit, or backend test change. Preserve all dirty
nested worktrees and unrelated main-worktree edits.
