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
  `field_is_passed`, `at_or_beyond_ceiling`, `has_pending_gating_proposal` are all pure index
  math over those tables. Feed them a *type's* tables instead of the globals and every logic
  body is unchanged. The single-door invariant (workers propose, `_apply_decision` is the
  only writer of real values) is preserved verbatim.

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

**Hermes config levers, in decreasing ease of per-worker use:**
- **Skill** — per worker child via `HERMES_TUI_SKILLS` (comma-joined list). Varies cleanly per
  type today. This is the primary behavior shaper.
- **Model + reasoning effort** — Hermes supports a per-session model override
  (`SessionEntry.model_override`, `set_model_override`, `_apply_session_model_override`); a type
  can pin its own model. (Planner's `session.create` path doesn't pass it yet — wiring, not a gap.)
- **Tools/plugins** — scoped per Hermes **home** (and per platform), not per session. Restricting
  a type's toolset (e.g. read-only explorer) is the one lever that isn't free per-session.

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

**Break points found (all thread-throughs, none architectural):**
1. `fields_codec` → deserialize per-type field set.
2. DB `CHECK` on `state`/`ceiling` → relax to app-layer validation via the registry.
3. `fields` JSON default on create → from the type, not a literal.
4. CLI/UI stage & field lists → from the type.
5. Any state-columned board view → must handle mixed types (a real UI question, downstream).
6. `plan_handoff_status` → becomes a per-type hook (no-op for types without those stages).

The runtime spine, resolution engine, single-door invariant, scope model, readiness, events,
chat, and worker-context are untouched in every case.

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

_Not verified against a build; this is a design recommendation. Next proof, if approved: mock one
non-coding type end-to-end through the registry before any core code moves._
