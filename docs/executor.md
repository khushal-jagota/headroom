# Extra machines

Extra machines let Workers use files, apps, provider sign-ins, and permissions away from the home. The home keeps the authoritative Tickets, conversations, and work record. An extra machine does not become another Panels server.

Several extra machines can pull work from one home. Each Ticket names its destination. The home runs its own Workers directly. An extra machine receives only work for its destination. If that machine is away, its work waits. There is no fallback destination. The Chief and Managers stay at home.

Mac arm64 and Linux x86_64 executors use the same pull link and Worker supervisor. Enrollment screens remain outside this system.

The TypeScript Mac executor gives each new Worker its own launchd job. A saved job label identifies that launch through stop, crash cleanup, and executor recovery. A restart waits for the old job to disappear. After job removal, a best-effort sweep stops same-user tools that retain that job's environment marker. Tools can clear the marker, so the sweep logs this limit and never blocks restart. Historical records still use exact process birth evidence until their old launches retire. Linux retains its cgroup custody.

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

The owner registers an extra destination from Settings → Machines (Add a machine) and obtains one join command and its private credential, both shown once. The page takes the home's trusted HTTPS origin from the home's own configuration and refuses to issue a command when none is set. The `panels machine join-command` command does the same for a terminal that has owner authority. The command installs a new executor and checks an accepted lease. The command always names the trusted HTTPS origin of the same home that issues the credential. A credential permits executor requests for one destination. Replacing it invalidates the old credential. The home normally stores only a verifier. During the original Mac handover, it also keeps the pending credential until the handover closes.

The original Mac has a one-time transition from its existing authority. The first upgraded runtime requests its credential automatically and saves it privately before its work loops start. That runtime is an update trial, and the Mac supervisor keeps it only if it confirms in time. So the old authority stays open until the supervisor's decision to keep it is final: the supervisor's saved selection names this trial, and no attempt is pending. The runtime then sends a credentialed close request, and retries with backoff until the home accepts. That request closes the handover: the old authority closes permanently, and the pending secret is removed. That closure survives home restarts and migration replay.

Until the handover closes, the home accepts both the credential and the old authority. Every supervisor reverts only before its saved selection names the trial. So a trial that crashes, or whose confirmation misses the supervisor's window, falls back to a runtime that can still lease and report the revert. The fixed supervisor needed no change for this. Each start of an upgraded runtime asks for its credential before it uses its saved file. While the handover is open, the home returns its pending credential, or a new one after a home database restore. After closure, or when the home is unreachable, the Mac uses its saved file.

The home log records issuance and closure times with the executor incarnation.

Until the handover closes, the old trust proves the owner login and the claimed executor ID. It does not prove the physical device. Under the accepted transition exception, another device under that login can take the original Mac's one-time credential. The operator replacement command provides recovery. A lost reply or a crash before the private save permits another fetch of the same credential, including after a home restart. The home never silently issues another credential.

After the handover closes, every executor request needs the credential, including update requests. A previous runtime that lacks credential support cannot regain the old authority after rollback.

A home from before this rule closed the handover at the trial's credentialed "current" report, before the supervisor kept the trial. If that trial died or missed its window, the supervisor restored a runtime that the home refuses. A current home with a runtime from before the close request keeps the handover open until the Mac keeps a runtime that sends it. The Mac installation runbook gives the [manual recovery](../ops/mac-executor/installation.md#recover-a-mac-locked-out-after-its-credential-handover).

## Starts and presence

Each machine has its own automatic-start capacity and pacing. These limits cover preparation and delivery until positive acceptance, not the number of running turns. The home retains priority and space for supervisors. An away or occupied destination cannot consume another destination's start capacity.

Presence comes from leases, judged by the home clock. A silent machine becomes away. Its running Tickets pause in the home's view, while the machine retains output for later delivery. Work never moves to another destination because a lease expires.

A lease reads pending work and the current cleanup evidence. The slower workspace maintenance pass refreshes retained history. Startup and explicit Worker actions still refresh their cleanup evidence directly.

On the Mac, background and start-time cleanup refreshes run in order. A start checks its Ticket after any older refresh has finished, so an older scan cannot replace its newer evidence. Native child starts and mailbox polling remain independent of that reconciliation.

Each machine has one current process incarnation. A restart replaces only that machine's old incarnation. The home refuses stale process requests. The new process can replay its own machine's retained output under the existing assignments. It cannot replay another machine's output.

## Commands and output

Commands wait in the home's durable mailbox until their destination fetches them. A prompt stays pending and its conversation stays busy until the native agent accepts it. Mailbox placement alone does not establish delivery. The executor journals a command before it runs it. A duplicate returns its saved result instead of another execution. Journal tombstones prevent repetition after a home database restore. Commands for one conversation retain their order. Different conversations can progress independently.

When a backend's initialization times out on the Mac, the executor keeps the waiting prompt in order and makes a bounded attempt to start a fresh child. It first proves the failed child has stopped. If startup still fails, or cleanup is uncertain, it reports the failure instead of risking a second child. It never retries a prompt that may have reached an agent.

The outbox saves each durable event before delivery. Each conversation sends its numbered items in order. The home commits each item and its receipt together. A duplicate receives the stored receipt. The home checks each sender against its own machine, including message delivery and duplicate receipts.

During a home update trial, the home defers new result receipts. Extra machines retain output until the healthy home or restored previous home accepts it. A rollback never removes an accepted result from that boundary.

Live text and tool progress remain lossy. Finished messages and durable events enter the outbox before delivery. The Mac retries transient failures within a finite delivery window. At expiry, it retains the failed content and shows the failure in its local status and log. A lost reply leaves the result uncertain, even when the Mac stops its attempt. Later items can proceed, and conversations share delivery capacity fairly. Native command failures also reach the conversation core directly, so artifact delays cannot leave a failed turn active. A matching native refusal to steer into an absent turn ends the stale active turn without changing its historical prompt delivery.

Ticket files travel apart from items. An item never carries a file and never waits for one, so a message that links a new file can arrive a few seconds before the file does. After each item and at each start, the executor compares the Ticket's `artifacts/` folder with the versions the home confirmed. It sends each new or changed file once and removes each file the folder no longer has. A failed file retries without delay to any item or to the other files. The home accepts a file only for a Ticket that belongs to the sending machine, checks its bytes on a separate thread, and then puts it in the Ticket's served folder. A matching digest does not grant another machine access.

Each Worker uses its own local Ticket workspace. The executor transfers prompts, skills, and the Ticket's `artifacts/` folder. It does not transfer the private worktree, its dependencies, or any other file in the Ticket folder. The home grants workspace retirement only for the exact registered folder and its owning destination. See [Ticket workspaces](environments.md#ticket-scratch).

Agents can send a proposal back with `panels ticket proposal ID revise` and guidance
from `--message TEXT` or `--body-file PATH`. Use `--body-file -` for stdin. The executor
checks Home support before it saves the submission. An older Home returns an update
request without queued work. An older executor still refuses the command locally.

Home checks the sender's current assignment and ordinary Ticket authority, then uses
the same send-back resolver as the direct CLI. Guidance enters the Worker's prompt
through the existing delivery path. The decision and receipt commit together. A lost
reply can return the original receipt through `panels executor retry SUBMISSION_ID`.
Mac and Linux receive this support through their versioned runtime update channel,
which preserves their journals and outboxes.

## Restarts and recovery

A home restart preserves mailboxes, assignments, results, and receipts. The conversation system adopts accepted remote runs. Pending prompts remain busy until their existing native result arrives. Neither path sends the prompt again.

An executor restart preserves its journal and outbox. The home separates commands that never ran from uncertain deliveries. It waits for each affected conversation's retained output before recovery. Proven undelivered prompts can start afresh. Other interrupted turns receive the existing recovery prompt. A timeout alone never proves refusal or process termination.

Each executor uses the shared Worker supervisor. It records exact process ownership and retains cleanup fences when termination remains unknown. Cleanup for one machine does not target another machine's processes. Native platform limits remain separate from protocol correctness.

## Updates and health

A machine update closes its admission, confirms exact child retirement, and saves pending output before replacement. Its replacement retains journal and outbox custody until the home accepts replay.

The signed Mac launcher and the Linux systemd user service start a fixed supervisor that selects a versioned runtime. The data folder retains journals, outboxes, credentials, files, and logs across updates. Update offers and progress belong to the requesting destination. Each executor reports its platform with authenticated requests. Update offers select an archive for that platform and the executor role. Content-addressed archives can share storage without sharing update authority. Linux archive downloads start only for registered Linux machines. Their failures do not block home or Mac updates. The home remembers rejected archive bytes for its process lifetime. The home retains two Linux archives.


Releases ship the TypeScript Mac executor with its own Node runtime. The first installation on the live Mac remains a separate owner action. The [TypeScript installation runbook](../ops/mac-executor/typescript-installation.md) describes that handoff.

Each executor reads the same driver list as the home. Each driver supplies its launch, model list, home folder and skills folder for that machine. A start or model list request for a backend the machine has no driver for is refused with that reason. It never falls back to another backend.

The home and executor use protocol 26. A turn ending may say whether its child can take another prompt; a Mac that leaves this out means the child is reusable. The backend key on the wire can be any driver slug. Each lease lists the driver kinds its machine runs. A machine that lists none runs no backend. The home refuses a remote start for a backend its machine does not run before anything reaches the mailbox, and asks a returning machine for model lists only for the backends it runs. An incompatible executor receives refusal. Before the original credential handover, its existing authority still permits its recovery update. After handover, updates require its credential too. See the [Mac installation runbook](../ops/mac-executor/installation.md) or [Linux installation runbook](../ops/linux-executor/installation.md).

The stall checker watches outstanding waits. Each machine's presence clock controls waits that depend on that machine. A sleeping machine does not accrue another machine's awake time. Home-only waits use elapsed home time. The separate watchdog can alarm when the server's own checker stops.

Presence cannot distinguish a sleeping machine from a stuck process that sends no leases. Long model silence can resemble a stalled turn. Home restarts can lose some accumulated presence time. Physical sleep, power loss, permissions, and native process cleanup need checks on the actual Mac.

## Cost and storage

The home retains a bounded set of database connections across machines for frequent runtime requests. If all retained connections are in use, an operation opens and closes a temporary connection. It never waits for a pool slot. Each operation retains its own transaction and commit participants. Shutdown closes retained connections.

Credentials, presence, catalogs, and runtime owners grow with machine count. Journals, command tombstones, incarnation records, and receipts grow with work history because duplicate protection requires them.

Leases carry one machine's bounded state and update offer. Command polls carry a bounded page of that machine's mailbox. Idle polls do not acquire the database write lock. Polls do not transfer the machine inventory or completed command history. Admission follows pending reservations, and health checks follow machines and outstanding waits.

## Code

- `server/executor/`: shared protocol, registry, transport, journals, mailboxes, files, and replay.
- `server/executor/mac/`: the TypeScript Mac executor and its packaging.
- `server/machines/`: destinations and credentials.
- `server/conversation/start_admission/`: automatic-start capacity and pacing.
- `server/conversation/deploy_interruptions/`: retained adoption and interrupted-turn recovery.
- `server/stalls/`: wait checks and watchdog.
- `server/environments/linux_executor/`: Linux executor archive, installation, systemd user service, and private enrollment entry.
- `server/environments/updates/mac/`: shared destination-specific executor offers and archive cache.
- `src/planner/executor/`, `src/planner/environments/mac_executor.py`, and `environments/mac_app/`: the Python Mac executor, its installation, and its signed launcher. They stay as the Mac's rollback until the owner approves their removal.

_Last verified: 2026-11-14._
