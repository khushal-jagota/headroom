# Implementation plan

1. Extend the durable conversation row so it can represent a Panels conversation generation with
   no ACP session. Keep `ConversationSessionBinding` strict and derive it only when the row contains
   a real ACP session id.
2. Add repository operations to ensure an initial empty generation, atomically advance **New** to
   another empty generation, snapshot launch configuration, and atomically bind the first ACP
   session to the current generation.
3. Make browser attach branch on that durable state. Bound state follows the existing replay path;
   empty state gets only an empty reset/ready bootstrap and never enters the ACP registry.
4. Make the first prompt activate the current empty generation, move all attached browsers through
   the ordinary bound reset, then publish the echo and deliver through the existing broker.
   Automatic Employee delivery already enters through `ensure_employee_stream`, so it uses the same
   activation operation.
5. Change browser prompt actions to carry content blocks rather than a fabricated ACP
   `PromptRequest`. Panels constructs the real request after a binding exists. Allow the envelope's
   ACP session id to be null only for empty connection envelopes.
6. Treat a stale browser generation cursor as a request for a complete current snapshot.
7. Add focused repository, hub, WebSocket, controller, and e2e regressions; then run the canonical
   `./verify` once on the settled tree.
