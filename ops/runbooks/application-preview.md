# Share a running application through a preview

For a Worker that needs Khushal to open its application on the assigned machine. The design,
the trust boundary, and the limits are in [docs/previews.md](../../docs/previews.md).
Only put code you trust behind a preview: it runs in Headroom's own browser origin.

The commands use the Worker's own Ticket identity. They cannot aim at another Ticket
or invent a hostname. Machine code checks the application locally and relays it through the server.

## Steps

1. Use the Ticket worktree that Panels supplies on the assigned machine.
   For work that the server runs directly, the existing path remains
   `<project.folder_path>/worktrees/<ticket-id>-preview` inside the project folder.
2. Run `panels preview prepare`. It reserves the address and returns `base_path`,
   `generation`, `public_origin`, and `worktree`.
3. Start the application from that worktree, on `127.0.0.1`, on a free unprivileged
   port, serving that exact `base_path` including the trailing slash.
4. Run `panels preview register --port <port>`. It checks the listener and returns the
   `url` to share. Share that URL, not the bare prefix.
5. When finished, run `panels preview stop` to revoke the address, then stop the
   application process yourself.

`panels preview show` prints the current generation and link.

## Making the app work under the prefix

The proxy does not rewrite HTML, CSS, or JavaScript and does not strip the base path.
Assets, API calls, redirects, cookies, and hot reload all use the generation prefix.

- For Vite, set `base` to the returned path, bind `127.0.0.1` with a strict port, and
  point hot reload at the public hostname over `wss` on port 443, with the socket path
  under `base`.
- Relative redirects stay inside the generation. Cookies set an explicit Path inside it
  and no Domain.
- Root-only apps, root auth callbacks, `__Host-` cookies, public hot-reload ports, and
  applications outside the prepared worktree are unsupported. Adapt the app before sharing the link.

## Restarts

Do not reuse a generation for a restarted process. Prepare and register a new one.
A server restart, machine restart, or lost relay ends the generation. Prepare and register again after it.
