# Previews

A preview is one link Khushal can open to an application a Worker is running on the Headroom host. It exists so he can try a running dev server from his own browser without anything new being exposed to the internet. A document or saved HTML artifact uses the Ticket's file preview instead.

## What it is made of

- **A preview address.** Tailscale Serve publishes one more HTTPS port on Headroom's tailnet name, such as `https://<host>.ts.net:8460`. It leads to the same Headroom server. Headroom answers previews only there, and nothing else there.
- **A registry.** One preview per Ticket, each with a fresh generation in its path: `/dev/tickets/<ticket-id>/<generation>/` on the preview address.
- **A proxy inside Headroom.** It forwards that generation to the Worker's dev server on loopback. It does not rewrite pages or strip the prefix, so the app must serve under that exact base path.
- **The Worker's commands.** `panels preview prepare`, `register`, `show`, and `stop`, which act only on the Worker's own Ticket. The steps a Worker follows are in [ops/runbooks/application-preview.md](../ops/runbooks/application-preview.md).

```
 Khushal's browser ──preview address──► Headroom proxy ──loopback──► Worker's dev server
                                        │                            (Ticket preview worktree)
                                        └── registry: Ticket, generation, observed process
```

## How it works

The Worker prepares a preview, which reserves the address and names the one worktree the app must run from. It starts the app on loopback under that base path, then registers the port. Headroom checks that the listening process really comes from the reserved worktree before it hands back the link. Khushal sees a link to the same name as Headroom, on another port.

A generation ends when the Worker stops it, when the process exits or the port is reused, when the Ticket is done or deleted, or when Headroom restarts. An old address shows an ended preview. A restarted app needs a new generation.

Previews need trusted Tailscale ingress and the preview address to be configured on the live host. Without both there are none. The main address answers no preview link. A preview runs on the named Home machine. That home can use Linux or macOS. Tickets on remote Mac executors cannot have previews. Nothing forwards to a remote executor.

## Native Mac evidence

The Mac inspector reads libproc evidence for listener ownership, exact process birth, boot identity, and the current worktree. Before the proxy sends bytes, it checks that the same process owns the accepted connection. A PID or command string alone grants no ownership.

Discovery requires one readable same-user owner. It skips peers whose permissions block inspection. The pinned process and its accepted connection must stay readable. Incomplete inventories and expired deadlines refuse the preview. Hidden holders remain outside that observation. Linux and Mac use the same registry, limits, and retirement rules.

The lifecycle checks active previews once per second and after record changes. Mac discovery scans bounded process and descriptor inventories only at registration. Later checks read the pinned process. No extra Worker loop exists.

## How a preview is kept apart from Headroom

A preview page is code a Worker wrote, so it runs on its own address. The port differs, so the browser treats the page as another origin. The page cannot read Headroom's answers or its storage. Its writes to the main address carry the preview origin, and Headroom refuses them. On the preview address Headroom answers only preview paths, so the page finds no Headroom API there either.

Cookies ignore ports, so the browser also sends Khushal's owner cookie to the preview address. The proxy forwards only the cookies the preview set itself, so the app never receives it.

## Why the content still has to be trusted

The separate address keeps a page off Headroom's origin. It is not a sandbox. The app runs as the same Unix user as Headroom, so it can read anything Headroom can. All previews share the one preview address, so a page can reach another Ticket's preview. The registration check proves where a process runs from. It does not prove who wrote it. Never put a vendor demo, a downloaded sample, or a page with third-party scripts behind a preview.

The tailnet is the gate: only tailnet members reach the origin. It is not local authentication. Any process on the host can reach a loopback port.

## The load it places on Headroom

Preview traffic runs inside Headroom's process. Headroom caps concurrent requests and WebSockets, globally and per preview, and limits transfer. Streams share the transfer limit in the order they ask, so a busy preview cannot starve one of its own requests. Excess gets a busy response at once, with no queue. These bounds protect ordinary Headroom work from the proxy. They do not limit the app's own CPU or memory.

## Decisions that shape it

- Own address, trusted content. Previews are a convenience for Khushal's own Workers, kept off Headroom's origin, not a sandbox.
- One fixed worktree per Ticket and a fresh generation per run, so an old link never reaches a new process.
- No rewriting. The app adapts to the base path, which keeps the proxy simple and honest.
- Bounded inside Headroom so a busy preview cannot starve the workspace.

## Code

- `src/planner/previews/`: registry, proxy, address separation (`address.py`), process checks, and the CLI. Limits are in `config.py` and `limits.py`.
- The preview address is the `preview_origin` setting. The operator step that adds its Tailscale Serve route is in [ops/panels-environments/README.md](../ops/panels-environments/README.md#preview-address).

_Last verified: 2026-11-15._
