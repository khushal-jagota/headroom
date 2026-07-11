# Ticket types — recommendation

_Making stages a property of a ticket **type**, not a hardwired universal, so different
kinds of work run through different stages, gates, and workers — as a first-class
extension of the current system, not a rewrite._

This is the durable write-up behind the thin recommendation. It records what the current
system actually is, the shape I recommend, the stress tests I ran against it, and the
genuine open decisions. Owner has not approved anything here yet.

---

## 1. The current reality (what "stages are welded to tickets" means precisely)

A ticket today has **one** fixed lifecycle and **one** worker skill:

```
success → approach → plan → implementation → closeout → done      (+ dropped)
```

Two facts from the code shape the whole recommendation:

**(a) A ticket already has two independent axes.**
- `ticket_status` — the *control* axis: who is driving right now
  (`empty` / `agent_running_step` / `awaiting_approval` / `user_takeover` / `errored`).
  The runtime, the Hermes gateway, readiness discovery, chat, and the event log all key
  off this axis. **It is type-agnostic.**
- `state` — the *workflow* axis: which stage the work is at (`needs_success` … `done`).
  **This is the only type-specific axis.**

  → So making stages per-type touches the `state` axis and leaves the entire runtime/
  gateway/chat/events spine untouched. This is the seam.

**(b) The resolution engine is already generic.** Every function in the state machine
(`tickets/logic/machine.py`) takes `state`/`ceiling`/`field` as arguments and only reaches
into a handful of module-level tables:
- `STATE_ORDER` (the ordered stages), `GATING_FIELD` (stage → the field it gates),
  `ADVANCE_TARGET` (stage → next stage), `FIELD_GATES` (inverse), `FieldName` (the 5 fields).

  → `state_index`, `advance_target`, `gating_field`, `auto_accept_target`, `resolve_scope`,
  `field_is_passed`, `at_or_beyond_ceiling`, `has_pending_gating_proposal` are pure index math over
  those tables — the reusable **kernel**. Feed them a *type's* tables instead of the globals and the
  kernel bodies are unchanged; the single-door invariant (workers propose, `_apply_decision` is the
  only writer of real values) is preserved. **But the refactor is contained, not zero** — terminal
  detection, `resolve_scope`, admission (`recap writable past needs_success`), and several
  `resolution` rules still name the coding stages by literal and get generalized to read the type.
  The honest blast radius is §7 (folded in from the gpt-5.6-sol review); "correctness heart doesn't
  move" was too strong.

**Where the fixed lifecycle is actually hardcoded** (the real change surface):
- `tickets/contracts.py` — `TicketState`, `STATE_ORDER`, `FieldName`, `GATING_FIELD`,
  `ADVANCE_TARGET`, `TicketFields` (module-level constants).
- `tickets/logic/machine.py` — imports and indexes those constants (bodies are generic).
- `tickets/logic/fields_codec.py` — deserializes the `fields` JSON expecting the 5 keys.
- `core/db.py` — `CHECK (state IN (…))` and `CHECK (ceiling IN (…))` enumerate the 7/6 values;
  **but `fields` is a single JSON blob column, so different field-sets cost nothing to store.**
- `cli/main.py` — `_FIELDS`, `_TICKET_SET_FIELDS` constants; `ticket create/approve` assume the set.
- `web/src/lib/ui.ts` + `TicketRoute.svelte` — frontend mirrors of the same constants.
- `machine.plan_handoff_status` — the one place with a hardcoded stage-pair
  (`needs_plan → needs_implementation` ⇒ `khushal` takeover).

**What is already type-agnostic and does NOT change:** the resolution engine flow, readiness
(`runtime/readiness.py`), admission checks, scope/ceiling math, the event kinds and the
event→resource invalidation (keyed by entity-id prefix, not by field name — a new stage adds
no event kind), the chat/worker-context spine, `_apply_decision`, the `_txn` wrapper.

---

## 2. The current worker/Hermes reality (the config surface)

- The planner runs **one** Hermes home (`data/hermes-home`) with a rich `config.yaml`
  (model, `reasoning_effort`, toolsets/`disabled_toolsets`, `platform_toolsets`,
  `skills.external_dirs`, `plugins.enabled`, per-purpose model overrides).
- It builds **one worker gateway + one chief gateway** (`core/server.py:_build_role_gateways`).
  `EntityRoutingGateway` already routes per-entity: the Chief entity → chief gateway, every
  ticket → the shared worker gateway. **Routing infrastructure exists; it has one entry.**
- A `SharedGateway` carries a `worker_role` (→ `HERMES_TUI_SKILLS` env var, which force-loads
  that skill for the child), a `base_env`, and a `worker_context`. Different gateways already
  mean different skills + env.
- The `implementer` field (`khushal` / `panels_worker` / `hermes_codex` / `hermes_claude`)
  already exists per ticket but currently only drives the plan→implementation takeover; it does
  **not** route to different gateways/skills/models yet.

**Hermes config levers — all scope per worker child:**
- **Skill** — per worker child via `HERMES_TUI_SKILLS` (comma-joined list). Varies cleanly per
  type today. This is the primary behavior shaper.
- **Model + reasoning effort** — Hermes accepts model/provider/effort on `session.create` and
  persists a per-session model override; a type can pin its own model. (Planner's `session.create`
  doesn't pass it yet — wiring, not a gap.)
- **Tools** — a worker child sets its own toolset via `HERMES_TUI_TOOLSETS` (per-child, like
  `HERMES_TUI_SKILLS`; `tui_gateway/server.py:2610`). Plugin *definitions* stay home-level, but tool
  *availability* is per-child. (An earlier draft called toolsets home-global — that was **wrong**,
  corrected by the gpt-5.6-sol review; see §7. A skill instruction is not tool enforcement — a
  genuinely read-only explorer needs a real restricted toolset, which this gives.)

---

## 3. The recommendation: a ticket **type** that bundles a workflow and a worker

Make `type` a first-class property of a ticket. A type is a small **declaration** with two parts:

- **Workflow** — ordered stages, the field each stage gates, the field set, the initial state,
  and the terminal state(s).
- **Worker** — the skill that runs it, its model (+ effort), and its tool scope.

`coding` is the current lifecycle, unchanged. `exploration`, `marketing`, `debugging` are other
types with their own stages and their own worker. Types live in a **registry in code** — core
holds zero per-type `if`s; adding a type is a folder + one registration line. This is literally
the standing **core/module contract** in `PRINCIPLES.md`.

### How it slots in — five touch points

1. **Type registry (code).** Each type declares `{stages, gates, fields}` + `{skill, model, tools}`
   and registers once. The current lifecycle becomes the built-in `coding` type.
2. **Ticket gets a `type` column.** `state`/`ceiling` become plain strings validated by the type's
   registry instead of a fixed DB `CHECK` list. `fields` is already a JSON blob → heterogeneous
   field-sets need no schema change. Migration: every existing ticket → `coding`.
3. **Machine reads the type's tables.** Thread the type's workflow into the machine functions in
   place of the imported constants. Resolution / scope / gates / readiness bodies unchanged.
4. **Workers route by type.** One worker gateway per type, each carrying its skill/model/tools;
   route a ticket to its type's gateway (the per-entity routing already exists — populate it).
5. **CLI + UI read stages from the type.** `ticket create --type …`; field/stage lists come from
   the type, not constants.

### Why this is good
- **Extension, not rewrite.** The correctness heart (resolution engine, single door, scope/gates,
  readiness) is preserved verbatim; only *where the stage tables come from* changes.
- **Matches your own principle.** Core/module registry, zero core conditionals, one-line to add.
- **DB stays one simple table.** No normalization into `ticket_kinds`/`ticket_stages` tables.
- **The worker seam already exists.** Per-entity gateway routing + per-child skill selection are
  built; they're currently populated with a single entry.

---

## 4. How others have solved it (precedent)

This is the **type-discriminator + per-type definition in a registry, generic engine** pattern.
- **Jira** is the closest real-world analog: an *issue type* → a *workflow scheme* (its own
  statuses + transitions) + a *screen scheme* (its own fields per operation). Directly the shape
  here. **But** Jira normalizes types into DB tables because end users configure workflows live in
  a multi-tenant product. We define types in code, so a code registry is lighter and truer — I
  deliberately do **not** copy the DB normalization.
- **Statecharts / XState** — the CS version: a generic interpreter over a workflow that is *data*.
- **STI / type-discriminator column** (Rails/Django) — the DB version: one table, a `type` column,
  per-type behavior in code.
- **Temporal / Camunda / Step Functions** — heavier workflow-as-data engines built for durable
  distributed execution; over-built for a single-user local planner, but the same core idea.

---

## 5. Stress tests (mocking types through the shape)

**`exploration`** — stages `question → findings → done`; fields `{question, findings}`; worker =
explorer skill, larger reasoning model, read-only tools.
- Machine: `STATE_ORDER=[needs_question, needs_findings, done]`, gates/advance map defined by the
  type → all machine math works. ✓
- Scope/ceiling: "approved until `needs_findings` then propose" — the ceiling picker reads *this
  type's* stages. ✓
- Readiness / approval / events: unchanged (generic). ✓
- **Break found:** `fields_codec` must deserialize the *type's* declared fields, not a fixed 5.
  (Cheap — the blob already holds arbitrary JSON.)

**`marketing`** (`brief → draft → review → done`) and **`debugging`**
(`reproduce → diagnose → fix → verify → done`) generalize identically. ✓

**Break points found** (§7 corrects "none architectural" — a few are more than thread-throughs):
1. `fields_codec` → deserialize per-type field set (becomes a validated mapping, not a fixed 5).
2. DB `CHECK` on `state`/`ceiling` → relax to registry/app-layer validation (with integrity replacement, §7).
3. `fields` JSON default on create → from the type, not a literal.
4. CLI/UI stage & field lists → from the type (one serialized manifest, not a parallel TS registry).
5. State-columned board view → cannot consume a heterogeneous state union as shaped (§7, real).
6. `plan_handoff_status` → a per-type transition-effect hook.
7. Universal terminals + Chief external-work → bigger than thread-throughs; see §7.

---

## 6. Genuine open decisions (my pick on each)

- **Type vs. the existing `implementer`.** Keep orthogonal: *type* picks the workflow + default
  worker; *implementer* stays the "a human takes over instead" override. Correlated, so they
  *could* fold into one — but they answer two different questions ("what kind of work" vs "who runs
  this instance").
- **Definitions in code vs. data.** Code registry. Only revisit for runtime-defined types w/o deploy.
- **Shared vs. per-type toolset.** Share for v1 (differentiate by skill + model); add a per-type
  Hermes home only if a type needs a genuinely locked-down toolset.

---

## 7. Independent review (gpt-5.6-sol, high, read-only) — folded in

**Verdict:** approve the direction (type discriminator + code registry + generic *linear* workflow
interpreter), but **not** the "clean extension, correctness heart untouched" rationale — that
overstated how localized the change is. Sharper framing: *introduce a code-registered, strictly
linear ticket-workflow module; persist a stable `ticket_type`; preserve universal `done`/`dropped`
and the generic control-status vocabulary; refactor contracts, codecs, ingress validation, read
models, and routing to consume the registered workflow; serve one serialized manifest to UI/CLI;
replace lost DB checks with registry validation + startup auditing.*

**Corrections to my claims:**
- **Toolsets are per-child, not home-global** (`HERMES_TUI_TOOLSETS`). My §2 error, fixed above.
- **The refactor is contained but not zero.** `is_terminal`, `resolve_scope`, admission, and several
  `resolution` rules name the coding stages literally; `FIELD_GATES` is a second lifecycle table;
  contracts are closed enums + a fixed 5-field dataclass. Signatures/types/validation change.
- **The two axes are separate in storage but coupled in operation** — filing/accepting a proposal
  *derives* control status from workflow states. Say "separate persisted axes with explicit
  transition coupling," not "independent." The reusable part is the control vocabulary + the
  transport/claim/session skeleton.
- **Fields aren't free:** `TicketFields` must become a validated mapping; codec/contract/API/views/
  defaults/corruption rules all change (today's codec has an unsafe catch-all returning `closeout`).

**Ranked constraints (folded into the design):**
1. **[Critical] Universal terminals.** Other domains read exact state strings — blockers/links
   (`state NOT IN ('done','dropped')`), sprint-item completion, approval/overdue queues. A type that
   ends at `complete` would block forever and stall its sprint item. **v1: every type ends in the
   universal `done`; `dropped` stays the universal exceptional terminal. Types vary only nonterminal
   stages + fields.** (My mockups already ended each type at `done` — now it's a stated invariant.)
2. **[Critical] Chief external-work is a first-class workflow interface.** `_FIELD_ORDER`/
   `_PREFIX_COUNT` independently encode the lifecycle; create must pick `ticket_type` before parsing
   state/fields; the settled-prefix derives from the type's ordered gates; a type may declare it
   doesn't support prefix reconciliation.
3. **[High] Board.** `board_view` builds a global `STATE_ORDER` map; `by_state[state]` fails on an
   unknown state. Cards should carry `ticket_type` + state id/label + gating field/label +
   is_done/is_dropped + control signals; keep **project** and **`ticket_status`** as the common axes;
   don't synthesize a global column taxonomy from unrelated workflows.
4. **[High] Scope = one registry source, two contextual slices.** Serialize the type's stage order
   once; header floor = current state onward, approval floor = newly-entered state onward + `none`.
   No parallel TS lifecycle.
5. **[High] Registry = a constrained linear-workflow deep module.** Validates at startup: id
   uniqueness, complete gate coverage, one successor per nonterminal stage, field refs, terminal
   placement, worker-profile refs. Rejects branching/repeated gates/ambiguous terminals. (My
   "terminal state(s)" over-promised; the engine is strictly linear.)
6. **[High] DB CHECK relaxation needs integrity replacement:** validate type/state∈type/ceiling∈type/
   fields-match on row-load and before-persist, plus a startup table audit. Keep cheap
   type-independent checks (`at_cap`, `ticket_status`, priority, non-empty).
7. **[High] Migration is more than "add a column."** SQLite needs a table rebuild to drop CHECKs;
   the ticket DDL is duplicated in 3 places (canonical + two rebuild templates) and migration order
   matters — a later template must not recreate old CHECKs/5-field default. Use the existing
   atomic-swap/FK-check/rollback discipline; backfill every row as `coding` in the same migration.
8. **[Medium] `plan_handoff_status`** → a named transition-effect hook the `coding` module declares
   (plan→implementation + human override ⇒ `user_takeover`); core invokes the interface with no type
   `if`. Decide whether direct jumps / external reconciliation should also fire it (today they bypass).
9. **[Medium] `ticket_type` immutable in v1** — a durable Hermes session already exists per ticket;
   changing type mid-life raises transcript/skill/model/field-conversion questions nobody needs yet.
10. **Name it `ticket_type`, not `type`** (project naming rule; `type` is ambiguous).

**Forks — all three of my picks upheld, with one sharpening:**
- Type vs `implementer`: keep separate — **but** the current `Implementer` enum is muddled (a person,
  a generic worker, two model-flavoured identities; only `khushal` has any effect). Redesign it as a
  clean instance-level execution override: `None` = type's default worker; human = takeover at the
  type's declared handoff; agent-profile = actually select another registered worker profile. Remove
  or rename values that won't route.
- Code registry over DB: strongly upheld (DB workflows buy runtime-editing nobody asked for + a
  config UI + live-versioning). But "code registry" = one backend authority + a serialized manifest,
  **not** parallel Python and TS constants. Changing a registered workflow is a data migration and
  must fail startup validation, not silently reinterpret old rows.
- Toolset: per-profile is viable in v1 within one shared home (`HERMES_TUI_TOOLSETS`); a skill
  instruction is not tool enforcement.

## 8. Worker realization — base skill + linked specialists (owner refinement)

Rather than one gateway child per type, realize worker differentiation mostly in the **skill layer**:
- **One base worker skill** (`panels-worker`) carries everything every worker needs — orientation,
  the propose-only contract, the CLI, gates/scope, artifacts. Authored once; no duplication.
- **Per-type specialist skills** (`…-explorer`, `…-marketer`, `…-debugger`) *link to* the base and add
  only the type's craft. All specialists are provisioned into the one home (existing symlink path).
- The employee runner's prompt already states the ticket's type/state; the base skill routes the
  worker to the matching specialist for that ticket. → **behavior + skill + model differentiate inside
  one shared gateway child** (model varies per session; skills are all home-available and selected by
  base-skill + prompt).
- Gateway children fork **only** where an OS-level capability must differ — i.e. a restricted
  **toolset** (read-only explorer). So the realistic shape is 1–2 children keyed by *toolset profile*,
  not one per type. The registry's worker-profile per type = `{specialist skill, model, toolset-profile}`;
  the toolset-profile is the only thing that maps to a distinct child.

This removes the duplicated "things every worker needs to know," keeps the code thin (lean on skills),
and shrinks the "workers route by type" touch point to: type → toolset-profile → child (few) + type →
specialist skill (in prompt).

---

_Not verified against a build; a reviewed design recommendation (gpt-5.6-sol folded in). Next proof,
if approved: mock one non-coding type end-to-end through the registry — proving universal-terminal
handling, external-work prefix from the type's gates, and base+specialist skill routing — before any
core code moves._
