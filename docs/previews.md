# Previews

A preview is one link Khushal can open to an application a Worker is running on the Headroom host. The link looks like:

`https://panels-vps.tail687c57.ts.net/dev/tickets/<ticket-id>/<generation>/`

The dev server stays on loopback. Headroom proxies that generation through the existing tailnet HTTPS origin. A code change or a Ticket approval does not publish the route. The release that contains previews has to be installed, and Tailscale ingress has to be configured. With no ingress configuration, there are no previews.

## Why the content has to be trusted

Preview JavaScript runs in Headroom’s browser origin. It can call Headroom’s APIs and read its storage. Cookie paths do not contain hostile script. Never put a vendor demo, a downloaded sample, or a page that embeds third-party scripts behind a preview. The application already runs as the same Unix user as Headroom. The registration checks prove that an observed process is listening from the reserved Ticket worktree. They do not prove who wrote the program, and they do not contain a malicious program running as that user.

The tailnet gate is membership in the tailnet before traffic reaches the published origin. It is not local authentication. A process on the host can reach the loopback port and forge headers. The published origin stays tailnet-only.

## How a Worker starts one

The commands use the Worker’s own Ticket identity. They cannot aim at another Ticket or invent a hostname.

1. The git worktree is exactly `<project.folder_path>/worktrees/<ticket-id>-preview`, inside the project folder. A symlink that escapes is refused.
2. `panels preview prepare` reserves the address and returns `base_path`, `generation`, `public_origin`, and `worktree`.
3. The application starts from that worktree, on `127.0.0.1`, on a free unprivileged port, serving that exact `base_path` including the trailing slash.
4. `panels preview register --port` checks the listener and returns the `url` to share.
5. `panels preview stop` revokes the address. The Worker still stops the application process.

The proxy does not rewrite HTML, CSS, or JavaScript and does not strip the base path. Assets, API calls, redirects, cookies, and hot reload all use the generation prefix. A bare prefix is not a running app and must not be shared as one. For Vite, set `base` to the returned path, bind `127.0.0.1` with a strict port, and point hot reload at the public hostname over `wss` on port 443, with the socket path under `base`. Do not reuse a generation for a restarted process. Prepare and register a new one.

Relative redirects stay inside the generation. Cookies set an explicit Path inside it and no Domain. Root-only apps, root auth callbacks, `__Host-` cookies, public hot-reload ports, and non-Linux hosts are unsupported. Adapt the app before sharing the link.

## Lifetime and the load it places on Headroom

A generation ends on stop, on process exit or port reuse, when the Ticket is Done or deleted, or when Headroom restarts. Old addresses show an ended preview. Restarting Headroom needs a new prepare and register. Active connections close within about two seconds of expiry.

Preview traffic shares Headroom’s process. Caps sit at 32 HTTP requests globally and 16 per preview, and at eight WebSockets globally and two per preview. Excess traffic gets a busy response. There is no admission queue. Transfer is limited, and bodies stream with a size cap. Those bounds protect ordinary Headroom work from the proxy. They do not stop the application process from using the host’s CPU or memory.

## Where it shows up

The Worker shares the returned URL. Khushal opens it in the browser he already uses for Headroom. The Ticket’s file preview remains the path for a document or a saved HTML artifact. A preview is for a running dev server.

## Pointers

`src/planner/previews/`. Limits live in `src/planner/previews/config.py` and `limits.py`.

_Last verified: 2026-10-30._
