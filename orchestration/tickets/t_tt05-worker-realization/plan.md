# Implementation Plan — t_tt05: Worker realization (skill-driven self-routing)

## 0. Mechanism finding (the load-bearing question) — resolved: INVOKE via `skill_view`

**Question:** given `HERMES_TUI_SKILLS=panels-worker`, how does the worker agent reach a *specialist* skill
(`panels-worker-coding`) symlinked into its home but not in its launched role?

**Finding (with evidence):**
1. `HERMES_TUI_SKILLS` only controls session-wide **preloading** of skill text into the system prompt
   (`tui_gateway/server.py:4103 _parse_tui_skills_env` → `agent/skill_commands.py build_preloaded_skills_prompt`).
   It does NOT gate which skills the agent may later load. (`shared_gateway.py:644` sets it to
   `self._worker_role` = `"panels-worker"`.)
2. On-demand skill access is a **core tool**: `skill_view(name)` (`tools/skills_tool.py`) loads any skill by
   bare name from `HERMES_HOME/skills` — exactly where `provision_planner_home_skills` symlinks our skills.
   It is in `_HERMES_CORE_TOOLS` (`toolsets.py:45`) and the `coding` posture set, so it is present under every
   worker posture. The planner sets no `HERMES_TUI_TOOLSETS`, so the worker resolves toolsets via Hermes'
   default/coding path — all include the skills tools.

**Decision:** the base skill instructs the worker to **invoke the specialist with `skill_view("<name>")`**.
Native, no wiring.

**Precondition (Codex F1):** `skill_view` must be in the worker's RESOLVED toolset. The current `hermes-cli`
config includes it and the planner sets NO `HERMES_TUI_TOOLSETS`, so the default worker has it — but Hermes
gives `HERMES_TUI_TOOLSETS` precedence, and a restricted set like `safe` omits `skill_view`. This is an
operational **precondition to document** (do not restrict the worker's toolset below one that includes
`skill_view`), NOT gateway wiring; the live self-routing run (§8) is where it's actually confirmed.

**Fallback (Codex F4):** do NOT word a bare relative `skills/<name>/SKILL.md` fallback — Hermes resolves
relative reads against the process CWD, not `HERMES_HOME`, so it would miss the provisioned link. Rely on
`skill_view` (works under the default config); omit the fragile relative-path fallback from the base skill.

---

## 1. `skills/panels-worker/SKILL.md` — EXTRACT-AND-PLACE, do NOT rewrite (owner constraint)

**Hard constraint (owner):** do not reword, relocate, or add wording to the base skill beyond the ONE new
section on invoking the specialist. The coding-specific sections are well-structured enough to be cut
verbatim and pasted into the specialist. The agent does not need to know who/why/whether something is
approved — approval is the system's business, invisible to the worker — so no approval-mechanism text.

**Three surgical changes, nothing else touched:**

1. **Extract `### The stages` catalogue (lines 20–31) → the coding specialist, VERBATIM.** These are the
   coding stage names (`Success → Approach → Plan → Implementation → Closeout → Done` + the per-state
   bullets). Cut them out of the base.
2. **Line 32 → exactly `you handle the one current step only.`** (owner's exact words). The rest of that
   sentence (the "Implementation does not fold Closeout into itself" clause AND the "you never approve your
   own proposal … approval always comes from the human" clause) is **dropped** — not reworded, not moved.
   This is the ONLY place a base sentence changes, and it's a deletion to the owner-specified words.
3. **Extract `### How to complete ticket stages effectively` (lines 81–92) → the coding specialist, VERBATIM.**
   (The per-stage `needs_success…needs_closeout` guidance.)

**Add exactly ONE new section** (the only new wording in the base) — place it where `### The stages` was, so
the "how a ticket moves" flow reads naturally. Terse; it also LISTS the specialists (owner: the base skill
gives the specialist skills themselves):

```
## Your ticket and your specialist
Which stages a ticket has, and what each needs, depend on its TYPE. Run
`panels worker my-ticket` — it names your worker skill. Invoke that skill with
`skill_view("<name>")` and follow it for the stage-by-stage work.
you handle the one current step only.

Worker skills:
- `panels-worker-coding` — coding tickets.
- `probe-worker` — the probe fixture type (test genericity proof).
```

**Everything else in the base stays EXACTLY as written** — `# Working a ticket` opener, `## The system`,
`### The CLI` (incl. "Never invoke `panels chief`."), `### Implementer assignment`, all of
`### Cross-cutting disciplines` (including the "Keep proposal shapes predictable" bullet, which names the
coding stages — it stays verbatim, no rewrite), `### Ticket-owned planning artifacts`. No other edits.

> The specialist LIST in the base is a thing you update when adding a worker — part of the (future)
> how-to-add-a-worker story. Fine for now.

---

## 2. `skills/panels-worker-coding/SKILL.md` — NEW: the extracted coding sections, VERBATIM

A real skill dir (provisioned in §5). It holds the two blocks cut from the base, pasted UNCHANGED, under
minimal frontmatter:

```
---
name: panels-worker-coding
description: Stage-by-stage guidance for a coding-type Panels ticket.
---
```
then, verbatim: the `### The stages` catalogue (base lines 20–31, dropping only the extracted line 32 per §1)
and the `### How to complete ticket stages effectively` block (base lines 81–92). No reworded text. (A one-
line heading like `# Coding ticket stages` may top the pasted catalogue for readability — that is a heading,
not a reword of the content.)

Type-agnostic content (CLI, implementer routing, cross-cutting disciplines, artifacts) is NOT duplicated
here — the worker loads both (base preloaded + specialist on demand).

---

## 3. `coding.py` + `coding_bridge.py` — point the profile at the new specialist; let validation accept it

- **`ticket_types/coding.py`**: `_WORKER_PROFILE.specialist_skill` `"panels-worker"` → `"panels-worker-coding"`.
- **`tickets/logic/coding_bridge.py`**: `_KNOWN_SKILLS` gains `"panels-worker-coding"` so R14 validation
  passes at `build_registry`:
  `_KNOWN_SKILLS = frozenset({"panels-worker", "panels-worker-coding"})`. Keep `"panels-worker"` — it is the
  base role loaded via `HERMES_TUI_SKILLS` and a real shipped skill; the catalog is the set of skills the
  registry may reference.

**`_worker_role` / `config.worker_skill` UNCHANGED (subtlest risk).** The launched base role stays
`panels-worker` (`core/config.py worker_skill` default → `server.py worker_role` → `HERMES_TUI_SKILLS`). Only
the on-demand specialist becomes `panels-worker-coding`. Do NOT touch `worker_role`/`config.worker_skill`/its
default. Log in `decisions.md`.

---

## 4. Placeholder specialist for `probe` — ship a real thin `skills/probe-worker/`

Ship `skills/probe-worker/SKILL.md` as a real (tiny) dir, NOT test-only, because the structural-invariant
test (§7b) and `provision_planner_home_skills` (raises `FileNotFoundError` on a missing dir) both require the
specialist dir to exist under `skills/` and be in `PLANNER_SKILL_NAMES`. Minimal frontmatter + one short
paragraph of thin `needs_alpha`/`needs_beta` guidance, labeled a genericity-proof placeholder. Harmless in
production (only the test registry names it).

`tests/support/probe.py`: `PROBE_SPECIALIST_SKILL = "probe-worker"` already matches the dir. **Required
edit:** `PROBE_KNOWN_SKILLS` must gain `"panels-worker-coding"` (its `build_probe_registry` builds
`[CODING_DEFINITION, PROBE_DEFINITION]`, so it validates coding's specialist too):
`frozenset({"panels-worker", "panels-worker-coding", "probe-worker"})`.

---

## 5. `config.PLANNER_SKILL_NAMES` — provision the new specialists

`minds/config.py` — append so they symlink into the worker home:
```python
PLANNER_SKILL_NAMES = (
    "panels", "panels-worker",
    "panels-worker-coding",   # NEW
    "probe-worker",           # NEW
    "panels-chief-of-staff", "panels-sprint-planning", "panels-rollover",
)
```
Both must be real dirs under `skills/` (§2, §4) or provisioning raises at startup.

---

## 6. `panels worker my-ticket` — return the WORKER NAME (owner: just the name)

Server-side, in the by-session endpoint `tickets/api.py get_my_ticket` (the only place `coding_bridge` is
reachable; the CLI is a thin HTTP client). After building `detail`, resolve the ticket's worker name from its
type and add ONE field — the worker name, nothing more:
```python
defn = coding_bridge.require(ticket.ticket_type)
detail["worker"] = defn.worker_profile.specialist_skill   # e.g. "panels-worker-coding"
```
Keep this local to `get_my_ticket` (worker-only concern), not the shared `ticket_json`. `ticket_type` is
already present in the payload.

**CLI human line** (`cli/main.py worker_my_ticket`) — add the worker name plainly; no verbose instruction
(the base skill owns "invoke it"):
```
f"{data['id']} {data['state']} {data['priority']} {data['title']}\n"
f"worker: {data['worker']}"
```
`--json` returns the whole dict, so the new `worker` key rides along. Under the probe test registry a probe
ticket resolves to `probe-worker`; in production coding resolves to `panels-worker-coding` — the self-routing
cue, computed from the registry.

---

## 7. Tests (deterministic — the `./verify` gates)

**(a) `my-ticket` returns the right worker — coding AND probe.** Extend the existing by-session TestClient
module (`tests/unit/test_readiness_actions.py`) or a focused `test_worker_my_ticket.py`:
- coding ticket → `body["worker"] == "panels-worker-coding"`.
- probe ticket (wrap in `install_probe_registry()`/`uninstall_probe_registry()`) → `body["worker"] == "probe-worker"`.
- CLI-line: a `test_cli_entrypoints.py` case stubs the HTTP response and asserts the human string contains
  `worker: panels-worker-coding`.

**(b) Structural invariant — every registered type's specialist is shipped + provisioned + LOADABLE.**
`tests/unit/test_ticket_type_registry.py::test_every_specialist_skill_is_shipped_and_provisioned`: build
`build_probe_registry()` (covers coding + probe); for each `defn`, `s = defn.worker_profile.specialist_skill`;
assert `(repo_root()/"skills"/s/"SKILL.md").is_file()` (Codex F6 — a bare dir provisions fine but `skill_view`
needs `SKILL.md`, so check the file, not just `.is_dir()`) AND `s in config.PLANNER_SKILL_NAMES`. Ties the
registry to a loadable on-disk skill.

**(c) The base/specialist split — UPDATE the EXISTING assertion in `tests/unit/test_minds.py` (Codex F3).**
That test provisions the home and currently asserts `panels-worker`'s SKILL.md CONTAINS the arrow sequence,
the five fields, `needs_implementation`/`needs_closeout`, `reviewable`, the closeout duties (merge/deploy/
follow-up/bookkeeping), and `"never approve"` — exactly the content the extraction removes. Rework it:
- read `coding_worker = (skills/"panels-worker-coding"/"SKILL.md")`; move the coding-content assertions
  (arrow sequence, the five fields, `needs_implementation`/`needs_closeout`, `reviewable`, closeout duties)
  onto `coding_worker`.
- assert the BASE `panels-worker` NO LONGER contains the arrow sequence / `needs_implementation` /
  `needs_closeout`; assert it DOES contain the anchors: `panels worker my-ticket`, `skill_view`,
  `you handle the one current step only.`, and still `Never invoke ` + "panels chief".
- **DROP the `"never approve"` assertion** — that text is deleted per the owner ruling.
- leave the `panels` (planner-skill) assertions UNCHANGED — `panels/SKILL.md` is not touched here.
- **Narrow honestly (Codex F5):** the base still contains the "Keep proposal shapes predictable" bullet
  naming the coding stages (kept verbatim per the no-rewrite ruling), so the split assertions target the
  EXTRACTED content (arrow sequence, `needs_*` ids, the how-to-stages block), NOT "zero coding words in the
  base". Do not add a claim the base is fully type-agnostic.

**Catalog / golden updates (expected — every registry that includes `CODING_DEFINITION` needs the new skill):**
- `test_ticket_type_registry.py` `EXPECTED["worker_profile_id"]` → `"panels-worker-coding"` (manifest derives
  it from `specialist_skill`).
- `test_ticket_type_registry.py` local `KNOWN_SKILLS` → add `"panels-worker-coding"`.
- **`tests/unit/test_ticket_type_persistence.py` `_two_type_registry` `known_skills` → add
  `"panels-worker-coding"` (Codex F2 — its `CODING_PROBE_DEFINITION` inherits coding's profile, so R14 fails
  without it).**
- `tests/support/probe.py` `PROBE_KNOWN_SKILLS` → add `"panels-worker-coding"`.
- `test_external_work_generic.py` synthetic defs use their own `known_skills`; grep-confirm self-contained
  (no edit expected). Also grep the whole tree for any other `known_skills=`/`specialist_skill=`/asserted
  `"panels-worker"` before finishing — miss one and its registry build fails R14.

---

## 8. The agentic proof (out-of-band) — LIVE OWNER RUN, not automatable cleanly

The deterministic gates prove **mechanism built + code-verified** (my-ticket returns the right worker per
type; every specialist is shipped; the split happened). They do NOT prove **self-routing observed live** — a
real worker reading its type and actually loading the specialist. `minds/smoke.py` has no ticket/DB/session
binding; adding it is the very gateway/session wiring this ticket cuts. So: ship the gates green; state
plainly that the single end-to-end demonstration (a live worker on a coding — and ideally probe — ticket,
observed loading its specialist) is a **live owner run** (`panels serve` + a seeded ticket + `panels worker
my-ticket` + a model), captured as a transcript. Report Phase 5 as **"mechanism built + code-verified; self-
routing not yet observed live"** — not "green".

---

## 9. Files touched (ordered)
1. `skills/panels-worker-coding/SKILL.md` — new (create the move target first).
2. `skills/probe-worker/SKILL.md` — new (placeholder).
3. `skills/panels-worker/SKILL.md` — extract two coding sections; line 32 → `you handle the one current step only.`; add the one specialist section. No other edits.
4. `src/planner/ticket_types/coding.py` — `specialist_skill` → `"panels-worker-coding"`.
5. `src/planner/tickets/logic/coding_bridge.py` — `_KNOWN_SKILLS` gains it.
6. `src/planner/minds/config.py` — `PLANNER_SKILL_NAMES` gains both dirs.
7. `src/planner/tickets/api.py` — `get_my_ticket` adds `detail["worker"]`.
8. `src/planner/cli/main.py` — `worker_my_ticket` human line adds `worker: <name>`.
9. `tests/support/probe.py` — `PROBE_KNOWN_SKILLS` gains `"panels-worker-coding"`.
10. `tests/unit/test_ticket_type_registry.py` — golden updates (`EXPECTED`, `KNOWN_SKILLS`) + structural-invariant test.
11. `tests/unit/test_minds.py` — REWORK the existing base-skill lifecycle assertion for the split (Codex F3).
12. `tests/unit/test_ticket_type_persistence.py` — `_two_type_registry` `known_skills` += `panels-worker-coding` (Codex F2).
13. `tests/unit/test_readiness_actions.py` (or new) — by-session coding + probe worker assertions.
14. `tests/unit/test_cli_entrypoints.py` — my-ticket human-line assertion.
15. Run `./verify`, cite full output.

## 10. Risks
- **`_worker_role`/specialist naming split (highest).** Base role stays `panels-worker`; only the on-demand
  specialist becomes `panels-worker-coding`. Do not touch `worker_role`/`config.worker_skill`. Logged in `decisions.md`.
- **Catalog drift (highest, easy to under-count).** Every registry including `CODING_DEFINITION` needs
  `panels-worker-coding` in its `known_skills`: production `coding_bridge._KNOWN_SKILLS`, the registry test's
  local `KNOWN_SKILLS`, **`test_ticket_type_persistence.py::_two_type_registry` (Codex F2)**, and
  `PROBE_KNOWN_SKILLS`. Miss one → R14 build failure that blocks that suite. Grep the whole tree for
  `known_skills=`/`specialist_skill=`/asserted `"panels-worker"` before finishing.
- **Existing skill-content test (Codex F3).** `test_minds.py` pins the OLD (pre-split) base skill (coding
  sequence + `needs_*` + "never approve" in `panels-worker`). It MUST be reworked for the split or `./verify`
  fails even with a correct extraction.
- **skill_view availability (Codex F1).** Works under the default `hermes-cli` toolset; a restricted
  `HERMES_TUI_TOOLSETS` would omit it. Operational precondition (don't restrict the worker toolset), confirmed
  by the live run — not code.
- **Provisioning raises on a missing dir.** Create the two `skills/` dirs (steps 1–2) before touching config.
- **Base-skill extraction fidelity (owner constraint).** No rewording; only the two extractions + line-32
  deletion + the one added section. The split test (§7c) fails if a discipline is dropped or a stage name
  stranded in the base.
- **Golden manifest test** flips `worker_profile_id` — expected, call it out so it doesn't read as an accident.
- **Scope guard.** No `HERMES_TUI_TOOLSETS`, no per-session model/effort, no second production type, no
  engine/contract shape change. Substantive code touch is only `get_my_ticket` + the CLI line.
