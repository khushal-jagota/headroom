# The Mac executor

The executor lets a Ticket's Worker run on Khushal's Mac while the VPS keeps the Ticket, its conversation, and every Panels record. Some work needs the Mac: its files, its apps, its provider sign-ins, and the macOS permissions granted there. The executor brings that work to the Mac without making the Mac a second Panels server.

## What it is made of

- **The VPS endpoint.** Part of the ordinary Panels server. It admits the Mac's connection, sends it commands, and applies what comes back through the same writers the VPS uses for local work.
- **The Mac service.** One long-running process on the Mac. It keeps a connection to the VPS, starts and stops agent processes, and holds everything it has not yet delivered.
- **The connection.** The Mac dials out to the VPS app over HTTPS. One WebSocket carries commands and receipts, and plain HTTP carries file bytes. The VPS never dials the Mac.
- **The outbox.** A small database on the Mac that keeps each piece of agent output, and any files it refers to, until the VPS confirms it.
- **The installation.** What keeps the service running on the Mac. See [What runs on the Mac](#what-runs-on-the-mac).

```
 VPS: Tickets, conversations, files, canonical writers
   ▲                                   │
   │ saved output, files, receipts     │ start, prompt, answer, stop
   │                                   ▼
 one outbound connection, opened by the Mac
   ▲                                   │
   │                                   ▼
 Mac service ── outbox ── agent process (Codex, Claude or Hermes)
                              │
                       Mac workspace and Ticket folder
```

## Which work goes to the Mac

Each Ticket has a locale, `vps` or `macos` (see [Tickets](tickets.md)). When a Worker step starts, a Ticket whose locale is `macos` gets an agent process on the Mac. Everything else stays on the VPS: VPS Tickets, the Chief, Managers, and goal agents. The VPS starts Mac work only while the Mac is connected and its reconnect check has finished. Otherwise the Ticket waits.

Mac execution is switched on per server: the VPS configuration names one executor ID, and only a Mac presenting that ID is admitted. Without it, the VPS runs everything locally.

All three backends can run on the Mac, with the same abilities as on the VPS. A missing provider install or sign-in on the Mac refuses the start.

A Mac Worker runs in its Project's folder, taken as a path on the Mac. A Ticket without one uses a Panels workspace inside the executor's data folder, which survives updates. [Conversations](conversations.md#the-workspace-for-an-agent) owns the workspace rules.

## A run on the Mac

Before it sends a start command, the VPS records which Mac agent process belongs to which conversation. That record lets it find and stop the process later, even after a restart.

To start, the Mac fetches the files the prompt needs, installs the current skills, prepares the workspace, and then starts the provider. Skills come from the VPS database at each start and are linked into the provider's normal home on the Mac, so a Mac Worker reads the same skills as a VPS Worker.

The Worker uses ordinary tools. Its Ticket artifacts go to a Ticket folder on the Mac, and the Mac copies new or changed files to the Ticket's folder on the VPS. When the Worker runs `panels`, the command goes to the Mac service. Reads go straight through to the VPS. Writes are saved in the outbox and applied on the VPS with the Worker's own authority, which comes from the VPS's record of the run and not from anything the Mac claims.

## Getting work back to the VPS

The Mac saves each piece of output before it sends it. The VPS applies it and records a receipt in one step, so a resend after a lost reply returns the first result instead of applying the work twice.

A file reaches the VPS before any message that refers to it is accepted. A link in a Ticket field therefore never points at a file that has not arrived.

Live text, thinking, and tool progress skip the outbox. They show in the conversation while the connection is up and are lost during an outage. Finished messages and all other recorded events are saved and resent.

## When the connection drops

A lost connection supplies no answer. Panels never guesses an outcome.

- **A short outage** leaves the Mac's agent processes running. On reconnect the VPS checks which processes, turns, and open questions still exist, and the Mac resends what it saved.
- **A VPS or Mac service restart** ends every Mac run that was live before it. The next send starts a fresh process that resumes the provider session. Anything whose delivery was uncertain stays uncertain and is not resent.
- **A Worker's `panels` write** that cannot be confirmed says so. It stays in the outbox, and the Worker must not repeat it.

Answers to permission and question requests can be saved while the Mac is offline. A saved answer counts as applied only when the Mac confirms that the original request received it.

A stop counts only when the Mac's final stop receipt arrives, after all of that process's earlier output has been received. A process missing from the Mac's list is not proof that it stopped.

## Model lists and health

Each machine reads its own backends' model lists. The VPS keeps the Mac's last answer, so pickers for Mac Tickets still work while the Mac is offline. [Conversations](conversations.md) covers the picker.

The executor health read shows whether the Mac is connected and ready, and when the VPS last heard from it.
A version refusal shows “Mac needs update” with both versions instead of a generic disconnection.
Mac Tickets that cannot start use the same reason. Last contact and refusal survive a server restart.
A compatible reconnect clears the refusal. Until then, it describes the last contact and does not prove that the Mac is online.

The single update answer includes Mac status beside the VPS result. An update completes when the VPS is done.
New Mac software also exposes the refusal in its local status. This does not start an update.

## What runs on the Mac

One installation lives in the owner's normal account:

- **The Panels app.** A small native launcher, signed for this Mac, that starts the executor in the Python environment.
- **A Python environment and a source checkout.** Both are replaced on each update. The owner installs and signs in to the providers.
- **A LaunchAgent** that starts the app at login, restarts it after a crash, and leaves it stopped after a clean maintenance exit.
- **The executor data folder.** The outbox, Ticket files, skill copies, the default workspace, the local socket, and logs. Updates never replace it.

**Permissions.** macOS asks for consent to protected folders on behalf of a responsible app. The signed Panels app exists so that consent belongs to "Panels" rather than to Python or to each provider. The signature is an Apple Development signature for this Mac only. It is not a distributable or notarized release.

**Updates.** An operator updates the Mac by hand. First the executor is asked to quiesce: it stops taking new work and exits only once no run, open request, uncertain result, or unsent output remains. The operator confirms the old process is gone, replaces the code, environment, and app, and starts the LaunchAgent again. Both machines must speak the same protocol version, and the Mac must recognise its outbox format. A mismatch refuses work before anything runs, and nothing is converted. There is no automatic updater and no automatic rollback.

The installation uses the owner's real home folder, so it shares provider sign-ins and skill folders with anything else in that account, including a second executor installation.

The steps are in the [Mac executor installation runbook](../ops/mac-executor/installation.md).

## Decisions that shape it

- The VPS stays the only record. The Mac holds work in transit, never canonical state.
- The Mac dials out. The VPS needs no route to the Mac, and the Mac needs no inbound port.
- The Tailscale login decides who may connect. The executor ID chooses which Mac is meant, and is not a password.
- Uncertainty is kept, never resolved by guessing. A timeout or lost reply is not a refusal, and a missing process is not a stop.
- The Mac and VPS halves never import each other's code. They meet only through the shared protocol, and the conversation system knows nothing about the executor.

## What is not yet proven

These still need confirming on a real Mac:

- That launchd ends provider processes when the service restarts.
- Running after a reboot before login. The LaunchAgent runs only in a logged-in session.
- That macOS attributes access to the Panels app for every provider, and keeps the grant across app and Python updates.
- That the Mac's disk honours the outbox's durability flushes through power loss.

## Code

- `src/planner/executor/`: both halves. `routing.py` sends Mac Tickets to the executor. `mac_*` is the Mac service, runner, outbox, and agent gateway. `vps_*` is the endpoint, runs, and inbox. `link.py` and `protocol.py` are shared.
- `src/planner/execution_configuration.py` and `src/planner/execution_identity.py`: the protocol and outbox versions and the shared identities.
- `src/planner/tickets/locale_configuration.py`: when a Mac Ticket may start.
- `src/planner/environments/mac_executor.py` and `src/planner/environments/mac_app/`: the LaunchAgent and the signed app.

_Last verified: 2026-11-04._
