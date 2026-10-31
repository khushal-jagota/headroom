# Worker types

A Worker type is a way of paying attention. Coding, research, debugging, and designing a flow do not want the same stages or the same moments of human judgment. The type is chosen when the Ticket is created and does not change afterward. It declares the ladder, which stages the Worker fills and which stages open a collaborative turn, the specialist skill, and the backend, model, and reasoning a new Ticket copies as its starting setup.

The Ticket then owns its launch setup. Later edits to launch defaults affect future Tickets. They do not rewrite an existing Ticket’s launch setup or locale. A change to the type’s ladder also applies to its unfinished Tickets, under the migration rules below.

## The ladder every type shares

Every shipped type starts at Brief and ends at Done, with a Consequences stage before Done. Consequences is where the applicable follow-through is proposed: merge, deploy, spawned Tickets, bookkeeping, a report. What sits between Brief and Consequences is the type’s own argument for how that kind of work should unfold.

A stage marked worker-owned is the Worker’s to draft. A stage marked user-owned receives one automatic opening turn, then continues with the person in the same conversation until the gate is filled or the person completes it. See [Proposals and gates](proposals.md). The ceiling can still park a worker-owned stage. That is how a coding Ticket stops before Implementation even though every coding stage is worker-owned: set the ceiling on Plan.

Ten types ship. `probe` exists only in tests, with unfamiliar stage names, so a coding-shaped assumption fails loudly. `personal`, `planning-day`, `planning-midday-check`, and `planning-sprint` remain readable history. They cannot be created, and their schedules can only be disabled.

Launch defaults below are what a fresh database copies onto a new Ticket. The owner can change prospective defaults and the specialist text in Settings. Hermes ignores reasoning. A Worker type can be marked as requiring macOS. That flag affects future creation only.

## Amendments and existing Tickets

A Worker type save can remove stages and fields even when unfinished Tickets use them. A Ticket on a removed stage moves to the nearest earlier stage that survives, using the old order. A ceiling on a removed stage follows the same rule. Its holder stays the same. For example, a Ticket at What Changes with a Plan ceiling gets a What Changes ceiling when Plan disappears. The save never grants Implementation by accident.

The save preserves stored text in removed fields. It does not invent field values or accept proposals. Finished Tickets remain unchanged. A Ticket whose current stage survives stays there.

The definition and all Ticket migrations save in one transaction. If a Ticket has no earlier destination, an incompatible proposal, or dispatched work whose step or authority changes, the save refuses and names it. Unsafe changes to stage order also refuse. Any failure leaves the definition and Tickets unchanged.

The save result contains each affected Ticket’s title, old and new stage, and old and new ceiling. Field-only effects also appear, even when both positions stay the same. The CLI prints the mapping, and JSON output preserves it. The caller must retain that result in the amendment’s evidence. Migration records do not enter Ticket guidance, which remains instructions for the Worker.

## Coding

Product and repository work. The ladder is Success Condition, What Changes, Plan, Implementation, Consequences. All of those stages are worker-owned. The usual way to keep a person in the loop is the ceiling: stop at the plan, or stop again before Consequences. Default launch is Codex, `gpt-5.6-sol`, medium. Skill: `panels-worker-coding`.

## General

The short ladder for work that does not deserve a special process: Work Done, then Consequences. Worker-owned throughout. Same Codex default as coding. Skill: `panels-worker-general`.

## Debugging

Diagnosis and a proposed fix, without an implementation stage. The Worker writes Problem Understanding, Root Cause, and Proposed Fix, then Consequences. The point is to know the cause and the fix well enough to hand implementation elsewhere, or to continue only after a person agrees. Worker-owned, with reasoning defaulting to high on the same Codex model. Skill: `panels-worker-debugging`.

## Research

A framed question and a sourced synthesis. Research Plan, Findings, Consequences. Worker-owned. Default launch is Claude, `opus`, high, because the workflow wants a long careful read more than a coding agent. Skill: `panels-worker-research`.

## Exploration

An unclear question that should pause and think with the person. Understanding is user-owned, then the Worker drafts a Research Plan and Findings, then Answer is user-owned again, then Proposed Follow-up and Consequences. The two user-owned stages are the product: do not let an open-ended Ticket race ahead of a shared picture, and do not let it declare the answer alone. Codex, medium. Skill: `panels-worker-exploration`.

## Product design

Flows and interactive artifacts before a coding handoff. Wireframe, Design, Consequences. Both middle stages are worker-owned in the shipped declaration. The human review is the ceiling, typically set so Wireframe or Design parks, plus the artifacts opened beside the conversation. Default launch is Claude, `opus`, medium. Skill: `panels-worker-product-design`.

## Initiative planning

Turn a confirmed direction into a set of Tickets. Rough Split into Parts, Question Tree, then Question Answers (user-owned), then Proposed Tickets and Consequences. The user-owned answers are where Khushal settles the questions the split uncovered before Tickets are proposed. Codex, medium. Skill: `panels-worker-initiative-planning`.

## Initiative review

Look at a delivered initiative, capture feedback, and create the follow-up that was actually agreed. Review, then Feedback (user-owned), then Follow-ups and Consequences. Codex, `gpt-6-astra`, high. Skill: `panels-worker-initiative-review`.

## New worker and amend worker

New Worker designs and lands another Worker type. After Brief, Purpose and Boundaries is user-owned, then the Worker drafts Stages and what good looks like, then Model and Effort is user-owned, then Drafting and Consequences. The person owns the purpose and the model choice. The Worker owns the stage design and the draft. Skill: `panels-worker-new-worker`.

Amend Worker changes an existing type in place. Amendment is user-owned, then Drafting and Consequences. The person states the amendment. The Worker drafts the change. Skill: `panels-worker-amend-worker`. Both default to Codex at medium.

## What feeds a type, and where it shows up

The shipped JSON seeds an empty database. After that, the database rows are the registry the server and the browser both read. Settings → Config lists each type, its stage ownership, its specialist skill, and its launch defaults. The Brief controls on a brand-new Ticket are the Ticket’s own copy of those defaults.

A specialist skill is what this kind of Worker reads on top of the shared Worker skill. How those files stay authoritative is [Skills](skills.md). `panels skill check` reports a skill row that names a command, stage, or skill this build does not have. It does not rewrite the row.

Adding a type is a product decision: a new ladder, a specialist skill, and one registration the server already knows how to serve. The browser does not grow a special case per type. It renders whatever ladder the manifest sends.

## Pointers

`src/planner/worker_types/shipped_worker_types.json` is the seed. `src/planner/worker_types/registry.py` is the registry the process serves. Settings UI is `web/src/routes/ConfigRoute.svelte`.

_Last verified: 2026-10-31._
