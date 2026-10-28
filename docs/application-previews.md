# Running application previews

A Worker can give Khushal one link to an application running on the Panels host:

`https://panels-vps.tail687c57.ts.net/dev/tickets/<ticket-id>/<generation>/`

This feature requires installation of the release that contains it. A code change or
Ticket approval does not install it, publish a route, or restart production.

## Trusted content only

Never serve untrusted or third-party content through an application preview. This
prohibition includes vendor demos, downloaded sample applications, and pages that
embed third-party scripts. Preview JavaScript shares Panels' browser origin and can
act as Khushal inside Panels, including accessing its APIs and browser storage.
Cookie paths and filtered headers do not isolate hostile JavaScript.

The application already runs as the same machine user as Panels. Process checks
prove an observed socket and cwd association with the reserved Ticket worktree;
they do not prove authorship or contain malicious same-user programs.

## Prepare, start, register

The CLI uses the Worker's existing local Ticket identity; commands cannot select
another Ticket or supply a destination hostname or worktree.

1. Create a Git worktree at exactly `<project.folder_path>/worktrees/<ticket-id>-preview`.
   It must resolve inside the configured project folder. Symlink escapes are refused.
2. Run `panels preview prepare --json`. Keep its `base_path`, `generation`, `public_origin`,
   and `worktree` values. Preparation reserves the address before startup.
3. Start the application **from that worktree** on an available nonprivileged port,
   listening only on `127.0.0.1`. Configure the entire application to serve the exact
   `base_path`, including its trailing slash. Use strict port selection.
4. Run `panels preview register --port PORT --json`. Registration verifies the actual
   listener and returns the usable `url`. Share that URL with Khushal.
5. Use `panels preview show` to inspect it. Run `panels preview stop` when finished,
   then stop the application process itself.

The proxy preserves the full path and query. It does not rewrite HTML, CSS, or
JavaScript and does not strip the base path. API routes, router navigation, assets,
redirects, cookies, and hot reload must all use the generation prefix. A bare path
prefix is not a running application and must never be presented as one.

For Vite 6, set `base` to the returned `base_path`, `server.host` to `127.0.0.1`,
`server.port` to the chosen port, and `server.strictPort` to `true`. Set HMR `host`
to the public hostname, `protocol` to `wss`, and `clientPort` to `443`; keep the
socket path under `base`. Prefix each API proxy key and router base. Configure
backend redirects and cookies for the same prefix. Verify the browser's actual
HMR URL and reject a direct localhost fallback. Do not copy a generation between
Tickets or reuse it for a restarted application.

Relative and same-origin redirects must remain inside the generation. Cookies
must have an explicit Path inside it and no Domain. Root-only applications, root
auth callbacks, `__Host-` cookies, public HMR ports, shared or clustered listeners,
and non-Linux hosts are unsupported. Adapt the application before sharing a link.

## Network gate and lifetime

Tailscale Serve enforces tailnet membership before traffic reaches the published
HTTPS origin. Panels keeps its loopback listener; no preview creates a public
listener or new Tailscale route. Every preview HTTP request and WebSocket upgrade
also requires the configured Tailscale provider, exact allowed login header,
canonical Host, and an allowed Origin when supplied. Missing or duplicate identity
headers fail closed. No ingress configuration means no application previews.

These checks are a **tailnet gate, not local authentication**. A same-user process
can reach the loopback Panels port and forge its headers. The published origin
must remain tailnet-only; changing that network boundary requires separate work.

Registration requires exactly one observed readable same-user process holding the
listener, with its real working directory inside the Ticket's reserved Git
worktree. Linux can hide non-dumpable processes even from the same user. Discovery
skips those unreadable processes, so it cannot exclude an additional hidden socket
holder. After registration, every connection still requires the pinned process's
boot identity, PID, start time, listener inode, and accepted reverse TCP tuple to
remain readable and match. This proves an observed socket/process/worktree
association; it does not prove authorship or isolation from same-user processes.

A generation ends on explicit stop, process exit or port reuse, Ticket completion
or deletion, or a Panels restart. Old addresses display an ended-preview response.
An application restart requires preparation and registration of a fresh generation.
Canonical Ticket changes trigger a recheck; a one-second timer covers lost signals.
Active traffic closes within two seconds of expiry. Stopping a preview revokes its
address and connections; the Worker still owns stopping its application process.

## Effect on Panels

Preview I/O shares Panels' process and event loop. Capacity is capped at 32 HTTP
requests globally and 16 per preview, plus eight WebSockets globally and two per
preview. Excess traffic receives a readable busy response without an admission
queue. Transfer is limited to 8 MiB per second with a 2 MiB burst across both
directions. Bodies are streamed, limited to 8 MiB, and use chunks of at most
64 KiB; headers are limited to 32 KiB and socket messages to 1 MiB.

Panels' server protocol caps inbound WebSocket messages at 1 MiB and its receive
queue at four messages before handing them to the application. These protocol
limits apply to all WebSockets served by Panels, including previews. Both sides
use a 20-second ping interval and a 10-second pong deadline. Preview transport
adds no message queue between receiving and forwarding; each direction awaits
its next send before reading another message.

Process inspection runs outside the event loop on two dedicated threads, with
bounded discovery and a one-second deadline. One thread is reserved for canonical
Ticket lifetime checks, so busy process inspections cannot queue ahead of expiry.
A connection must prove its actual accepted peer belongs to the pinned process before application bytes leave Panels.
HTTP pre-header work ends within five seconds; idle, total request, blocked send,
and socket message limits bound stalled traffic. Configuration lives in
`src/planner/previews/config.py`.

These bounds protect ordinary Panels work from proxy-induced load. They cannot
prevent a separate application process from exhausting the host's CPU or memory.
