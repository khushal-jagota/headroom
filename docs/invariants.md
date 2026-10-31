# Decisions that must stay put

These are the product decisions that look removable until a later change tries to remove them. Each one is load-bearing. A change that crosses one should say so in the open.

## Only sent text reaches an agent

A Ticket field, a proposal row, an event, a browser update, or a line in the UI is not model context. The Worker knows what was delivered into its conversation. To change what it sees, send a real prompt. The notebook is the honest record of that delivery. It is not a side channel the model reads later.

## One door settles a gate

Workers propose. The proposal resolver settles the current stage’s text and advances exactly one stage, or it parks the proposal. There is no arbitrary stage setter. A direct user completion exists only for the current user-owned stage, and it uses that same door. Accepting a proposal records the next ceiling and the next holder in the same write.

## The claim starts work and does not watch it

Readiness takes one worker-step claim, sends the opener, and stops. Started, queued, and injected are success. On a worker-owned stage the claim stays out until a proposal is filed or a parked proposal is decided. On a user-owned opener the claim returns as soon as that send is accepted, and the recorded opener fact is what stops a second automatic prompt. A refusal or a suppressed send gives the claim back and forgets a tentative opener. An uncertain delivery leaves the claim errored until someone restarts the Worker. Nothing listens for the turn to end. The stage moves when a proposal is filed or a parked proposal is decided.

The claim is the only control fact the Ticket stores. Liveness is a separate question, answered by asking the conversation whether a turn is running.

## The change signal carries no payload

Every normal database commit announces that something was written. The announcement names nothing. Open screens and the readiness loop respond by reading current state. SQLite and the periodic timer remain the canonical answer if an announcement is missed. A few writers stay quiet on purpose, where no screen is waiting on those rows and the open conversation is handed its own tail directly.

## You may act on anything strictly below you

Khushal and Chief stand above everything. A Manager stands above its current child Tickets. A Ticket stands above nothing, so its Worker cannot approve its own proposal. That falls out of the rule.

Three narrow controls sit beside it, each for a stated reason:

- Re-parenting a Ticket changes who stands above it, so only Khushal and Chief may do it.
- Settling a Manager is an owner-only presentation control. Chief never settles, and settlement does not stop children.
- Who a caller is remains a local claim the server believes. The rule decides what that caller may do, not how they proved their name.

## Three agent backends, one process, one home

A conversation runs Hermes, Codex, or Claude. Untrusted names are refused at one door. One conversation has at most one child process. Each real agent identity has one canonical home and one credential. Headroom does not keep a second copy of that login.

## The accent means a person is needed

One accent color, currently the soft steel in `assets/tokens.css`, means needs the human: a gate, an approval, a required action, and the enabled Send control. It is not decoration, and it stays out of ordinary agent prose. A selected row is a sunken surface, not the accent. Conversation keeps one human pill and bare agent prose. The approval surface is the one raised control.

## The skill row is the instruction

`managed_skills` is the authority for skill text. The files under `data/skills/` are copies written from those rows. The packaged tree under `src/planner/skills/` seeds an empty database and is not read again. An edit in the package changes a fresh install only.

## A schedule creates a Ticket and stops

A due schedule writes an ordinary Ticket and an occurrence receipt for that local minute. It does not start the Worker. Readiness does, after the commit. Missed minutes are not backfilled.

## Gates are workflow, not a sandbox

Ticket stages, ceilings, and authority decide who may advance the work record. They do not confine a process to a folder, a network, or a credential. Preview links are trusted same-origin content for that reason. See [Previews](previews.md).

_Last verified: 2026-10-30._
