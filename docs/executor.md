# The Mac executor

The executor lets a Ticket's Worker use the Mac's files, apps, provider sign-ins, and macOS permissions. The VPS keeps the Ticket, conversation, and every Panels record. The Mac does not become a second Panels server.

## What it is made of

- **The lease.** The Mac reports itself to the VPS every 10 seconds. The VPS reads presence and health from it.
- **The command mailbox.** A table on the VPS. Every command for the Mac waits there until the Mac fetches it.
- **The command journal.** A file on the Mac. The Mac records each command there before it runs it.
- **The outbox.** A file on the Mac. It keeps agent output and its files until the VPS confirms receipt.
- **The installation.** It keeps the Mac service alive. See [What runs on the Mac](#what-runs-on-the-mac).

The Mac opens every request. Each is a plain HTTPS request. No work depends on a connection staying up.

```
 Mac executor                                   VPS server
  lease, every 10 s  ────────────────────────▶  presence, health, update target
  long poll for commands ◀───────────────────   command mailbox (database)
  command journal ── results ────────────────▶  first result wins
  outbox ── items, per conversation ─────────▶  apply now, one commit, answer
  reads, live text, files ◀──────────────────▶  each its own request
                                                stall checker ──▶ watchdog (own unit) ──▶ alarm
```

## Which work goes to the Mac

Each Ticket names a stable machine destination (see [Tickets](tickets.md)). The home destination keeps direct execution. Only the original Mac destination uses the configured executor ID. Without that executor, its Tickets wait. They never run on the home. The Chief and Managers stay on the home. Legacy `vps` and `macos` values remain aliases for these original two destinations. The executor wire protocol stays at 23.

Settings → Machines shows each destination’s availability, current Worker count, and Tickets that wait while it is away. Counts use existing conversation signals and Ticket ownership. An away machine has unknown activity. Display names, four icons, and eight colors do not enter routing or authority checks.

A Mac Ticket starts only while the Mac is present. Until then its step shows "Waiting for the Mac". A message or answer to an existing Mac conversation is stored, and the Mac receives it when it returns.

All three backends can run on the Mac. Each Ticket uses its own workspace inside the executor's Ticket files folder ([Conversations](conversations.md#the-workspace-for-an-agent)). The Mac fetches the prompt's files and skills from the VPS, and copies changed Ticket artifacts back. It never transfers the private worktree or its dependencies. Panels creates that worktree from the local project repository. Workers handle dependency setup, pushes, and pull requests.

A Worker's `panels` writes enter the outbox, and the VPS applies them with the authority of the original run.

The VPS supplies authoritative Ticket lifecycle state with a successful lease. The Mac uses that state for the [seven-day file clock](environments.md#ticket-scratch). Before removal, it requests fresh permission for the exact expired workspace on that Mac. Registration binds the native conversation to its actual local folder. A server mirror has a separate identity and cannot retire the Mac checkout.

Active native turns preserve their files. Idle sessions stop through the ordinary executor path before their workspace expires. Offline or incomplete local state never establishes that a Ticket ended. The clock covers Ticket folders. External legacy evidence remains in place for owner review. Shared project clones and extracted executor updates are outside it.

## Presence

The lease holds the Mac process's identity, the outbox backlog, and which turns are working. The VPS judges every time limit by its own clock.

| Presence | Meaning |
| --- | --- |
| Present | A lease arrived within the last minute. |
| Not heard from | No recent lease. The page shows "Not heard from since 14:02". |
| Asleep | A lease said "going to sleep". The Mac does not send this yet. |
| Refused | A version refusal, such as "Mac needs update". |

While the Mac is away, nothing is decided. Commands wait. Running Mac Tickets show "Paused: the Mac is away". When the Mac returns, it sends its backlog.

## Commands

Start, prompt, steer, cancel, stop, answers, and model-list requests are rows in the mailbox. The Mac long-polls for them, and records each one in its journal before it runs. Commands for one agent run in order. Different agents run side by side.

The journal keys each command by its ID. A recorded command never runs again; its saved result is reported again instead. Finished rows stay as tombstones, so a command restored from a VPS database backup is never run twice.

Each result goes back on its own request, and the first result recorded wins. Effects on a conversation, such as a stop proof, travel in the outbox with the agent's events. Sending never waits for the Mac, except a steer. A stop never expires; it counts only after its proof arrives. The VPS can withdraw a command the Mac has not fetched, which proves it never ran. A held poll hands out commands only while the Mac was heard from recently, so a silent Mac is not handed work it will never read. A steer that later proves it never reached its turn goes to the queue as the next message.

## Getting work back to the VPS

The Mac saves each piece of output before it sends it. Each conversation sends its numbered items in batches, by itself and in order. The VPS applies each item with its receipt in one commit. A resend after a lost reply gets the stored receipt, so nothing is applied twice.

The VPS answers each batch quickly, with receipts or "retry later". A dropped request never cancels a save. If one conversation's items fail, only that conversation waits. One conversation never blocks another. An item from a replaced Mac process is refused, and the new process replays it.

The outbox sends a bounded number of conversations at once and rotates after each batch. A conversation in retry backoff leaves capacity for others. A file reaches the VPS before any item that refers to it, so a Ticket link never points at a missing file. Live text, thinking, and tool progress skip the outbox and are lost while the Mac is away. Finished messages and recorded events are saved and resent.

## Restarts

Nothing is guessed. An uncertain outcome stays uncertain, and no provider call is repeated.

- **A VPS restart** keeps Mac work running. The VPS rebuilds its view from the database and the lease, and adopts each open Mac run live. Waiting commands keep waiting.
- **A Mac process restart** shows as a new process identity in the lease. The VPS withdraws the old process's waiting commands. The new process's journal shows which fetched commands never ran; those are withdrawn, and the rest become uncertain. Then, for each conversation separately, the VPS waits until the Mac has sent everything it held for it. If the turn's prompt never ran, the same prompt goes to a fresh agent, and a VPS crash in between does not lose it. Otherwise the turn ends interrupted, and the conversation gets one recovery prompt, which resumes the saved session ([Conversations](conversations.md#lifecycle)) and repeats any answer the old agent did not take. A message waiting behind that turn waits until then too, even if the replay ends the turn normally, and then goes to the fresh agent.

## Health and alarms

Every wait has an owner, a start time, and a limit. The stall checker runs inside the server. It records each overdue wait as a finding that names the Ticket and conversation, and it writes a heartbeat. It watches items, commands, stops, drains, silent turns, failing leases, and retrying saves.

The watchdog is its own unit. A timer runs it outside the server, so a stuck server can still raise an alarm. It alarms once on each new finding, and on a stale heartbeat. A finding stays open until its wait ends, so a sleep or a restart does not alarm on it again. It pushes to Khushal, unless the "Stalled work" [notification](notifications.md) is off, and it messages the Chief, within 5 minutes. The watchdog alarms only after Khushal installs its timer ([Deployment](deployment.md#the-switch-to-mailboxes)).

A sleeping Mac raises no alarm. Stalls that depend on the Mac count only the time it is present, added up across the times it is awake, so a lossy link or a Mac that is awake only now and then still alarms. A stretch shorter than a minute, such as a dark wake, does not count.

Known gaps: a stuck executor that sends no leases looks the same as sleep. A model that thinks for a long time without live text can raise a false silent-turn alarm. A VPS restart forgets some present time already counted, and waits kept only in memory start again. A dark wake longer than a minute counts as present time. A long install raises one "stall checker has not run" alarm.

## What runs on the Mac

One installation lives in the owner's normal account:

- **The Panels app** is a signed launcher, so macOS associates folder consent with Panels.
- **The supervisor** selects a versioned runtime and keeps the previous one for recovery.
- **A LaunchAgent** starts at login and restarts the service after a crash.
- **The data folder** holds the outbox, the command journal, Ticket files, skills, the workspace, and logs. Updates never replace it.

The executor uses the same Worker supervisor as the home. It records exact process ownership, lowers priority, and reconciles old records before new work. Unknown cleanup retains the affected Ticket fence. Unrelated Tickets can continue. The lease reports the reason and any older record that cannot identify a Ticket. An unobserved detached tool can escape, so cleanup never promises that every descendant stopped. Native Mac checks establish its platform limits. On macOS, signals to recorded survivors follow an immediate PID and microsecond birth check. A small check-to-signal race remains. Unknown cleanup retains its fence.

**Updates.** One press of Update covers both machines ([Deployment](deployment.md#the-card-and-the-mac)). If the new runtime does not lease in time, the supervisor reverts. The VPS accepts one protocol version; an older Mac is refused with "Mac needs update".

Claude and Codex use the Mac's external CLI installations. A runtime update preserves that selection. It does not replace their versions with the copies inside the archive. An explicit external Claude executable remains an operator choice.

The steps are in the [Mac executor installation runbook](../ops/mac-executor/installation.md).

## Decisions that shape it

- The VPS stays the only record. The Mac holds work in transit, never canonical state.
- The Mac pulls its work. The VPS needs no route to the Mac, and the Mac needs no inbound port.
- Each message is judged by its own IDs: the command ID, the item number, and the Mac process. No connection plays a part.
- Uncertainty is kept. A timeout or lost reply is not a refusal, and a missing process is not a stop.
- The outbox format does not change, so the supervisor can always revert to the previous runtime.
- The fallback is the previous release, not a second path in code.
- The Mac and VPS halves meet only through the shared protocol.

## What is proven, and what is not

A harness on the VPS runs a real Mac half and a real VPS half through 100 sleep cycles: dropped links, silent links, dark wakes, long sleeps, and restarts of either side. It checks that no event is lost or applied twice, that every message and answer reaches its agent once, and that a sleeping Mac raises no alarm. It excuses, by name, only messages held in VPS memory at a VPS restart. A three-cycle version runs in CI.

Not yet proven:

- **The sleep and wake rules.** They are not built. The Mac does not report "going to sleep" and has no wake hook.
- **The real Mac sleep test.** A script records what a Claude Code turn and a Codex turn do across real sleeps. It waits for a time agreed with Khushal.
- On the real Mac: the start-up cleanup, priority under build load, Tailscale Serve holding a long poll, running before login, permission grants across updates, and the disk keeping its flushes through power loss.

## Code

- `src/planner/executor/`: both halves. `vps_*` is the VPS half, `mac_*` the Mac half, and `protocol.py`, `contracts.py`, and `logic/` are shared. `routing.py` sends Mac Tickets to the executor.
- `src/planner/conversation/deploy_interruptions/`: adoption after a VPS restart, and recovery after a Mac restart.
- `src/planner/stalls/` and `ops/panels-environments/panels-stall-watchdog.*`: the stall checker, watchdog, and its units.
- `src/planner/core/migrations/versions/executor_mailboxes.py` adds the mailbox tables; `retire_executor_websocket_tables.py` drops the old connection's tables.
- `src/planner/environments/mac_executor.py` and `environments/mac_app/`: the LaunchAgent and signed app.
- `scripts/executor_sleep_cycles.py`: the 100-cycle harness. `scripts/mac_sleep_test.py`: the real Mac sleep test.

_Last verified: 2026-11-12._
