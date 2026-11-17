# Backend cards and explicit provider limits

The ten backend routes depend on `BackendHttpRuntime` in `contracts.ts`. Routes never
import provider adapters. They list and validate backends from `backend_ids`, in
driver order, and find a backend's sign-in through `sign_in(id)`; a backend without
one, or without the member a route calls, answers 404. Card reads cache local catalog
facts. Only an explicit usage refresh acquires provider limits. Successful
observations replace cached limits in one short transaction. Failed reads retain the
last successful observation.

`composition.ts` builds the runtime from the driver registry. Each live instance
supplies its card status, its optional sign-in and its optional allowance usage. The
runtime owns the one probe environment every card probe shares, and its shutdown
disposes the registry, which stops each instance's catalog owner, sign-in child and
probes together. Without a registry, the cards come from the built-in manifests and a
recording environment with no executables.

`service.ts` keeps cards, per-backend locks and the complete-list rule. A status may
name a freshness key, which Codex uses for its saved login, so a kept card goes stale
when what it read changes. In machine mode, a status that detects upgrades by version
is re-read when `--version` changes. `probe.ts` holds the one card probe that each
driver's `runtime/status.ts` calls with its manifest recipe, identity reader and
catalog reader. The probe also records the CLI version verdict that later launches
reuse.

Each HTTP operation supplies its cancellation signal to probes and update commands.
`commands.ts` owns each detached command group. Abort, timeout, and shutdown kill
that group and wait for its direct child's exit. Completion does not wait for a
descendant to close inherited pipes. Server shutdown stops commands before record
closure. HTTP transports use `redirect: 'manual'`, so a redirect cannot resend a
usage credential.

Sign-in session timestamps accept `Date` or a string in Python's
`datetime.isoformat()` form. A source with microseconds must supply the string, and
the response preserves it exactly. A `Date` retains its millisecond precision.

`machine_catalog` supplies the `RemoteCatalogSource` for Tickets on another machine;
with an executor the production bindings return its live or last offline catalog.

Test mode composes no production provider: instances read no catalog, login or
account, and production process and HTTP doors refuse test contexts before any
external action. The comparison host in `server/test_host.ts` uses a fixed
`TicketModelSnapshots` source (`testing/ticket_models.ts`). Direct checks compare full
cards, the HTTP routes, source rows, recording calls and provider limit decisions
against Python. They do not prove a real provider account, child lifetime or network
access.
