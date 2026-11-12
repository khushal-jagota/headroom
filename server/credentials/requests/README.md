# Credential requests

An employee requests an API key through public metadata in its current conversation.
The owner saves a value or rejects the request. Panels delivers that outcome back
to the same conversation with a stable sender identity. A saved value does not
prove that the provider accepts it.

## Public operations

`routes.ts` registers these six routes beneath `/api`:

| Method and path | Result |
| --- | --- |
| `POST /credentials/requests` | Create or reuse an employee request, then deliver an immediate reuse outcome when applicable. |
| `GET /credentials/requests/:request_id` | Read the public metadata snapshot. |
| `POST /credentials/requests/:request_id/save` | Save or replace an encrypted credential and deliver its reference. Owner only. |
| `POST /credentials/requests/:request_id/reject` | Reject a pending request and deliver that outcome. Owner only. |
| `POST /credentials/requests/:request_id/review` | Refresh the replacement revision after a stale credential conflict. Owner only. |
| `POST /credentials/requests/:request_id/retry-delivery` | Retry an outcome whose delivery was safely refused. Owner only. |

`actions.ts` owns `create`, `save`, `reject`, `review`, and `retry`. Each receives
the caller's transaction capability and returns an `EventMutation`. Actions do
not open another database connection, commit, await, or deliver a prompt.
`origin_conversation` checks both the current employee association and the
conversation's stored identity. A request never changes its origin after reset.

`data.ts` owns `read`, `row_for`, `update`, and `settle_attempt`. Public snapshots
reuse the generated Python contract and the canonical conversation decoder.
The existing credential store owns encryption, revisions, and plaintext access.

## Delivery and transaction boundaries

An outcome first commits its public snapshot and an `intent` delivery attempt in
the conversation's record transaction. The attempt stores a stable sender identity
and a prompt that contains the outcome and credential reference. Only after that
commit does `CredentialRequestOperations.dispatch` call the real core's `send`.
The core's receipt and the resulting request card settle in record transactions.

`areas.ts` binds `data.observe_events` as the record's event participant. It finds
the exact attempt by sender identity and origin conversation, settles its receipt,
and returns the updated request-card event within that same transaction. A late
queued HTTP result cannot replace a real prompt receipt. An obsolete attempt can
settle its own row without replacing the newer attempt on the request card.

`data.reconcile_deliveries` is the startup participant. It settles lost `intent`
and `queued` attempts from their latest exact durable receipt. Without such a
receipt, their outcome is `uncertain`. A backend write failure also means
`uncertain`, because a failed write does not prove that the provider received no
bytes. Startup does not blindly send those outcomes again. Safe refusals alone
enable the owner's retry operation.

Dispatch takes the Manager lifecycle lock before the association lock when the
requester is a Manager. Other requesters take their association lock directly.
It checks the original association again under that lock before the send. A
retired origin receives `requesting_conversation_retired`, rather than a delivery
to a newer conversation.

`shield_operation` admits each complete action and dispatch through the same
`ConversationHttpWriteScope` as the conversation routes. Shutdown seals that scope
before it drains. A late request cannot start another credential mutation, while
an admitted operation remains tracked through its final delivery settlement.
Browser departure does not cancel that work. Workspace admission still gates each
operation. `finish_operations` joins the retained task set before the core closes.

## Privacy and resource boundary

Only metadata fields enter request rows, events, and prompts. The API key value
goes directly to the existing encrypted credential writer in the same save
transaction. Private body parsing does not reflect the value in an error. Returned
snapshots contain a public credential reference and revision, without its secret.

The existing Unit 13 resource provider already supplies the key for the bounded
`/api/credentials` list. This package reuses that provider. The ordinary credential-request
detail route has no resource key. Request-card updates use the existing
conversation event and view pipeline.

## Verification boundary

`tests/compare/direct/test_unit_19_credential_requests.py` passes against Python.
It compares exact returned events and source rows for receipt races, obsolete
attempts, write failures, and startup recovery. It uses synthetic rows and a fixed
SQL clock, without a listener or provider. The complete HTTP comparison through
the real core passes with no differences. It proves actual prompt delivery, card
projections, and persisted attention. The unchanged hosted credential-wire browser
proof remains pending for reload and private-wire behavior.
