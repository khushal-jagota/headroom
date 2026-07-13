# Ticket types

Not every ticket does the same kind of work, so not every ticket walks the same
stages. A **ticket type** is a single registry entry that declares one workflow end
to end: the ordered stages a ticket of that type moves through, the field each stage
gates, the fields it carries, and its **worker** — the specialist skill (and the
model / toolset) that knows how to do that kind of work. The engine holds no "if this
is a coding ticket" branches; it reads the type and follows whatever the type says.

Two types ship today. **`coding`** is the default — product or repository work, the
six-stage lifecycle the planner has always had. **`new_worker`** is the create-a-worker
worker: its own worker designs and lands *another* worker, through a bespoke lifecycle
of its own. A third, **`probe`**, is a test-only fixture — never a shipped type; it
exists only to prove the machinery is genuinely general, not coding-shaped in disguise.

```
                    ONE DEFINITION per type
              (stages · gates · fields · worker profile)
                     validated at boot, or refuse to start
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                        ▼
        THE ENGINE                               THE WORKER
   reads order / gates / fields              reads the profile → its
   through the one seam                      specialist skill (how to
   (coding_bridge)                           do this type's stages)
        │                                        │
        ▼                                        ▼
   drives the ticket                        does each stage's work
   through its stages                       and proposes
```

The same definition feeds two consumers, and neither hard-codes the workflow. Add a
type and both the engine and the workers pick it up — no new branch in either.

## What a definition holds

One definition is a plain description of a workflow:

- **Stages** — the full ordered list, from a leading `needs_kickoff` to a trailing
  `done`, plus the reserved `dropped` terminal that sits outside the line (a ticket
  can be dropped from anywhere). Each non-terminal stage names the one field it gates:
  the blank that must be filled and accepted for the ticket to move on.
- **Fields** — the ordered set of blanks the ticket carries, led by `kickoff`. Every
  field is gated by exactly one stage, and every non-terminal stage gates exactly one
  field; the two lists are two views of the same spine.
- **A worker profile** — which specialist skill guides this type's work, and optionally
  a model and toolset. Today only the skill name is load-bearing; the rest is declared
  but unused.
- **Transition hooks** — a small table for the rare case where accepting one stage's
  work should durably change the ticket's runtime status. `coding` has one: when an
  accepted plan hands a human-assigned ticket to implementation, control becomes a
  durable user takeover. `new_worker` has none — every one of its stages is bounded
  agent work with no human handoff.

A definition is checked the moment the registry is built, against a fixed set of rules:
the first stage must be `needs_kickoff` gating `kickoff`, the last must be `done`, every
field gated once and only once, every worker skill and toolset must be one the system
actually knows, and so on. A malformed definition raises there and then — so a broken
type makes the server **refuse to boot loudly**, rather than failing on the first ticket
that happens to use it. The reference lists the validator checks against (which skill
names and toolsets exist) are handed in from outside, so the type machinery stays a
self-contained corner that nothing else depends back on.

_Code paths:_ `src/planner/ticket_types/contracts.py` (the shapes),
`src/planner/ticket_types/coding.py` and `new_worker.py` (the two shipped definitions),
`src/planner/ticket_types/logic/validation.py` (the boot-time rules),
`src/planner/ticket_types/registry.py` (the registry that validates on construction),
`src/planner/ticket_types/logic/views.py` (the derived order / gate / advance lookups).

## The two doors it feeds

A definition reaches the rest of the system through exactly two doors.

**The engine** reads a ticket's type through one seam — a single module that is the
only place in the engine allowed to name the type registry. Every other engine module
that needs to know a ticket's stage order, its gates, or where a stage advances to
reaches those answers through that one seam, resolving each ticket row's *own* type.
This is why there are no per-type branches: a generic operation takes the definition and
indexes its tables, so a ticket with unfamiliar stages costs the engine nothing.

**The web** reads types through an HTTP endpoint, `GET /api/ticket-types`, which serves
one entry per registered type: its stages, labels, gates, fields, and ceiling range, in
a single flat shape. The frontend turns each entry into a per-type lifecycle and renders
a ticket using *its* type's stages — so a coding ticket and a new_worker ticket show
different spines from the same code, driven entirely by what the server served.

_Code paths:_ `src/planner/tickets/logic/coding_bridge.py` (the single engine seam),
`src/planner/ticket_types/logic/manifest.py` (the served shape),
`src/planner/core/server.py` (`GET /api/ticket-types`),
`web/src/lib/lifecycle.ts` (the manifest → per-type lifecycle the frontend renders from).

## The start ceiling, and the first working stage

How far a worker may go on its own is a ticket's **scope**, and its ceiling is where the
type machinery meets it. Two thresholds sit near the front of every type's lifecycle,
and they are deliberately kept apart:

- **The default start ceiling is `needs_kickoff`, for every type.** A fresh ticket is
  leashed right at kickoff: nothing advances until the human grants scope. This is the
  "review before agents start" rule made structural — the leading `needs_kickoff` stage
  is itself a selectable ceiling, and a new ticket starts scoped exactly to it.
- **The first working stage is the *second* stage** — the first one that does real work
  (`needs_success` for coding, `needs_stages` for new_worker). This is a distinct view,
  not the same number as the start ceiling. Three things key off it: the gate that
  decides when a recap may be written, the read that decides a sprint is in progress,
  and the seed point for work imported from outside Panels.

These two used to be one value, which was fine only while a fresh ticket started at the
second stage. Moving the start ceiling back to kickoff without splitting them would have
fired those three behaviours one stage too early; keeping them as separate derived views
preserves each. Neither is a per-type knob — both are read off the type's own stage order.

_Code paths:_ `src/planner/ticket_types/logic/views.py`
(`default_ceiling`, `ceiling_range`, `first_worker_stage`).

## How the worker finds its specialist

A worker does not get routed to its type's skill by code — it routes itself. The launched
base role is `panels-worker`, and it is deliberately **type-agnostic**: it knows how to
work *a* ticket one step at a time, but nothing about coding's or new_worker's particular
stages. That base skill's text is preloaded when the worker starts.

To specialize, the worker runs `panels worker my-ticket`. Alongside the ticket's id and
state, that command reports the ticket's **worker** — the specialist skill for its type,
resolved from the definition's worker profile. The worker then loads that skill on demand
with the `skill_view` tool and follows it for the stage-by-stage work. The base role's job
is only to point the way; the specialist carries the substance:

- `panels-worker-coding` — coding tickets: success, approach, plan, implementation, closeout.
- `panels-worker-new-worker` — new_worker tickets: stages, thinking, drafting, closeout.

Preloading only names the base role; it never gates later access, so `skill_view` can load
any specialist in the worker's Hermes home. The one precondition is that the worker's
toolset must include `skill_view` — cut below that and self-routing breaks.

_Code paths:_ `skills/panels-worker/SKILL.md` (the base role and its my-ticket → skill_view
step), `skills/panels-worker-coding/SKILL.md` and `skills/panels-worker-new-worker/SKILL.md`
(the specialists), `src/planner/tickets/api.py` (`GET /tickets/by-session/{key}` returns the
`worker` field), `src/planner/cli/main.py` (`worker my-ticket`).

## Adding a worker — the recipe

This is the centerpiece: the exact sequence to add a new type to the running system. It is
what a `new_worker` ticket's own closeout follows, and it feeds both doors at once — the
type for the engine, the skill for the worker.

1. **Write the specialist `SKILL.md`** under `skills/<name>/` — front matter plus one
   guidance section per stage: what a good result at that stage is, and who does it.
2. **Add the definition module** under `src/planner/ticket_types/` — the workflow:
   ordered stages, the field each gates, the ordered fields, the worker profile, any
   transition hook. Reuse the shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`,
   `done`, and `dropped` bookends; novel middle stages are plain strings of your choosing.
3. **Register it** — add the new skill name to the known-skills catalog (so the validator
   accepts the worker profile) and add the definition to the production registry, both in
   the engine seam.
4. **Provision the skill** — add its directory to the planner skill list, so startup links
   the skill into the worker's Hermes home. That link is the step that lets a worker
   `skill_view` the specialist.
5. **Announce the type** to the agent front doors — add the specialist to `panels-worker`'s
   worker list, and add the type (with what it's for) to `panels-chief-of-staff`, so the
   Chief creates and reconciles it instead of filing everything as coding.
6. **Restart.** The registry builds and validates at boot; the skill directories symlink
   into the Hermes home at startup. The `SKILL.md` must exist before the restart, or
   startup fails on a missing skill. A restart is what makes the type live.

`probe` is the test-only proof this recipe generalizes: it declares a lifecycle whose
stages are not coding's and drives a ticket through the real server end to end, with no
worker session, so any ingress point that secretly assumed coding fails the test. It is a
fixture, explicitly not a shipped second type.

_Code paths:_ `skills/panels-worker-new-worker/SKILL.md` (the recipe, as closeout guidance),
`src/planner/tickets/logic/coding_bridge.py` (the known-skills catalog and the production
registry), `src/planner/minds/config.py` (`PLANNER_SKILL_NAMES` and the startup symlinking),
`src/planner/core/server.py` (registry build + skill provisioning at boot),
`tests/support/probe.py` (the test-only fixture type).

## The front doors know the types

The two agent skills a human reads as prose — the base `panels-worker` and the
`panels-chief-of-staff` — each carry an explicit written list of the shipped types, rather
than fetching them from the served manifest. The Chief was type-blind before this: it filed
every piece of work as a coding ticket and had coding's stage names hard-written into its
external-work and worker-boundary guidance. Now it knows the "a new *kind* of worker → a
`new_worker` ticket" mapping and the field sets per type. Keeping those lists current is a
step in a `new_worker` ticket's closeout (announce the new type to both), so they don't
drift as future types ship. An explicit list plus that closeout discipline is chosen over
dynamic discovery precisely because these are prose skills, not a fetch surface.

_Code paths:_ `skills/panels-worker/SKILL.md`, `skills/panels-chief-of-staff/SKILL.md`.

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the scope, the resolution engine, and
  the approval gate the type's stages move through. The `coding` type's stages are the six
  described there.
- **The employee runtime** (`employee-runtime.md`) — the worker that self-routes to the
  specialist skill and files the proposals each stage gates.
- **The front end** (`frontend.md`) — the screens that render a ticket using its type's
  served lifecycle.
- **The command-line tool** (`cli.md`) — where `worker my-ticket` names the specialist.

## Deferred

- **The live self-routing proof has not been observed end to end.** The machinery is built
  and verified in code — the base role points at `skill_view`, `my-ticket` names the
  specialist, and a `probe` ticket drives a non-coding lifecycle through the real server in
  the test suite. A live worker actually reading `my-ticket`, loading its specialist, and
  working a type's stages has not yet been run. Trigger: an owner smoke of a worker
  self-routing through a full type.
- **The worker profile's model and toolset are declared but inert.** Only the specialist
  skill name is consumed today; a definition can name a model / reasoning effort / toolset,
  but nothing reads them. Trigger: per-type model or toolset selection is wired into the
  gateway.

---

_Last verified: 2026-07-13._
