# Who may act

One sentence decides almost every guarded write in Headroom: you may act on anything strictly below you.

Khushal, Chief, Managers, and Workers all call the same routes. There is no separate interface for agents. Who the caller is, and where the target sits, decides whether the call is admitted.

## Who can call

The server knows four kinds of caller: the **owner** (Khushal), **Chief**, a **Manager**, and a **Ticket** (its Worker). Goals have no agent. Old goal transcripts remain stored without a goal conversation interface. The authenticated browser is the owner. An agent's `panels` calls carry its identity, set by the conversation that launched it.

The live service sets `PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS=1`. For loopback calls without a nonempty `X-Plan-Actor`, the server checks the socket's holders through `/proc`. Ticket or Manager IDs alone do not skip this check. It refuses callers inside `panels-live.service` or `panels-workers.slice` with `agent_forbidden` and asks them to restore `PLAN_ACTOR`. At least one readable holder must establish the caller's identity. Every readable holder must remain outside both agent cgroups.

Calls with a nonempty actor, authenticated browser calls, and nonloopback callers retain their existing path without a socket lookup. Missing or unknown client addresses still require evidence.

If a same-user process has unreadable descriptors, the server checks its cgroup. The server skips that process only if the cgroup is readable and outside both agent cgroups. Otherwise it refuses the call. The refusal detail and debug log include the number of skipped processes. Missing socket evidence or unreadable holder evidence also causes refusal. Other local callers remain the owner.

This environment-only setting defaults to off. The application removes it from its environment at startup, so agents and their scratch servers do not inherit it. Generic agents with no role identity also lose implicit owner access to Panels when the setting is on.

The Hermes gateway (`hermes-gateway.service`) is a known owner-level caller outside Panels. Its calls without identity remain the owner.

A known gap remains: a local process can send a forged `tailscale-user-login` header directly to loopback. Trusted ingress then treats it as the owner. This rule does not close that gap.

Uvicorn also trusts `X-Forwarded-For` from `127.0.0.1` by default. A local process can forge a nonloopback address there and skip this check. This deliberate forgery stays out of scope.

Deliberate forgery through hidden descriptors outside the agent cgroups is also outside this rule. The scan cannot inspect descriptors that the kernel hides from it.

Manager requests use the `manager` actor and `X-Plan-Manager-ID`. Old Sprint Item actors and identity headers do not establish authority.

## Position

```text
Khushal ─┬─ everything
Chief ───┘

Manager ─ its held goals and current child Tickets

Ticket ─── nothing
```

A Manager stands above a Ticket because the Ticket is assigned to it. Move the Ticket and the old Manager no longer stands above it. A Ticket stands above nothing, so its Worker cannot decide its own proposal. That is the rule answering, not an extra ban. (A Worker type could declare something its Tickets stand above. No current type does.)

The same rule covers owner-level configuration: skills, Worker types, and projects sit at the top, so only Khushal and Chief stand above them.

## Your own record

Acting as yourself is a different question from standing above something. A Worker files proposals, writes its recap, and asks for help on its own Ticket. A Manager writes its own goal, recap, and artifacts. Chief or the holding Manager edits a goal and its page through the same chain. The chain does not decide these.

Proposing is the Ticket speaking about its own work, so nobody, including Khushal, proposes on a Ticket's behalf.

## The exceptions

- **Re-parenting.** Only Khushal and Chief may move a Ticket to another Manager. At the moment of the call, the Ticket really is below the Manager trying to hand it away, so the plain rule would allow it. The exception exists so authority cannot be given away by whoever currently holds it.
- **Creation.** Making a Ticket acts on nothing that exists yet, so the rule cannot refuse it. The pieces it arrives with are checked one at a time instead. You can place it under a Manager in your own chain. Only someone above the new Ticket may set its ceiling and holder.
- **Doors about who you are.** A small set of routes is not about any target. They ask only what kind of caller this is. Some admit only Khushal: his own reads and sends in a conversation, answering an agent's permission question, saving a credential, installing an update, and settling a Manager. Others admit Khushal or Chief, such as Chief's recap and the defaults for new Managers. These are deliberate and few.

Who must look at a parked proposal, the holder, is a narrower fact owned by the [Ticket lifecycle](tickets.md). Holding a review grants nothing the rule does not already give.

## What this is not

The rule decides what a caller may do. It does not prove who the caller is. The browser and the command line state an identity and the server believes it. It is not a login system.

Reading Tickets and Managers is open to every caller. The rule guards writes.

Recap requests follow the same hierarchy. The owner can request any agent. Chief can request Managers and Tickets. Managers can request their child Tickets. A Ticket can request none. Each request names its caller and clears the old recap before delivery.

## Decisions that shape it

- **One rule instead of per-route lists.** Adding a route means asking where its target sits, not writing a new list of who is allowed.
- **Self-approval is impossible by structure.** It falls out of position, so no route can forget to forbid it.
- **Authority cannot be handed away.** Re-parenting is reserved for the top.
- **Identity is trusted, not proven.** Keeping strangers out is the host's job, not this rule's.

## Code

- `src/planner/core/authority/logic.py`: the rule, the own-record check, and who may hold a review.
- `src/planner/core/authority/intake.py`: checks requests and Mac commands against the target and relation at intake.
- `src/planner/core/authority/service.py`: supplies the database facts for the rank rule.
- `src/planner/core/authctx.py`: turns a request into a caller, and holds the owner-only doors.

_Last verified: 2026-11-12._
