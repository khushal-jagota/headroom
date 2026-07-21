# ACP-05 browser turn-boundary implementation review

## Verdict

`READY`

No concrete contract, regression, or proof finding remains in the ticket scope.

## Scope reviewed

- `orchestration/tickets/acp-05-browser-turn-boundary/contract.md`
- `orchestration/tickets/acp-05-browser-turn-boundary/implementation-plan.md`
- `orchestration/tickets/acp-05-browser-turn-boundary/implementation-report.md`
- the ticket-local change in `web/src/lib/acp/conversationState.ts`
- the ticket-local regressions in `web/tests/acp-browser-state.test.mjs`

The ACP frontend files are still untracked as part of the wider migration, so Git cannot isolate this
small correction from the earlier ACP-03 source. I therefore reviewed the exact helper/call sites and
the new regression blocks named by the implementation plan and report.

## Frozen-behavior review

- `closeMissingIdAgentGroup` clears exactly `activeMissingIdGroup` and
  `currentAgentMessageId`. It preserves `turnOrdinal`, `currentRole`, and `segmentOrdinal`, and does
  not touch transcript, timeline, receipts, queue, or optimistic human state.
- The activity reducer invokes that helper only for the exact terminal set `idle`, `interrupted`, and
  `failed`. Thinking, working, compacting, waiting-for-permission, and any other non-terminal
  activity continue without a split.
- The delivery reducer stores its receipt and persistent acknowledgement exactly as before, then
  closes the group only when the receipt state is `interrupted`. Accepted, queued, started, rejected,
  and steer acknowledgement do not split the response.
- The queued regression reproduces the observed ordering: optimistic queued prompt and matching
  human echo precede the predecessor's final missing-ID chunk; terminal idle closes that group; the
  successor's first missing-ID chunk receives a distinct fallback ID. It asserts both distinct IDs
  and the two exact texts.
- The Send Now regression proves an interrupted receipt separates consecutive missing-ID agent
  chunks. The non-terminal control proves thinking, working, accepted steer, queued, and started
  updates retain one response. The production condition itself is an exact terminal allow-list, so
  compacting and waiting-for-permission also remain non-splitting without another branch.

## Focused verification

Run independently:

```text
$ cd web && node tests/acp-browser-state.test.mjs
acp-browser-state.test.mjs: all assertions passed
```

The implementation report also records green component assertions, zero Svelte diagnostics, and a
production build directed to a temporary output directory. It correctly did not run `./verify` or
write generated distribution for this ticket.

## Findings

None.
