# Previews

A preview is one link Khushal can open to an application a Worker is running on the Headroom host. It exists so he can try a running dev server from his own browser without anything new being exposed to the internet. A document or saved HTML artifact uses the Ticket's file preview instead.

## What it is made of

- **A registry.** One preview per Ticket, each with a fresh generation in its address: `/dev/tickets/<ticket-id>/<generation>/` on the existing tailnet HTTPS origin.
- **A proxy inside Headroom.** It forwards that generation to the Worker's dev server on loopback. It does not rewrite pages or strip the prefix, so the app must serve under that exact base path.
- **The Worker's commands.** `panels preview prepare`, `register`, `show`, and `stop`, which act only on the Worker's own Ticket. The steps a Worker follows are in [ops/runbooks/application-preview.md](../ops/runbooks/application-preview.md).

```
 Khushal's browser ──tailnet HTTPS──► Headroom proxy ──loopback──► Worker's dev server
                                        │                            (Ticket preview worktree)
                                        └── registry: Ticket, generation, observed process
```

## How it works

The Worker prepares a preview, which reserves the address and names the one worktree the app must run from. It starts the app on loopback under that base path, then registers the port. Headroom checks that the listening process really comes from the reserved worktree before it hands back the link.

A generation ends when the Worker stops it, when the process exits or the port is reused, when the Ticket is done or deleted, or when Headroom restarts. An old address shows an ended preview. A restarted app needs a new generation.

Previews need trusted Tailscale ingress to be configured on the live host. Without it there are none. Tickets that run on the Mac cannot have previews, and an existing preview ends if its Ticket moves to the Mac. Nothing forwards to the Mac.

## Why the content has to be trusted

Preview pages run in Headroom's own browser origin. Their scripts can call Headroom's APIs and read its storage. The app already runs as the same Unix user as Headroom. The registration check proves where a process runs from. It does not prove who wrote it or contain it. Never put a vendor demo, a downloaded sample, or a page with third-party scripts behind a preview.

The tailnet is the gate: only tailnet members reach the origin. It is not local authentication. Any process on the host can reach a loopback port. See [Invariants](invariants.md).

## The load it places on Headroom

Preview traffic runs inside Headroom's process. Headroom caps concurrent requests and WebSockets, globally and per preview, and limits transfer. Excess gets a busy response at once, with no queue. These bounds protect ordinary Headroom work from the proxy. They do not limit the app's own CPU or memory.

## Decisions that shape it

- Same origin, trusted content. Previews are a convenience for Khushal's own Workers, not a sandbox.
- One fixed worktree per Ticket and a fresh generation per run, so an old link never reaches a new process.
- No rewriting. The app adapts to the base path, which keeps the proxy simple and honest.
- Bounded inside Headroom so a busy preview cannot starve the workspace.

## Code

- `src/planner/previews/`: registry, proxy, process checks, and the CLI. Limits are in `config.py` and `limits.py`.

_Last verified: 2026-11-04._
