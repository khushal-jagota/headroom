# t_hs04 — Retire temporary product session drains and verify recovery

## Outcome

All production human-chat and employee execution paths use the ordered session ingress. The old
per-operation assumption that "the next session completion belongs to me" is removed, and session
cleanup, shutdown, restart, and resume are verified as one complete Panels-only feature.

## Public contracts

- Product callers cannot open raw temporary Hermes event drains or settle from an unowned
  session-level `message.complete`.
- The low-level gateway may retain one raw session-event primitive solely behind the ordered ingress
  and isolated transport smoke tooling.
- Idle session objects detach only after Hermes is observed idle and no Panels consequence is pending.
  Reopening uses the stored Hermes session ID and continues the same conversation.
- Chief and employee role gateways shut down independently and release all live session objects.
- Recovery uses Hermes's existing resume/history/running snapshot. Ambiguous transport gaps become
  honest unknown/offline outcomes; they do not trigger inferred success, failure, or automatic retry.
- Live documentation describes the two role gateways, per-live-session ingress, and the distinction
  between persistent Hermes sessions and detachable Panels listeners in plain language.
- The completed feature lands as one squashed commit on `codex/hermes-session-ingress` so it can be
  reverted atomically.

## Red-first acceptance tests

1. Static/contract coverage proves no production human-chat or employee caller opens a temporary raw
   session drain.
2. The Chief Stop-then-send and employee internal-overlap regressions pass together.
3. Multiple employee sessions remain isolated inside the employee role gateway; Chief remains
   isolated in its separately configured gateway child.
4. Idle eviction, explicit gateway shutdown, app restart, same-session resume, and child-death unknown
   outcomes are deterministic and leak no listener/thread.
5. Existing chat messages, commands, images, activity, worker context, direct revision, and employee
   settlement suites remain green without frontend presentation changes.
6. The authoritative `./verify` passes once on the integrated feature branch.

## Contract and implementation scope

- Final contraction of the gateway/session interfaces after both migrations.
- Focused lifecycle/recovery integration tests and current gateway/chat documentation.
- Serial integration and one authoritative full verification run.

## Explicit exclusions

- No Hermes modification, new identity protocol, UI redesign, transcript migration, durable event
  replay, generic message queue, or retry scheduler.

## Blocked by

- `t_hs02 — Move human chat onto the ordered session ingress`
- `t_hs03 — Move employee execution onto the ordered session ingress`
