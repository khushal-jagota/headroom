# Ticket t_tt05 — Worker realization: activate the declared worker profile (skill-driven, no wiring)

Phase 5 — the last piece of the machinery. Make a worker actually work a **non-coding** type by having the
agent **self-route to the right specialist skill**, driven by the type's already-declared (but inert)
`WorkerProfile`. This is deliberately **skill-driven, not code-routed** (owner ruling this session): the
"routing" is the agent reading its ticket's type and loading the matching specialist skill — NOT the
gateway/session/toolset wiring the old PLAN.md 5b proposed. Drop that wiring.

Depends on: t_tt00–t_tt04 (registry with `WorkerProfile`, `coding_bridge`, manifest, type-driven ingress +
read-models). Production stays coding-only; the second type is exercised by the test fixture (`probe`).

## What already exists (do not rebuild)
- `WorkerProfile { specialist_skill, model, toolset_profile }` is **declared per type and validated**
  (`contracts.py` — "declared, INERT this ticket; validated for reference only"). Coding's
  `specialist_skill="panels-worker"`, `toolset_profile="default"`. `probe`'s references a placeholder skill.
  `coding_bridge` injects `_KNOWN_SKILLS={"panels-worker"}` as the validated catalog.
- Workers launch with `HERMES_TUI_SKILLS = <base worker role>` (`shared_gateway.py:644`), and
  `config.PLANNER_SKILL_NAMES` symlinks repo `skills/` into the worker's Hermes home
  (`provision_planner_home_skills`). `panels worker my-ticket` already reports "who you are + current state".
- `skills/panels-worker/SKILL.md` is the base worker skill — **but it HARDCODES coding's stages**
  (Success→Approach→Plan→Implementation→Closeout) in its text. That is the thing to generalize.

## Scope — activate the profile

**In:**
- **Base skill → type-agnostic.** Rewrite `skills/panels-worker/SKILL.md` to describe the *mechanism*, not
  coding's stages: you work one step at a time; run `panels worker my-ticket` to learn your ticket's **type**
  and your **specialist skill**; **load that specialist** and follow it for the stage-specific work. Keep the
  type-agnostic disciplines that already live here (never approve your own proposal; implementer-assignment
  routing; the propose/recap/note CLI; "never invoke panels chief"). Remove the coding stage catalogue.
- **Coding specialist skill.** New `skills/panels-worker-coding/SKILL.md` — the coding stage guidance moved
  out of the base skill (needs_success…needs_closeout: what each stage needs, the deep disciplines). Point
  coding's `WorkerProfile.specialist_skill` at it (`coding.py`), and update `coding_bridge._KNOWN_SKILLS` so
  validation accepts it. (The base skill name loaded via `HERMES_TUI_SKILLS` is unchanged; only the
  *specialist* the agent loads on demand changes.)
- **Placeholder specialist for the test type.** A minimal specialist skill the `probe` profile references —
  enough to prove the agent loads the right one for a non-coding type. Keep it a placeholder (thin stage
  guidance), test-scoped where possible.
- **Provisioning.** Add the new specialist skill(s) to `config.PLANNER_SKILL_NAMES` so they symlink into the
  worker home alongside the base skill.
- **`panels worker my-ticket` surfaces the profile.** Its output includes the ticket's **type** and its
  **`worker_profile.specialist_skill`** (resolved via `coding_bridge`/registry from the ticket's type) plus
  the one-line instruction to load that specialist. This is the agent's self-routing cue — the ONLY code
  touch of substance.
- **The mechanism detail the plan must resolve:** exactly HOW the worker accesses the specialist given
  `HERMES_TUI_SKILLS` — does it INVOKE it as a Hermes skill (if HERMES_TUI_SKILLS doesn't gate invocation of
  home skills), or READ `skills/panels-worker-<type>/SKILL.md` as a file (always works — it's symlinked in
  the home)? The base skill's instruction wording follows from this. Investigate Hermes skill semantics; pick
  the one that reliably works and state it.

**Out:** gateway/session/runtime/human-chat routing; per-session model/effort; per-type toolset children /
`HERMES_TUI_TOOLSETS` (the old 5b — CUT; a specialist shapes behavior, not tools/model, for now — revisit
only if a real type needs genuinely different tools). The real self-service worker-creator (Job B). Removing
the throwaway type / writing the how-to-add-a-worker docs (the wrap-up, after Phase 5).

## Acceptance (what CAN be verified deterministically)
- `panels worker my-ticket <id>` output includes the ticket's `ticket_type` and its
  `worker_profile.specialist_skill` and the load-the-specialist instruction (asserted, both a coding ticket
  and a `probe` ticket → their respective specialists).
- **Structural invariant test:** for every registered type, `worker_profile.specialist_skill` resolves to a
  skill directory that (a) exists under `skills/` and (b) is in `PLANNER_SKILL_NAMES` (provisioned). This
  ties the registry to the on-disk skills so a type can't declare a specialist that isn't shipped.
- The base `panels-worker` skill no longer hard-codes the coding stage list (asserted: the coding-specific
  stage names live in `panels-worker-coding`, not the base).
- `coding_bridge._KNOWN_SKILLS` accepts coding's new specialist; the registry still validates; production
  registry coding-only.
- `./verify` green.

## The one soft spot (honest, out-of-band)
The **agentic proof** — a live worker actually reading its type and following the correct specialist — is
NOT a `./verify` gate; it is proven by *running a real Hermes worker* against a ticket (non-deterministic,
needs the gateway + a model). Attempt an automated worker smoke (via `minds/smoke.py` or the gateway) if it's
cleanly doable and record the transcript; otherwise leave this single end-to-end demonstration for the owner
to run, and say so plainly. Do NOT report Phase 5 "green" on the strength of the code/structure tests alone —
distinguish "mechanism built + code-verified" from "self-routing observed live".

## References
- `src/planner/ticket_types/contracts.py` (`WorkerProfile`), `coding.py` (coding's profile),
  `src/planner/tickets/logic/coding_bridge.py` (`_KNOWN_SKILLS`), `tests/support/probe.py` (probe profile).
- `src/planner/minds/config.py` (`PLANNER_SKILL_NAMES`, `provision_planner_home_skills`),
  `shared_gateway.py` (`HERMES_TUI_SKILLS`), the `panels worker my-ticket` command (cli/`worker`).
- `skills/panels-worker/SKILL.md` (base, to split). Owner rulings: skill-driven routing, no wiring
  (this session); v2 = v1 + gates; lean on Hermes skills not code.
