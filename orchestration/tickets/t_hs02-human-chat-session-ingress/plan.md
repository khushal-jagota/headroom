# t_hs02 implementation plan — human chat on ordered session ingress

## Purpose of this slice

Move every human-facing Hermes operation behind the `t_hs01` live-session boundary while keeping
the `GatewayAdapter`, `ChatTurn`, API, database, and frontend behavior stable. Chief, Ticket, and Day
chat continue to render the same human, assistant, system, partial-output, and activity rows. This
ticket changes who owns Hermes observations, not what the user sees.

`t_hs01` must land first. Use its final public `LiveSession`/manager names rather than creating a
second session abstraction.

## Required session consequence seam

The hs01 session queue is the only consumer of raw ordered observations. Extend that boundary with
the smallest consequence handle needed by callers: submitting a prompt returns Hermes's immutable
`SubmissionReceipt` plus a handle that receives only the observations relevant to that accepted
submission and can be released when its product consequence is settled.

This registry is observational, not a Panels queue:

- `streaming` owns the newly started lifecycle;
- `queued` remains pending until Hermes emits the next lifecycle after the current lifecycle ends;
- `steered` joins the current Hermes lifecycle, so its observations may also be relevant to the
  already-active submission;
- no handle sends later, promotes work, waits for Panels-inferred idle, retries, or stores prompt text
  after Hermes has accepted it.

An interrupt receipt never closes a consequence. A paused product turn may settle immediately, but
its consequence remains registered until the ordered ingress observes its terminal/idle lifecycle.
This is what lets A absorb A's delayed interrupted completion while B waits for B's own lifecycle.

If hs01 already exposes this shape, use it unchanged. Otherwise add the immutable handle/receipt
contract in `planner.minds.contracts` and its session-owned routing in
`planner.minds.sessions.service`; do not add a turn ID, database status, timer, or FIFO.

## RED tests first

Add public-behavior tests before migration. Assert API state, adapter chunks, and emitted Hermes
frames; do not test private manager maps.

1. **The reported race.** With the real `SharedGateway` over the scriptable child, submit A, interrupt
   it, immediately submit B, then emit A's delayed `message.complete(status="interrupted")` followed
   by B's start/delta/complete. A's consumer receives A only; B remains open until and receives B
   only. Parameterize B's native receipt over `streaming` and `queued` where the event sequence is
   valid.
2. **All human entities.** Drive the same stop-A/send-B sequence through the FastAPI chat API for a
   Ticket, a canonical Day ID, and `agent_panels_chief_of_staff`. Assert A stays interrupted, B
   completes with B's text, and no old completion becomes B's message or error.
3. **Steer semantics.** A `steered` receipt does not manufacture a second completion. It follows the
   current lifecycle observations Hermes actually emits and settles only from those observations.
4. **Presentation parity.** A start, tool activity, deltas, and completion still produce the existing
   `session`/`activity`/`token`/`done` chunk sequence and the same `ChatState`: human input, partial
   output, activity label, assistant reply, and system output roles are unchanged. A paused partial
   reply remains visible once and the later terminal event cannot overwrite or duplicate it.
5. **Every input path.** Prove ordinary message, sync send, model-backed command, text-plus-image, and
   image-only submission all obtain an ingress-owned consequence. Pure display commands still return
   one system result without pretending to have a model lifecycle.
6. **Exact worker-context acknowledgement.** Parameterize human message, model-backed command, and
   image submission over accepted `streaming`, `queued`, and `steered` receipts. In every case the
   exact prepared context key/revision pairs are acknowledged once after acceptance; the visible
   human/command/image transcript remains the original product text while only Hermes receives the
   prepared model text. `TransportUnknown` retains those receipts, sends no retry, and—on the image
   path—does not guess whether compensating detach is safe.
7. **Image adjacency.** Two concurrent same-session image submissions cannot interleave
   `image.attach` and `prompt.submit`; each attachment is immediately paired with its own prompt
   write. Attach failure sends no prompt. A known prompt rejection detaches the image; an unknown
   delivery does not retry or issue speculative cleanup.
8. **Receipts are not completions.** For `queued` and `steered`, withhold lifecycle observations and
   assert no `done` chunk and no completed ChatTurn appears merely because the RPC acknowledged.
9. **Idle reopen.** After a completed turn and observed idle, close/reopen the product chat and send
   again. The stored session key is unchanged, `session.resume` is used, and prior visible messages
   remain intact.
10. Keep the existing command, image, session-key race, pending worker-context, pause, navigation, and
   live-chat browser tests green. Add one browser regression using the slow fake: pause A, send B
   immediately, and verify the current appearance plus exactly one B reply after remount.

## Implementation shape

### 1. Project ordered observations into accepted submissions

Keep one router per role gateway and one ordered ingress per live session. The session layer owns the
small amount of disposition-aware consequence routing described above. Each accepted submission gets
one bounded observation stream; releasing it decrements hs01's pending-consequence count so observed
idle may become dormant.

Route lifecycle events in session order. `message.start`, tool/activity events, deltas, terminal
messages, and `session.info` remain unmodified observations. A queued consequence cannot claim the
active consequence's terminal event; it becomes current only when Hermes starts its next lifecycle.
An intentional steer may share the active lifecycle. Unknown submission delivery remains unknown
and is never guessed into another attempt's completion.

### 2. Migrate `SharedGateway` human operations

Preserve the public `GatewayAdapter` signatures. Internally:

- `send()` consumes the new human prompt consequence instead of `_submit_and_drain`;
- `stream(..., mode="message")` maps that consequence's observations to the existing chunks;
- `run_command()` and `stream(..., mode="command")` run `slash.exec`/`command.dispatch` through the
  live session's ordered command-write seam; model-backed `skill`/`send` results then submit through
  the same consequence path, while `exec`/`plugin`/display results remain system output;
- `interrupt()` resolves the stored key to the bound live session and uses its ordered interrupt
  operation. It returns after Hermes acknowledges, without consuming or inventing a terminal event;
- `history()` and resume/create continue to bind through hs01 and preserve stored-key rotation.

Leave employee `run_ticket_step()` and its compatibility drain untouched for `t_hs03`. Human paths
must no longer call `open_session_events` or accept the next session-wide completion as their own.

Keep worker-context preparation at the actual model submission boundary. Acknowledge its exact
key/revision receipts once, and only after Hermes returns an accepted `streaming`, `queued`, or
`steered` disposition, across message, model-backed command, and image paths. Known rejection and
unknown delivery retain the context. Prepared context and image-only cues remain model input only;
they never replace the product-visible human text or managed-image Markdown.

### 3. Keep image delivery one operation

Add an optional pre-submit attachment to the existing live-session submission operation (or use the
equivalent hs01 seam if it lands there). Under the per-session outbound lock, complete
`image.attach`, register the consequence, and write `prompt.submit` before another same-session
submit/interrupt write may interleave. Release the lock before waiting for the prompt response or any
model observations.

On attach failure, do not submit. On a definitely rejected prompt, run the existing `image.detach`
compensation. On timeout/child loss, preserve the honest unknown result: do not retry the prompt or
guess whether detaching is safe.

### 4. Preserve the chat projection

Keep `start_human_turn`, `_run_human_turn`, `pause_turn`, and `chat.data` as the product projection.
Their current rules remain load-bearing:

- persist/attach the stored session key before prompt submission;
- store visible human text separately from worker-context suffixes and image-only model cues;
- map activity to `doing`, deltas to `responding`, assistant completion to the existing message row,
  and pure command output to `system`;
- Pause settles the visible turn as `interrupted` with partial output and does not change Ticket
  runtime status;
- a delayed terminal callback for an already-paused turn is an idempotent no-op at the writer.

No new API field or frontend branch is needed. Change chat contracts or adapter signatures only if
the hs01 consequence handle cannot remain private to `SharedGateway`; prefer keeping it private.

## Concurrency invariants to inspect

- Raw Hermes observations have exactly one consumer: the hs01 session router.
- Product consumers read consequence handles, never the session-wide queue.
- The same-session outbound lock preserves attach/submit and command/submit write order, but is not
  held while waiting for model lifecycle observations.
- Different live sessions remain independent; Chief and employee gateway children remain separate.
- Interrupt acknowledgement does not release the old consequence or mark the transport idle.
- Dormancy requires observed Hermes idle and zero unreleased consequences; the durable stored key is
  never deleted.

## Implementation order and gates

1. Add the race, disposition, projection, exact worker-context acknowledgement, and image-adjacency
   RED tests.
2. Add/complete the disposition-aware consequence handle in the hs01 session module.
3. Migrate message streaming and sync send; make the Chief/Ticket/Day race green.
4. Migrate commands, images, and interrupt; keep all visible/model-input splits and compensation
   behavior green.
5. Run focused minds/chat/API tests, the live-chat and image Playwright files, Ruff, Mypy, and
   `git diff --check`.
6. Run the authoritative full `./verify` once after hs02 lands. Record full output in the ticket
   report; do not re-run merely to quote it.

## Permitted files

- `src/planner/minds/contracts.py`
- `src/planner/minds/sessions/service.py`
- `src/planner/minds/shared_gateway.py`
- `src/planner/chat/contracts.py` only if an outward shape is genuinely required
- `src/planner/chat/service.py` only for projection glue needed by the new consequence result
- `src/planner/core/adapters/base.py` only for the stable adapter contract/documentation
- focused unit/API tests under `tests/unit/`
- `tests/e2e/test_live_chat_state.py`
- this ticket's plan/review/report files

Do not change Hermes, `src/planner/core/server.py`, database schema/migrations, frontend source,
employee settlement, or unrelated documentation in this ticket.
