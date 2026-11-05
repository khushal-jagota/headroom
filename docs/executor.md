# The Mac executor

The executor lets a Ticket's Worker use the Mac's files, apps, provider sign-ins, and macOS permissions. The VPS keeps the Ticket, conversation, and every Panels record. The Mac does not become a second Panels server.

## What it is made of

- **The VPS endpoint** admits the connection, sends commands, and applies results through the ordinary canonical writers.
- **The Mac service** maintains the connection and starts and stops agent processes.
- **The connection** carries commands and receipts over a WebSocket, and file bytes over HTTPS. The Mac opens it.
- **The outbox** keeps agent output and its files until the VPS confirms receipt.
- **The installation** keeps the service alive. See [What runs on the Mac](#what-runs-on-the-mac).

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

Each Ticket has a locale, `vps` or `macos` (see [Tickets](tickets.md)). A `macos` Worker step gets an agent process on the Mac. VPS Tickets, the Chief, Managers, and goal agents stay on the VPS. Mac work waits until the Mac connects and completes its reconnect check.

The VPS configuration names one executor ID. Only that executor is admitted. Without this configuration, the VPS runs everything locally.

All three backends can run on the Mac, with the same abilities as on the VPS. A missing provider install or sign-in on the Mac refuses the start.

A Mac Worker runs in its Project's folder, taken as a path on the Mac. A Ticket without one uses a Panels workspace inside the executor's data folder, which survives updates. [Conversations](conversations.md#the-workspace-for-an-agent) owns the workspace rules.

## A run on the Mac

The VPS records which Mac agent belongs to each conversation before it sends a start command. This record survives restart.

The Mac fetches prompt files, installs skills, prepares the workspace, and starts the provider. Skills come from the VPS database at each start and link into the provider's normal home.

Ticket artifacts go to a folder on the Mac. The Mac copies changed files to the Ticket's VPS folder. The Worker's `panels` commands use the Mac service. Reads go directly to the VPS. Writes enter the outbox, then the VPS applies them with authority from its original run record.

## Getting work back to the VPS

The Mac saves each piece of output before it sends it. The VPS applies it and records a receipt in one step, so a resend after a lost reply returns the first result instead of applying the work twice.

A file reaches the VPS before any message that refers to it is accepted. A link in a Ticket field therefore never points at a file that has not arrived.

Live text, thinking, and tool progress skip the outbox. They show in the conversation while the connection is up and are lost during an outage. Finished messages and all other recorded events are saved and resent.

## When the connection drops

A lost connection supplies no answer. Panels never guesses an outcome.

- **A short outage** leaves the Mac's agent processes running. On reconnect the VPS checks which processes, turns, and open questions still exist, and the Mac resends what it saved.
- **A VPS or Mac service restart** ends every Mac run that was live before it. The next send starts a fresh process that resumes the provider session. An uncertain provider call stays uncertain and is not repeated.
- **A Worker's `panels` write** that cannot be confirmed says so. It stays in the outbox, and the Worker must not repeat it.

Answers to permission and question requests can be saved while the Mac is offline. A saved answer counts as applied only when the Mac confirms that the original request received it.

A stop counts only after its final receipt and all earlier output arrive. A missing process is not proof of a stop.

## Model lists and health

Each machine reads its own model lists. The VPS retains the Mac's last answer for offline pickers. [Conversations](conversations.md) covers the picker.

Health shows connection readiness and last contact. A version refusal shows “Mac needs update” with both versions. Mac Tickets use the same refusal reason. Last contact and refusal survive restart. A compatible reconnect clears the refusal. Until then, it describes historical contact, not current presence.

The update status includes the Mac's target, progress, and failure beside the VPS result. An update completes when the VPS is done. A Mac that still waits for its target does not imply that it is offline.

## What runs on the Mac

One installation lives in the owner's normal account:

- **The Panels app** is a signed native launcher for a fixed supervisor.
- **The fixed installation** retains its Python environment and supervisor. They select a versioned runtime and keep the prior runtime for recovery.
- **A prebuilt runtime** supplies Python and agent packages. The owner supplies sign-ins and other native tools.
- **A LaunchAgent** starts at login and restarts after a crash. A clean maintenance exit leaves it stopped.
- **The data folder** holds the outbox, Ticket files, skills, workspace, socket, and logs. Updates never replace it.

**Permissions.** macOS asks for protected-folder consent on behalf of a responsible app. Panels uses a signed app so that macOS can associate consent with Panels. Its Apple Development signature is for this Mac only, without distribution or notarization.

**Updates.** One owner approval names the release and timing for both machines. “Update now” stops connected Mac agents before VPS installation. “When work finishes” waits for idle work. The Mac checks while connected and after reconnect or wake. Its authenticated update channel works even when its work protocol is too old.

The VPS retains the approved Mac archive and target outside database rollback and publication expiry. After VPS success, the Mac installs those bytes beside its previous runtime. If the candidate fails to report readiness, the supervisor reverts. The launcher and data folder stay in place.

Saved results and their files remain in the outbox through the swap. A planned handoff retains their original authority until the VPS receives them. It does not repeat provider calls or restore stopped agents. Ordinary maintenance still waits for an empty outbox before exit.

The VPS accepts the current work protocol or one older. The current window is protocols 19 and 20, both with outbox format 5. Messages ignore unknown fields but still check their known fields and identities. The protocol check detects schema changes without a version change. Unsupported outbox formats fail without conversion or deletion.

Existing installations need one operator-controlled bootstrap installation. Its Python environment and supervisor then remain fixed. Local fixtures do not prove permission retention, certificate renewal behavior, or Gatekeeper acceptance.

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
- That permission grants survive a runtime swap and certificate renewal, and Gatekeeper accepts the downloaded runtime through this updater.
- That the Mac's disk honours the outbox's durability flushes through power loss.

## Code

- `src/planner/executor/`: both halves. `routing.py` sends Mac Tickets to the executor. `mac_*` is the Mac service, runner, outbox, and agent gateway. `vps_*` is the endpoint, runs, and inbox. `link.py` and `protocol.py` are shared.
- `src/planner/execution_configuration.py` and `src/planner/execution_identity.py`: the protocol and outbox versions and the shared identities.
- `src/planner/tickets/locale_configuration.py`: when a Mac Ticket may start.
- `src/planner/environments/mac_executor.py` and `src/planner/environments/mac_app/`: the LaunchAgent and the signed app.
- `src/planner/executor/runtime_bootstrap.py`, `mac_updates.py`, and `src/planner/environments/mac_updates.py`: the fixed supervisor, update client, retained target, and progress.

_Last verified: 2026-11-04._
