# ACP-03 plan review — round 1

## Findings

### 1. Blocker — normal Hermes session metadata is planned as an unsupported-content error

`plan.md:143-159` sends `current_mode_update`, `config_option_update`, and
`session_info_update` to an **Unsupported agent content** row/status. Those are known members of the
pinned SDK `SessionUpdate` union, not unknown or partial protocol updates. More importantly, the
reference study records Hermes emitting model/mode updates during an ordinary session
(`research.md:102-105`). With the status precedence at `plan.md:382-385`, a normal Hermes attach can
therefore produce the persistent error treatment reserved by the contract for malformed/unknown
updates, leaving the pane falsely reporting unsupported agent output.

Handle these three stable SDK updates as typed non-transcript session metadata (the donor already
has `configOptions`; current mode and session info can remain equally small typed state). They must
not become assistant text, a transcript error row, or `protocol_update_rejected`. Keep the calm
unsupported row for genuinely unrenderable tool/content values and the frozen protocol-rejection
path for unknown/partial updates. Add a Hermes-shaped fixture containing mode/config/session-info
updates and assert that it changes only the corresponding typed state and raises no unsupported
status. The unstable `plan_update` / `plan_removed` variants may remain explicitly unsupported for
this slice because ACP-03 implements the stable full-snapshot `plan` contract.

### 2. High — the composer cannot implement the frozen Steer capability rule from its declared props

`plan.md:359-361` gives `AcpComposer` commands, queue, receipts, activity, and the employee label,
but `plan.md:370-374` then requires that same component to enable Steer from `supportsSteer`. The
value exists in connection state, yet it is absent from the component contract. That leaves the
implementation either reading undeclared state or inferring capability through a forbidden source.

Add the exact `supportsSteer: boolean` connection value to `AcpComposer`'s props and pass it from the
snapshot through `AcpConversationPane`. Component tests must cover both values while keeping Queue
and Send Now enabled in both cases. ACP-00b's required connection field remains the only source.

### 3. High — reconnect errors have no successful-recovery transition

The plan creates persistent gap/browser errors at `plan.md:240-242,269-272`, says activity never
clears them at `plan.md:163`, and gives them highest status precedence at `plan.md:382-385`. It never
defines when a successful replay/reset/ready transition resolves a recoverable error. After the
controller has reattached and rebuilt a complete typed state, the pane can therefore continue to say
**Conversation updates were missed. Reconnecting…** or **Conversation data could not be read.
Reconnecting…** indefinitely.

Separate recoverable connection/gap errors from the intentionally persistent
`protocol_update_rejected` status. Freeze the recovery rule: retain the recoverable error while the
bad epoch is closed and replay is incomplete, then clear or replace it only after an admitted
reset/replay/ready sequence proves continuity. Add the corresponding fake-socket assertion. A
protocol-rejection row/status must remain persistent and must not be cleared by this recovery rule.

### 4. Medium — replacing mutable donor collections does not make the public snapshot immutable

`plan.md:81-103` promises that subscribers never receive a mutable internal collection, but the
planned public `session: SessionData` uses donor fields currently declared as mutable arrays and a
mutable `Map` (`sessionStore.ts:6-15`). Copy-on-write prevents reducer aliasing; it does not stop a
consumer from calling `snapshot.session.pendingToolCalls.set(...)` or mutating an exposed array and
silently changing the controller's current snapshot.

Make the exported pure-state surface recursively readonly at its collection boundaries
(`readonly` arrays/records and `ReadonlyMap`, or an equivalent immutable projection) while the
Zustand compatibility delegate may keep its own mutable store-facing types if required. Add a
compile-time/source-boundary assertion and a runtime snapshot test proving consumer mutation cannot
change a later `snapshot()` result.

## Confirmed sound

- The plan uses one exhaustive ACP dispatcher over corrected donor helpers rather than introducing a
  second transcript reducer. Thought isolation, deterministic missing-ID grouping, text-boundary
  ordering, stable plan replacement, tool reconciliation, and donor provenance are concrete.
- The ACP-00a `terminal_state` disposition is correctly carried through keyed accumulated output,
  exit/signal/truncation/release rendering without transcript prose.
- Outer-envelope validation is strict while SDK-owned payload schemas remain imported rather than
  copied. Sequence admission, reset-only generation advance, optimistic echo, authoritative FIFO
  queue state, exact permission options, and disposal all have focused fixture proof.
- The nine-component Svelte 5 inventory is feasible with the existing `MarkdownBlock`,
  `FilePreview`, `ChatComposer`, and follow-scroll mechanics. The component-local, existing-token,
  cardless visual rules respect the owner direction and do not import acp-ui styling.

## Verdict

**NOT READY** — resolve findings 1-3 before implementation. Finding 4 is a bounded contract
correction and should be incorporated in the same amendment; it does not justify another broad
review round once the orchestrator records the dispositions.
