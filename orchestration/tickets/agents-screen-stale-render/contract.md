# The Agents screen can render a snapshot it never leaves

## Why this ticket exists

On a server whose answers come back fast, the Agents screen can paint "Loading agents..."
and stay there. It is not waiting for anything: its three reads have all finished, the
data is in the cache, and the screen simply never re-renders to look at it. Only a reload
clears it.

This is a real user-facing failure and it is likelier the faster the server is — so it is
likelier on the VPS, where the browser and the server are the same machine, than on any
development laptop.

## What is known

Measured in a browser against a cold server, in the stuck state:

```
["workers"]      status=success  fetchStatus=idle  hasData=true   observers=1
["worker-types"] status=success  fetchStatus=idle  hasData=true   observers=1
["skills-home"]  status=success  fetchStatus=idle  hasData=true   observers=1
```

and the screen is showing its loading line. `ResourceState` renders that line when
`loading && !hasData`, so the component is reading `isFetching` true and `data` undefined
from queries that are neither.

- **Only this screen.** Day, Backlog and Sprint were checked against the same cold server
  in the same state and all rendered correctly.
- **Timing is the trigger.** Whether it happens depends on how quickly the three reads
  resolve. Anything that makes them slower hides it; anything that makes them faster
  brings it out.
- **The change stream is what rescues it.** `startChangeStream`'s open handler invalidates
  every query, which refetches and re-notifies. Suppress that one call and the screen is
  stuck every time — which is the reproduction below.

## Reproduction

Suppress the invalidation in `startChangeStream`'s `onopen` (return before
`invalidateEverything()` on the first open), rebuild, then open `#/agents` as the very
first request to a freshly started server. It stays on the loading line. Do the same on
`#/day` and it renders.

## What was ruled out

Each of these was tried and reverted; none of them is the cause.

- The three reads returning different data — the payloads are byte-identical between a
  server where it hangs and one where it does not.
- A request never arriving or never finishing — all three complete with full bodies.
- The `#/workers` → `#/agents` redirect — it hangs on the direct route too.
- `enabled` on the three queries — removing it entirely does not fix it.

`AgentsRoute` and `TicketRoute` are the only routes that freeze a prop with `untrack`, and
`AgentsRoute` is the only one that then drives `enabled` from it. That is the remaining
structural difference from the screens that work, and it is where to look next.

## Done when

The reproduction above renders the screen, and the screen no longer depends on the change
stream's open handler to be correct.
