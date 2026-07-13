# BRIEF — Job B: the `new_worker` ticket type (the create-a-worker worker)

## What this is
The payoff of the type machinery: a real, **production-shipped** ticket type, `new_worker`,
whose worker's job is to design and land ANOTHER worker. It runs a bespoke thinking-scaffold
lifecycle (deliberately NOT coding's), and because the stack is fully type-driven
(t_tt02–t_tt05) it needs no engine/UI change — only a new definition, a new specialist skill,
registration, and tests.

Owner framing (binding): the hard part of authoring a worker is the **thinking**; a
thin-prompted "write me a skill" is exactly the failure mode. So the lifecycle IS the thinking
scaffold — each stage a distinct thinking beat the human reviews before it proceeds.

## The locked lifecycle (owner)
`kickoff → needs_stages → needs_thinking → needs_drafting → needs_closeout → done`
(`dropped` = reserved exceptional terminal). ONE thinking stage. Each stage is gated by a
same-named field the agent proposes and the human approves — that gate IS the per-step review.

Stages / fields:
- `needs_kickoff` / `kickoff` — reuse (universal prefix). The ask: "I want a worker that does X."
- `needs_stages` / `stages` — **NOVEL**. The agent proposes the NEW worker's lifecycle: best
  guess + alternatives + stages it deliberately included or excluded, with rationale. For review.
- `needs_thinking` / `thinking` — **NOVEL**. The design substance: what makes a good deliverable
  ("good PR") for this kind of work; who the typical implementer is (human vs agent — this feeds
  the new type's default implementer / transition hooks); the standards the new skill must enforce.
- `needs_drafting` / `drafting` — **NOVEL**. Write the real artifacts: the new worker's `SKILL.md`
  and its `ticket_types` definition.
- `needs_closeout` / `closeout` — reuse. Land it: files in place, registered; a restart activates.
- `done`, `dropped` — reuse terminals.

Ceiling range: `needs_kickoff → needs_stages → needs_thinking → needs_drafting → needs_closeout → done`
(the full linear order — the kickoff-ceiling decoupling made `needs_kickoff` a selectable ceiling).
Default ceiling: `needs_kickoff` (global — a fresh ticket starts scoped to kickoff). The type's FIRST
WORKER stage is `needs_stages`, a distinct concept the recap gate / sprint-in-progress key off.

## Ships to PRODUCTION (not test-only)
- `NEW_WORKER_DEFINITION` goes into `coding_registry()` — `build_registry([CODING_DEFINITION,
  NEW_WORKER_DEFINITION], …)`.
- Its specialist skill is added to `coding_bridge._KNOWN_SKILLS` and to
  `minds/config.PLANNER_SKILL_NAMES` (provisioned into the worker Hermes home).
- (Contrast `probe`, which stays test-only in `build_probe_registry()`.)

## The specialist skill — the substance
A NEW skill (name TBD; convention → `panels-worker-new-worker`) that guides an agent through the
four thinking beats above. **Abstracted from coding**: lift the general patterns from
`skills/panels-worker-coding` + `skills/panels-worker` (how to think about stages, what a good
deliverable is, one-step-at-a-time discipline, keeping proposal shapes predictable) and DROP the
coding-specifics (git, PRs, code review). The base `panels-worker` skill stays preloaded and
type-agnostic; this specialist is `skill_view`'d when a worker runs a `new_worker` ticket.
Its `needs_closeout` guidance IS the mechanical "how to add a worker" recipe (the one the
docs-worker is writing) — one recipe, two consumers.

## Files (implement against — nothing outside these)
- NEW `src/planner/ticket_types/new_worker.py` — the `WorkflowDefinition`. Novel stage/field ids
  are plain strings declared directly (like `tests/support/probe.py`), NOT sourced from the
  `TicketState`/`FieldName` enums (they are not members). Reuse the enum string values for the
  shared ids (`needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, `done`, `dropped`).
- `src/planner/tickets/logic/coding_bridge.py` — add the specialist skill to `_KNOWN_SKILLS`;
  register `NEW_WORKER_DEFINITION` in `coding_registry()`.
- `src/planner/minds/config.py` — `PLANNER_SKILL_NAMES` += the new skill dir.
- NEW `skills/<specialist>/SKILL.md` — the thinking scaffold.
- Tests: registry coverage/golden for `new_worker`; a persistence test (a `new_worker` ticket
  round-trips its novel states/fields through the DB/engine); the structural invariant (every
  specialist skill shipped + provisioned) — confirm it now covers `new_worker`; a manifest check.

## Seams to VERIFY (do NOT assume zero-change — prove each in the plan)
- `copy_text` / any stage-keyed UI copy: do the novel fields (`stages`/`thinking`/`drafting`)
  render sensible text, or is there coding-keyed copy that goes blank/wrong for a foreign field?
- the employee step-runner prompt: is it field-generic (reads the field id from the definition),
  so a `new_worker` step reads sensibly? (Unproven live — connects to the standing self-routing
  caveat; a live worker step on a novel field has not been observed.)
- manifest + frontend: novel stages render on the board/ticket route (should, per t_tt04a/04b) —
  confirm, don't assume.

## Acceptance
- `./verify` green with `new_worker` in the production registry.
- A `new_worker` ticket can be created and driven stage-by-stage (propose → approve → advance)
  through the same gate machinery — persistence + engine test.
- The specialist skill is shipped and provisioned (structural invariant passes).
- Codex plan review and diff review report no violations.

## Deliverable staging (owner-in-the-loop)
1. **DESIGN SENSE-CHECK (first)**: the definition spec + the FULL skill draft (especially the
   `needs_thinking` stage) + the file/test list + the seam-verification findings. Owner and
   orchestrator drive. STOP — no file edits until go-ahead.
2. After go: implement to the driven design; Codex diff review; `./verify`; orchestrator
   integrates serially.
