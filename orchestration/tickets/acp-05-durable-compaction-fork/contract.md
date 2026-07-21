# ACP-05 durable compaction fork capture

> **Corrected after real Hermes dogfood:** the durable-fork requirement remains, but the same-child
> private-load and same-child publication clauses below are superseded by
> [ACP-05 asynchronous compaction handoff correction](../acp-05-compaction-private-load-hang/contract.md).
> ACP notifications are asynchronous; the corrected path forks on the source child, proves persistence
> by loading on a fresh unpublished child, and publishes that child as a new runtime generation.

## Why this ticket exists

Real Hermes dogfood made the existing compaction-capture assumption false. `/compact` visibly entered
`compacting`, Hermes compressed its in-memory history, and the prompt completed, but a replacement ACP
child loaded the old durable history and could not find a unique summary. The UI therefore showed
`Context failed · explicit`, and a process restart would restore the uncompressed conversation.

The upstream cause is in the read-only Hermes checkout: its ACP compact command temporarily disconnects
the live agent from its session DB while compressing, then its ordinary save path treats the restored DB
owner as if persistence already happened. Panels must not patch Hermes or infer its private database.
Hermes advertises the official ACP `session/fork` capability, and that operation persists a copy of the
current in-memory history. Panels will use that standards-based seam while the compacted history is still
resident in the leased child.

## Frozen behavior

### Official same-child capture

1. After the owning prompt response-consumption barrier closes and before retiring or replacing the live
   child, capture calls official ACP `session/fork` on that exact leased child and exact bound session.
   It does not call a Hermes-private method, inspect or write Hermes SQLite, synthesize a summary, or
   create a new blank session.
2. The initialized child retains whether the agent advertised `sessionCapabilities.fork`. Capture fails
   visibly and leaves the original binding/child usable when fork is not advertised. Backend definitions
   do not guess this runtime capability.
3. The fork response supplies the successor ACP session ID. The same child privately loads that fork
   through the existing ordered response-consumption/private-ingress boundary. Those replay updates are
   never published into the old browser epoch.
4. Hermes summary normalization remains the sole summary parser. Exactly one structurally valid summary
   in the private fork replay produces `compacted`; zero, multiple, rejected, or malformed candidates
   produce the existing visible failed capture. A compacted result is never fabricated from token counts
   or the command's human-readable completion text.

### Durable binding transition

5. A structurally valid fork is durably installed with one compare-and-swap from binding generation N to
   N+1, retaining the employee and backend and using the fork session ID. The registry publishes the new
   runtime record only after the repository and its existing Ticket/Chief mirror contain that exact
   binding. The child generation and record identity remain unchanged for the normal same-child winner.
6. Browser publication then transitions atomically to the new binding: `reset` at N+1, the fork's typed
   replay in order, `ready`, the exact current queue state, and the normalized compaction completion. No
   N-bound update may appear after the N+1 reset, and no N+1 ordinary publication may target the old
   stream. Attached browsers all observe the same epoch transition.
7. Pending FIFO intent survives the binding change. Every queued prompt is retargeted to the complete
   N+1 runtime handle and fork session ID, remains visibly queued with exactly one human echo in the new
   epoch, and starts once only after capture publication and idle settlement. Send Now/new-conversation
   cancellation retains the broker's existing single-owner ordering.
8. Tracked worker completion does not settle successful until the fork, private replay, durable CAS,
   browser transition, and normalized completion all finish. Explicit and automatic compactions use the
   identical transition path.

### Failure and race ownership

9. Fork failure, private-load failure, invalid replay, or binding-CAS failure never publishes a compacted
   boundary. If the child was switched to the fork but N remains durable, Panels reloads N before
   continuing; inability to restore exact N is generation-fatal. A recoverable capture failure publishes
   the existing failed boundary, returns to idle, and advances the FIFO without duplicating delivery.
10. A CAS loser does not publish its unused fork or delete it through a non-standard/private API. It adopts
    the exact durable winner through normal registry machinery, transitions the browser to that winner,
    and does not call the orphan a successful compaction. An unused fork may remain in the backend because
    ACP provides no required transactional create-and-delete primitive.
11. Child death, cancellation timeout, permission cancellation, terminal cleanup, ingress overflow, and
    hub shutdown keep their existing fail-closed deadlines and exact-generation ownership. No transition
    hook may call back into the same hub employee sequencer reentrantly or leave an admitted waiter/task
    pending.
12. The previous retire-child/load-old-session capture path is deleted once the fork path is proven. There
    is one compaction capture implementation, not a backend-specific fallback. The Hermes checkout remains
    strictly read-only.

The separate observation that Hermes reported a larger post-compaction token estimate is not in this
ticket. Panels reports ACP state faithfully; it does not judge or rewrite the backend's compression
quality.

## Required proof

- SDK child tests prove the advertised fork capability is retained, official `session/fork` receives the
  exact session/cwd/directories/servers, ordered private load closes its response barrier, and missing
  capability fails before an RPC.
- Registry tests prove same-child fork → private load → binding CAS N→N+1, atomic mirror-before-record
  publication, exact-handle rejection, restoration after post-load failure, CAS-winner adoption, and no
  private/ordinary ingress leakage.
- Broker/hub tests prove explicit and automatic capture share this path; all attached browsers receive
  reset → typed replay → ready → queue/compaction in order; old-source updates are rejected; tracked
  settlement waits; queued and Send Now successors are retargeted and delivered once; capability/capture
  failure remains visible and does not strand the FIFO.
- A real official-SDK scripted-agent e2e advertises fork and durably stores the compressed fork. It proves
  explicit compaction, browser refresh, child death/restart, and a fresh `session/load` all replay the
  unique summary from the N+1 binding. The same fixture proves automatic compaction and queue succession.
- Focused Ruff, strict Mypy, the affected Python suites, ACP browser state/component suites, Svelte
  diagnostics, and a temporary production frontend build pass. Do not run canonical `./verify`; ACP-10
  owns the one final run.

## Allowed files

- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/sdk_child.py`
- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/turn_broker.py`
- `src/planner/conversation/hub.py`
- `src/planner/conversation/composition.py`
- `src/planner/conversation/__init__.py` only if an internal exported port/type changes
- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/e2e/test_acp_conversation.py`
- `tests/support/acp_scripted_agent.py`
- this ticket's plan/report/review/evidence files
- `PROGRESS.md`
- `decisions.md`

No wire schema, browser reducer/component, shared visual asset, generated distribution, config, unrelated
domain, runtime database, or Hermes-checkout file is in scope. If implementation proves another production
file is mechanically required, stop and amend this contract with the reason before touching it.
