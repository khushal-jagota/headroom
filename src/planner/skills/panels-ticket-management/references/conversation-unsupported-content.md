# Diagnosing generic unsupported content in Panels conversations

Use this when a Ticket or Chief conversation shows **unsupported content** and the user asks what was sent.

## Distinguish the two surfaces

- **Unsupported update** is a server-side protocol rejection. Its envelope is `protocol_update_rejected` and includes the rejected `sessionUpdate` discriminator plus a reason.
- **Unsupported content** can be generated only by the browser reducer. It does not prove that the backend sent an unknown ACP payload.

## Grounded diagnostic

1. Read the live Ticket and events first. Do not infer the payload from the pill text.
2. Attach read-only to `/api/conversation` with `{"type":"attach","employeeId":"<ticket-id>"}` and capture the reset/replay envelopes through `ready`.
3. Inspect ACP `sessionUpdate` discriminators and tool-call identities in sequence.
4. Compare the replay with the browser reducer’s unsupported branches.

A common replay-consistency case is a `tool_call_update` whose matching `tool_call` start is absent from the reset snapshot. The update itself may be valid and contain ordinary text content, but the browser has no pending tool call to patch and emits generic **Unsupported agent content**. Report this as an orphaned replay update—not an exotic Worker payload—and include the sequence, tool-call ID, kind/status, and the ordinary result that was carried.

Also check for intentionally unsupported `plan_update` / `plan_removed` updates and genuinely unknown runtime discriminators before concluding the orphan case.

## Safety

This is read-only diagnosis. If the Ticket still has a live turn, do not release, retry, interrupt, or mutate it merely because the transcript shows unsupported content.
