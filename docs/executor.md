# Extra machines

Extra machines let Workers use files, apps, provider sign-ins, and permissions away from the home. The home keeps the authoritative Tickets, conversations, and work record. An extra machine does not become another Panels server.

Several extra machines can pull work from one home. Each Ticket names its destination. The home runs its own Workers directly. An extra machine receives only work for its destination. If that machine is away, its work waits. There is no fallback destination. The Chief and Managers stay at home.

The current extra-machine runtime is the Mac executor. Additional platforms and enrollment screens remain outside this system.

## The pieces

Each extra machine has its own credential, presence, command mailbox, and current process identity. Its journal and durable outbox remain on that machine. The home runs one executor owner for each registered destination through a shared registry.

```
 Extra machine A                           Home
  credential, lease ────────────────────▶  A presence and current process
  command poll ◀─────────────────────────  A command mailbox
  journal, results, outbox ──────────────▶  A assignments and receipts

 Extra machine B
  credential, lease ────────────────────▶  B presence and current process
  command poll ◀─────────────────────────  B command mailbox
  journal, results, outbox ──────────────▶  B assignments and receipts
                                            Tickets and conversation record
```

The extra machine opens every request. A lost connection does not lose ownership or decide whether work ran. Catalogs, update progress, files, and workspace access follow the authenticated destination too. Display names, icons, and colors do not grant authority.

Workers retain their existing permissions to read shared Ticket records for collaboration. All machines belong to one owner. Machine isolation covers transport: commands, child authority, receipts, files, and workspaces. It does not change ordinary agent permissions for those shared records.

Settings → Machines shows availability, current Worker activity, and Tickets that wait while a destination is away. Unknown activity stays unknown. The old `vps` and `macos` names remain aliases for the original home and Mac destinations.

## Credentials and the original Mac

The owner registers an extra destination and gives it its credential through the operator commands. A credential permits executor requests for one destination. Replacing it invalidates the old credential. The home normally stores only a verifier. During the original Mac handover, it also keeps the pending credential until the first authenticated request.

The original Mac has a one-time transition from its existing authority. The first upgraded runtime requests its credential automatically and saves it privately before its work loops start. Until the first request authenticates with that credential, the original Mac can fetch the same pending credential again. Its old authority permits that fetch. The first authenticated request permanently closes the old authority and removes the pending secret. That closure survives home restarts and migration replay.

The home log records issuance and closure times with the executor incarnation.

Until that first authenticated request, the old trust proves the owner login and the claimed executor ID. It does not prove the physical device. Under the accepted transition exception, another device under that login can take the original Mac's one-time credential. The operator replacement command provides recovery. A lost reply or a crash before the private save permits another fetch of the same credential, including after a home restart. The home never silently issues another credential.

After the first authenticated request, every executor request needs the credential, including update requests. A previous runtime that lacks credential support cannot regain the old authority after rollback.

## Starts and presence

Each machine has its own automatic-start capacity and pacing. These limits cover preparation and delivery until positive acceptance, not the number of running turns. The home retains priority and space for supervisors. An away or occupied destination cannot consume another destination's start capacity.

Presence comes from leases, judged by the home clock. A silent machine becomes away. Its running Tickets pause in the home's view, while the machine retains output for later delivery. Work never moves to another destination because a lease expires.

Each machine has one current process incarnation. A restart replaces only that machine's old incarnation. The home refuses stale process requests. The new process can replay its own machine's retained output under the existing assignments. It cannot replay another machine's output.

## Commands and output

Commands wait in the home's durable mailbox until their destination fetches them. The executor journals a command before it runs it. A duplicate returns its saved result instead of another execution. Journal tombstones prevent repetition after a home database restore. Commands for one conversation retain their order. Different conversations can progress independently.

The outbox saves each durable event before delivery. Each conversation sends its numbered items in order. The home commits each item and its receipt together. A duplicate receives the stored receipt. Machine ownership is checked before receipt access, including duplicates.

Live text and tool progress remain lossy. Finished messages and durable events wait in the outbox through an outage. A slow or failed conversation does not block the others.

Files reach the home before an item that refers to them. Uploads wait privately until the home accepts their item. The home checks file ownership before publication and publishes in small groups so other writers can proceed. A matching digest does not grant another machine access.

Each Worker uses its own local Ticket workspace. The executor transfers prompts, skills, and Ticket artifacts. It does not transfer the private worktree or its dependencies. The home grants workspace retirement only for the exact registered folder and its owning destination. See [Ticket workspaces](environments.md#ticket-scratch).

## Restarts and recovery

A home restart preserves mailboxes, assignments, results, and receipts. The conversation system adopts retained remote runs instead of starting another provider call.

An executor restart preserves its journal and outbox. The home separates commands that never ran from uncertain deliveries. It waits for each affected conversation's retained output before recovery. Proven undelivered prompts can start afresh. Other interrupted turns receive the existing recovery prompt. A timeout alone never proves refusal or process termination.

Each executor uses the shared Worker supervisor. It records exact process ownership and retains cleanup fences when termination remains unknown. Cleanup for one machine does not target another machine's processes. Native platform limits remain separate from protocol correctness.

## Updates and health

The signed Mac launcher and fixed supervisor select a versioned runtime. The data folder retains journals, outboxes, credentials, files, and logs across updates. Update offers and progress belong to the requesting destination. Content-addressed archives can share storage without sharing update authority.

The home and executor use protocol 26. An incompatible executor receives refusal. Before the original credential handover, its existing authority still permits its recovery update. After handover, updates require its credential too. See the [Mac installation runbook](../ops/mac-executor/installation.md).

The stall checker watches outstanding waits. Each machine's presence clock controls waits that depend on that machine. A sleeping machine does not accrue another machine's awake time. Home-only waits use elapsed home time. The separate watchdog can alarm when the server's own checker stops.

Presence cannot distinguish a sleeping machine from a stuck process that sends no leases. Long model silence can resemble a stalled turn. Home restarts can lose some accumulated presence time. Physical sleep, power loss, permissions, and native process cleanup need checks on the actual Mac.

## Cost and storage

The home shares a bounded set of database connections across machines for frequent runtime requests. Each operation retains its own transaction and commit participants. Shutdown closes these connections. These operations reuse the schema that each connection prepares.

Credentials, presence, catalogs, and runtime owners grow with machine count. Journals, command tombstones, incarnation records, and receipts grow with work history because duplicate protection requires them.

Leases carry one machine's bounded state and update offer. Command polls carry a bounded page of that machine's mailbox. Idle polls do not acquire the database write lock. Polls do not transfer the machine inventory or completed command history. Admission follows pending reservations, and health checks follow machines and outstanding waits.

## Code

- `src/planner/executor/`: shared protocol, registry, transport, journals, mailboxes, files, and replay.
- `src/planner/machines/`: destinations and credentials.
- `src/planner/conversation/start_admission/`: automatic-start capacity and pacing.
- `src/planner/conversation/deploy_interruptions/`: retained adoption and interrupted-turn recovery.
- `src/planner/stalls/`: wait checks and watchdog.
- `src/planner/environments/mac_executor.py` and `environments/mac_app/`: installation and signed launcher.

_Last verified: 2026-11-13._
