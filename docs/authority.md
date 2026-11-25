# Who may act

One sentence decides almost every guarded write in Headroom: you may act on anything strictly below you.

The owner, Chief, Managers, and Workers all call the same routes. There is no separate interface for agents. Who the caller is, and where the target sits, decides whether the call is admitted.

## Who can call

The server knows four kinds of caller: the **owner**, **Chief**, a **Manager**, and a **Ticket** (its Worker). A call that is none of these is unidentified. Goals have no agent. Old goal transcripts remain stored without a goal conversation interface. An agent's `panels` calls carry its identity, set by the conversation that launched it.

Each agent conversation receives a private launch credential. The CLI sends it automatically. The server checks its hash and resolves the caller from the conversation’s current Ticket, Chief or Manager association. Role names, ids and `X-Plan-*` headers grant no authority. A missing, invalid or revoked agent credential never makes an agent the owner.

Agents use `panels api` when no dedicated command covers a route. This keeps the credential inside the same automatic transport as ordinary commands. Deployment recovery prompts give agents that command rather than an unauthenticated HTTP instruction. Recap requests name `panels recap`, which writes the caller's own recap.

For access from another device, the owner signs in to Tailscale. The installed home trusts its configured Tailscale login. Tailscale Serve adds the login header. This owner path is separate from agent credentials.

A browser write must come from the server's own address. The desktop app is the one other origin the server accepts, `panels://app`, named once in `server/core/http/middleware.ts`. The server sends CORS headers to that origin only, for reads, writes, preflights and live streams. The origin adds no identity: the app still reaches a hosted server through Tailscale Serve. The app as built forwards its requests from its native side, which sends no Origin, so it does not rely on this rule.

Client version headers describe software and devices. They grant no authority and do not replace launch credentials or trusted ingress.

An unidentified caller stands above nothing. It can read what any caller can read and send a message to Chief. Other writes are refused. The refusal directs an agent to the authenticated CLI API command instead of hand-built headers. Panels supplies a resumed child with its credential. A terminal without identity and the stall watchdog use unidentified access on the live host.

A server without trusted ingress, such as staging or a local checkout, retains owner access for a call with no identity. An attempted agent call with an invalid credential or old identity headers stays unidentified there too.

This catches accidents, not deliberate impersonation by a process running as the same Unix user. Such a process could read another process’s launch credential or fake the Tailscale header at the loopback listener. Separate Unix users remain outside this boundary.

## Position

```text
Owner ───┬─ everything
Chief ───┘

Manager ─ its held goals and current child Tickets

Ticket ─── nothing
```

A Manager stands above a Ticket because the Ticket is assigned to it. Move the Ticket and the old Manager no longer stands above it. A Ticket stands above nothing, so its Worker cannot decide its own proposal. That is the rule answering, not an extra ban. (A Worker type could declare something its Tickets stand above. No current type does.)

The same rule covers owner-level configuration: skills, Worker types, and projects sit at the top, so only the owner and Chief stand above them.

## Your own record

Acting as yourself is a different question from standing above something. A Worker files proposals, writes its recap, and asks for help on its own Ticket. A Manager writes its own goal, recap, and artifacts. Chief or the holding Manager edits a goal and its page through the same chain. The chain does not decide these.

Proposing is the Ticket speaking about its own work, so nobody, including the owner, proposes on a Ticket's behalf.

## The exceptions

- **Re-parenting.** Only the owner and Chief may move a Ticket to another Manager. At the moment of the call, the Ticket really is below the Manager trying to hand it away, so the plain rule would allow it. The exception exists so authority cannot be given away by whoever currently holds it.
- **Creation.** Making a Ticket acts on nothing that exists yet, so the rule cannot refuse it. The pieces it arrives with are checked one at a time instead. You can place it under a Manager in your own chain. Only someone above the new Ticket may set its ceiling and holder.
- **Doors about who you are.** A small set of routes is not about any target. They ask only what kind of caller this is. Some admit only the owner for actions such as sending a message, answering a permission question, saving a credential, installing an update, and settling a Manager. Others admit the owner or Chief, such as Chief's recap and the defaults for new Managers. These are deliberate and few.

Who must look at a parked proposal, the holder, is a narrower fact owned by the [Ticket lifecycle](tickets.md). Holding a review grants nothing the rule does not already give.

## What this is not

The hierarchy decides what an authenticated caller may do. Launch credentials establish an agent’s identity; trusted ingress establishes the owner’s identity on the live host.

Reading Tickets and Managers is open to every caller, including an unidentified one. The rule guards writes.

Recap requests follow the same hierarchy. The owner can request any agent. Chief can request Managers and Tickets. Managers can request their child Tickets. A Ticket can request none. Each request names its caller and clears the old recap before delivery.

## Decisions that shape it

- **One rule instead of per-route lists.** Adding a route means asking where its target sits, not writing a new list of who is allowed.
- **Self-approval is impossible by structure.** It falls out of position, so no route can forget to forbid it.
- **Authority cannot be handed away.** Re-parenting is reserved for the top.
- **Agent identity belongs to the conversation.** Reset removes its authority; changing saved role text cannot grant another identity.
- **A failed agent credential never becomes owner access.** Owner access follows its own boundary; a failed credential can only read and message Chief.

## Code

- `server/core/authority/logic.ts`: the rule, the own-record check, and who may hold a review.
- `server/core/authority/intake.ts`: checks requests and Mac commands against the target and relation at intake.
- `server/core/authority/service.ts`: supplies the database facts for the rank rule.
- `server/core/authority/identity.ts`: turns a request into a caller. Executor self-reads enter through a verified in-process context.
- `server/agent_identity/`: conversation credentials, hash storage and private launch delivery.

_Last verified: 2026-11-23._
