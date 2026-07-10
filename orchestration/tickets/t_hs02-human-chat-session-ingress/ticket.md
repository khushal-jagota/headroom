# t_hs02 — Move human chat onto the ordered session ingress

## Outcome

Chief, Ticket, and Day chat use the ordered Hermes session ingress for messages, model-backed
commands, images, and Stop. The current UI, visible transcript, streaming text, activity messages,
and system-message presentation remain materially unchanged.

A delayed completion from the operation being stopped can no longer finish or interrupt the next
human message.

## Public contracts

- Implement against `src/planner/chat/contracts.py`, `src/planner/minds/contracts.py`, and the chat
  gateway adapter contract.
- Keep Panels-visible human text distinct from model input, including hidden worker context and
  managed-image references.
- Stop may settle the visible product turn according to the existing UI contract, but the underlying
  Hermes session remains active until its ordered ingress observes Hermes idle.
- A new send goes directly to Hermes and preserves its `streaming`, `queued`, or `steered`
  disposition. Panels does not wait for its own idea of idle or create a local queue.
- Message, command, and image submission share the same session lane. Image attachment remains
  adjacent to its prompt submission and cannot be detached from that operation by another caller.
- Hermes activity and terminal events continue to project into the existing `ChatTurn` and message
  presentation. Product state does not become a second transport scheduler.

## Red-first acceptance tests

1. Reproduce the Chief failure: stop turn A, immediately submit B, receive A's delayed interrupted
   completion, then B's response. A remains interrupted and B receives only B's response.
2. The same sequence works for Ticket and Day human chat.
3. The visible transcript, roles, system activity, partial streaming text, and completed reply remain
   the same as before the transport migration.
4. Message, model-backed command, text-plus-image, and image-only sends all use the ordered ingress
   and preserve their existing visible/model-input split.
5. A queued or steered receipt is not represented as an independently completed response before
   Hermes supplies the relevant lifecycle observations.
6. Closing or reopening an idle chat session preserves its stored session ID and prior conversation.

## Contract and implementation scope

- `src/planner/chat/contracts.py`
- `src/planner/core/adapters/base.py`
- `src/planner/minds/contracts.py`
- Human chat service/gateway integration and focused API/browser regressions.

## Explicit exclusions

- No visual redesign, transcript-role redesign, Hermes change, new composer queue, new database
  status, or employee ticket-settlement migration.
- Do not hide or remove the system/activity information the current UI exposes.

## Blocked by

- `t_hs01 — Add one ordered ingress for each live Hermes session`
