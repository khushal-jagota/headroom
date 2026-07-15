# Codex plan review and disposition

Review command: `codex exec`, model `gpt-5.5`, reasoning effort `high`, read-only sandbox.

## Full review output

> - `implementation-plan.md:180` lacks acceptance proof for `contract.md:67`:
>   acknowledgement fully sent before child termination. The plan asserts the order in
>   implementation, but the e2e only checks eventual acceptance output and eventual child
>   replacement, not that the old child has not received `SIGTERM` before the restart client has
>   received the accepted response.
>
> - `implementation-plan.md:154` lacks acceptance proof for stale-socket safety before lease
>   acquisition. It tests a second supervisor with a different control socket, which proves the
>   lease is port-scoped, but it does not prove a failed second supervisor using the same live
>   socket path leaves the first supervisor's socket intact and still usable. This is the risky
>   case for “stale socket may be removed only after the lease is acquired”.
>
> - `implementation-plan.md:184` does not prove restart from a Ticket worktree as called out by
>   `contract.md:50`. It uses an empty temporary directory; that covers generic cwd isolation, but
>   not the explicit worker/Ticket-worktree scenario where an alternate worktree may contain its
>   own repo files and config.

## Disposition

All three findings are accepted.

1. The control exchange now stays open after the compatible accepted response. The supervisor may
   not return the restart decision until the accepted client closes the exchange. A real-socket
   process test holds that client open and proves the old child remains alive.
2. The duplicate-owner slice now covers both different-socket and same-live-socket attempts. The
   latter must leave the original socket usable for a real restart command.
3. The caller-isolation fixture is now Ticket-worktree-shaped and carries conflicting local config
   plus an absent frontend build. The replacement must still serve the supervisor launch root.

## Corrected-plan re-review

The same reviewer configuration returned:

> NO VIOLATIONS
