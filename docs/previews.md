# Previews

A preview lets the owner open a running application from a Ticket. The application runs on the assigned machine and the link stays on the existing tailnet. A document or saved HTML artifact uses the Ticket's file preview instead.

## What it is made of

- **A registry.** One preview per Ticket, each with a fresh generation in its address: `/dev/tickets/<ticket-id>/<generation>/` on the existing tailnet HTTPS origin.
- **A proxy inside Headroom.** It routes that generation to the assigned machine. Machine code forwards requests to the Worker's dev server on loopback. It does not rewrite pages or strip the prefix, so the app must serve under that exact base path.
- **The Worker's commands.** `panels preview prepare`, `register`, `show`, and `stop`, which act only on the Worker's own Ticket. The steps a Worker follows are in [ops/runbooks/application-preview.md](../ops/runbooks/application-preview.md).

```
 Reader ──tailnet HTTPS──► server ──restricted relay──► machine code ──loopback──► application
                            │                              │
                            └── Ticket and generation      └── local process evidence
```

## How it works

The Worker prepares a preview, which reserves the address and names the worktree the app must run from. It starts the app on loopback under that base path, then registers the port. Machine code checks that the listener belongs to that worktree. It opens an authenticated connection to the server for HTTP traffic and WebSockets, including hot reload. The reader needs no direct connection to the machine.

A generation ends when the Worker stops it, when the process exits or the port is reused, when the Ticket is done or deleted, or when the relay disconnects, when machine code restarts, or when the server restarts. An old address shows an ended preview. A restarted app needs a new generation.

Previews need trusted Tailscale ingress to be configured on the live host. Without it there are none. Previews run on the Ticket's assigned machine, on Linux x86_64 or Apple Silicon. Machine code retains native process evidence. The server keeps the destination, generation, and preview state. The Tickets of the server's own machine also run through machine code, so they use the same path. The server never checks an application folder or process on its own disk.

## Native Mac evidence

The Mac inspector reads libproc evidence for listener ownership, exact process birth, boot identity, and the current worktree. Before the proxy sends bytes, it checks that the same process owns the accepted connection. A PID or command string alone grants no ownership.

Discovery requires one readable same-user owner. It skips peers whose permissions block inspection. The pinned process and its accepted connection must stay readable. Incomplete inventories and expired deadlines refuse the preview. Hidden holders remain outside that observation. Linux and Mac use the same registry, limits, and retirement rules.

The lifecycle checks active previews once per second and after record changes. Mac discovery scans bounded process and descriptor inventories only at registration. Later checks read the pinned process. No extra Worker loop exists.

## Why the content has to be trusted

Preview pages run in Headroom's own browser origin. Their scripts can call Headroom's APIs and read its storage. The app runs under the same account as machine code. The registration check proves where a process runs from. It does not prove who wrote it or contain it. Never put a vendor demo, a downloaded sample, or a page with third-party scripts behind a preview.

The tailnet is the gate: only tailnet members reach the origin. It is not local authentication. Any process on the host can reach a loopback port.

## The load it places on Headroom

Preview traffic passes through the server and machine code. Headroom caps concurrent requests and WebSockets, globally and per preview, and limits transfer. Streams share the transfer limit in the order they ask, so a busy preview cannot starve one of its own requests. Excess gets a busy response at once, with no queue. These bounds protect ordinary Headroom work from the proxy. They do not limit the app's own CPU or memory.

## Decisions that shape it

- Previews run in the same origin as Headroom. Use them only for trusted content.
- One fixed worktree per Ticket and a fresh generation per run, so an old link never reaches a new process.
- No rewriting. The app adapts to the base path, which keeps the proxy simple and honest.
- Bounded inside Headroom so a busy preview cannot starve the workspace.

## Code

- `server/previews/`: registry, proxy, process checks on Linux and the Mac, and the CLI. Limits are in `config.ts` and `limits.ts`.

_Last verified: 2026-11-22._
