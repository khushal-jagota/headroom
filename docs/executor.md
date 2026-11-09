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
  reads, live text, files ◀──────────────────▶  each its own request and limit
                                                stall checker ──▶ watchdog (own unit) ──▶ alarm
```

## Which work goes to the Mac

Each Ticket has a locale, `vps` or `macos` (see [Tickets](tickets.md)). A `macos` Worker step gets an agent process on the Mac. VPS Tickets, the Chief, and Managers stay on the VPS. The VPS configuration names one executor ID. Without it, the VPS runs everything locally.

A Mac Ticket starts only while the Mac is present. Until then its step shows "Waiting for the Mac". No claim is taken and nothing errors. A message or answer to an existing Mac conversation is stored, and the Mac receives it when it returns.

All three backends can run on the Mac. A Mac Worker runs in a Panels workspace inside the executor's data folder ([Conversations](conversations.md#the-workspace-for-an-agent)). Before a start, the Mac fetches the prompt's files and installs the current skills from the VPS database. The Mac copies changed Ticket artifacts to the Ticket's VPS folder. A Worker's `panels` reads go to the VPS directly. Its writes enter the outbox, and the VPS applies them with the authority of the original run.

## Presence

The lease holds the Mac process's identity, the outbox backlog, and which turns are working. The VPS judges every time limit by its own clock. The Mac's clock is for display only.

| Presence | Meaning |
| --- | --- |
| Present | A lease arrived within the last 60 seconds. |
| Not heard from | No lease for 60 seconds. The page shows "Not heard from since 14:02". |
| Asleep | A lease said "going to sleep". The page shows "Asleep since 14:02". The Mac does not send this yet. |
| Never | No compatible Mac has leased yet. |
| Refused | A version refusal: "Mac needs update" or "Mac version is incompatible", with both versions. |

While the Mac is away, nothing is decided. Commands wait. Running Mac Tickets show "Paused: the Mac is away". When the Mac returns, the model lists refresh and the Mac sends its backlog.

## Commands

Start, prompt, steer, cancel, stop, answers, and model-list requests are rows in the mailbox. The Mac long-polls for them, and records each reply in its journal before any command runs. Commands for one agent run in order. Different agents run side by side.

The journal keys each command by its ID. A recorded command never runs again; its saved result is reported again instead. Finished rows become tombstones and are never deleted. So a command restored from a VPS database backup is never run twice.

Each result goes back on its own request, and the first result recorded wins. Effects on a conversation, such as an applied answer or a stop proof, travel in the outbox with the agent's events.

Sending never waits for the Mac, except a steer. Each kind has its own expiry rule:

| Command | Expiry |
| --- | --- |
| Start, prompt, cancel | Only when the Mac process is replaced. |
| Steer | At fetch, if its turn has ended or 10 seconds have passed. It then becomes the next message. While the Mac is away, it becomes the next message at once. |
| Answer | At fetch, if its question is no longer open. |
| Stop | Never. A stop counts only after its proof arrives, or after a replaced process's conversation is drained. |
| Model list | After 35 seconds. While the Mac is away, the last saved list is shown. |

The VPS can withdraw a waiting command with no round trip, which proves it never ran. A fetched command is never withdrawn. A prompt the Mac could not deliver ends its turn failed, and the reason says whether it may have reached the agent.

## Getting work back to the VPS

The Mac saves each piece of output before it sends it. Each conversation sends its numbered items in batches, by itself and in order. The VPS applies each item with its receipt in one commit. A resend after a lost reply gets the stored receipt, so nothing is applied twice.

The VPS answers each batch within 20 seconds, with the receipts it stored or "retry later". A dropped request never cancels a save. If one conversation's items fail, only that conversation waits. One conversation never blocks another. An item from a replaced Mac process is refused, and the new process replays it.

A file reaches the VPS before any item that refers to it is accepted, so a Ticket link never points at a missing file.

Agent reads, live text, and file transfers are plain requests with their own limits. Live text, thinking, and tool progress skip the outbox and are lost while the Mac is away. Finished messages and recorded events are saved and resent.

## Restarts

Nothing is guessed. An uncertain outcome stays uncertain, and no provider call is repeated.

- **A VPS restart** keeps Mac work running. The VPS rebuilds its view from the database and the lease. Each open Mac run is adopted live: its running turn and open asks come back. Waiting commands keep waiting.
- **A Mac process restart** shows as a new process identity in the lease. The VPS withdraws the old process's waiting commands. Fetched commands without a result become uncertain. Then, for each conversation separately, the VPS waits until the Mac has sent everything it held for it. Only then does the turn end interrupted. The conversation gets one recovery prompt, which resumes the saved session ([Conversations](conversations.md#lifecycle)).

## Health and alarms

Every wait has an owner, a start time, and a limit. The stall checker runs inside the server every 15 seconds. It records each overdue wait as a finding that names the Ticket and conversation, and it writes a heartbeat.

| Stall | Limit |
| --- | --- |
| A conversation's oldest Mac item does not move | 3 min |
| A command is not fetched | 2 min |
| A stop or retirement has no proof | 3 min |
| A Mac-restart drain does not finish | 3 min |
| A working turn with no tool running sends nothing | 3 min |
| The Mac makes contact, but no lease is accepted | 3 min |
| A Mac item or a VPS-only save keeps retrying | 2 min |

The watchdog is its own unit. A timer runs it every 30 seconds, outside the server, so a stuck server can still raise an alarm. It reads the database read-only. It alarms on each new finding and on a heartbeat older than 90 seconds. It pushes to Khushal, unless the "Stalled work" [notification](notifications.md) is off, and it messages the Chief. Its state file, `data/stall-watchdog/stall-watchdog-state.json`, records what it has alarmed on, so each stall alarms once.

| Path | Worst case |
| --- | --- |
| A stall | 180 s limit + 15 s sampling + 15 s checker + 30 s watchdog = 240 s |
| A dead checker | 90 s stale heartbeat + 30 s watchdog = 120 s |

Both are within 5 minutes. The watchdog alarms only after Khushal installs its timer ([Deployment](deployment.md#the-switch-to-mailboxes)).

A sleeping Mac raises no alarm. Stalls that depend on the Mac count only time while it is continuously present, and pause while it is away. The lease-failing stall does not pause, but a sleeping Mac makes no contact.

Known gaps:

- **The restart gap.** An item stall or a lease-failing stall that has not reached its limit counts again from zero after a VPS restart. An overdue one keeps its start.
- A stuck executor that sends no leases looks the same as sleep.
- A model that thinks for over 3 minutes without live text can raise a false silent-turn alarm.
- A write stuck inside its database transaction blocks every writer. Only the heartbeat alarm catches it.

## What runs on the Mac

One installation lives in the owner's normal account:

- **The Panels app** is a signed native launcher for a fixed supervisor.
- **The fixed installation** keeps its Python environment and supervisor. It selects a versioned runtime and keeps the previous runtime for recovery.
- **A LaunchAgent** starts at login and restarts the service after a crash. A clean maintenance exit leaves it stopped.
- **The data folder** holds the outbox, the command journal, Ticket files, skills, the workspace, the socket, and logs. Updates never replace it.

**Mac only.** The LaunchAgent marks the service `ProcessType=Interactive`. Agent processes run at a lower priority, and the builds they start inherit it, so builds cannot starve the service. The service records its agent processes, and at its next start, before its first lease, it ends any that still run. So a replaced Mac process's agents are really gone. None of this applies on the VPS.

**Permissions.** macOS asks for protected-folder consent on behalf of a responsible app. The signed Panels app lets macOS associate consent with Panels. Its Apple Development signature is for this Mac only.

**Updates.** The lease reply carries the update target, and a current Mac downloads its runtime ahead of time. One press of Update covers both machines ([Deployment](deployment.md#the-card-and-the-mac)). The new runtime goes beside the previous one. If it does not lease and confirm in time, the supervisor reverts. A Mac too old to lease still updates through the separate update channel.

**Versions.** The VPS accepts protocol 22 only, with outbox format 5 and command journal format 1. An older Mac is refused with "Mac needs update". An unknown outbox or journal format fails without conversion or deletion.

The steps are in the [Mac executor installation runbook](../ops/mac-executor/installation.md).

## Decisions that shape it

- The VPS stays the only record. The Mac holds work in transit, never canonical state.
- The Mac pulls its work. The VPS needs no route to the Mac, and the Mac needs no inbound port.
- The Tailscale login decides who may connect. The executor ID chooses which Mac is meant.
- Each message is judged by its own IDs: the command ID, the item number, and the Mac process. No connection plays a part.
- Uncertainty is kept. A timeout or lost reply is not a refusal, and a missing process is not a stop.
- The outbox format does not change, so the supervisor can always revert to the previous runtime.
- The fallback is the previous release, not a second path in code.
- The Mac and VPS halves never import each other's code. They meet only through the shared protocol.

## What is not yet proven

- **The sleep and wake rules.** They are not built. The Mac does not report "going to sleep" and has no wake hook. They wait for the sleep test.
- **The real Mac sleep test.** A script records what a Claude Code turn and a Codex turn do across a 2, 15, and 60 minute sleep. It has not run. It waits for a time agreed with Khushal, who wakes the Mac by hand.
- That launchd or the start-up cleanup ends provider processes when the service restarts.
- That lower agent priority keeps the service responsive under real build load.
- That Tailscale Serve holds a 50-second long poll.
- Running after a reboot before login.
- That macOS attributes access to the Panels app for every provider, and keeps grants across updates.
- That the Mac's disk honours the outbox's and journal's durability flushes through power loss.

## Code

- `src/planner/executor/`: both halves. `routing.py` sends Mac Tickets to the executor. `protocol.py`, `contracts.py`, `configuration.py`, and `logic/` are shared. `vps_*` is the VPS half: `vps_presence.py` (the lease), `vps_mailbox.py`, `vps_runs.py`, `vps_inbox.py`, and `app_transport.py` (the routes). `mac_*` is the Mac half: `mac_presence.py`, `mac_mailbox.py` (the journal), `mac_runner.py`, `mac_outbox.py`, and `mac_agent_processes.py` (priority and cleanup).
- `src/planner/execution_configuration.py` and `execution_identity.py`: the versions and identities.
- `src/planner/tickets/locale_configuration.py` and `runtime/worker_step_readiness.py`: when a Mac Ticket may start.
- `src/planner/conversation/logic/retained_turn.py` and `conversation/deploy_interruptions/`: adoption after a VPS restart, and recovery after a Mac restart.
- `src/planner/stalls/`: the wait registry, checker, rules (`logic/findings.py`), and watchdog. `ops/panels-environments/panels-stall-watchdog.*`: its units.
- `src/planner/core/migrations/versions/executor_mailboxes.py`: the presence, mailbox, and stall tables.
- `src/planner/environments/mac_executor.py`, `environments/mac_app/`, `executor/runtime_bootstrap.py`, `executor/mac_updates.py`, and `environments/mac_updates.py`: the LaunchAgent, signed app, supervisor, and updates.
- `web/src/lib/ticketMachines.ts`: the presence and Ticket labels.
- `scripts/mac_sleep_test.py`: the sleep test.

_Last verified: 2026-11-09._
