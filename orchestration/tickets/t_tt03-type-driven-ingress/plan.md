# t_tt03 — Type-driven CLI/API ingress + external-work genericization + the go/no-go gate — implementation plan

Read `BRIEF.md` (authoritative) and `../../ticket-types-redesign/PLAN.md` (Phase 3 + "First go/no-go") first. This plan is line-anchored to the tree as of this write; re-confirm anchors before editing.

## 0. What already exists (so we build nothing twice)

The engine below the ingress is **already per-type**. Every data-layer writer resolves the row's definition and threads it:
- `data.py:69-93` `_row_to_ticket` → `ticket_type_guard.resolve_and_validate(ticket_type, state, ceiling)` returns the `WorkflowDefinition`; `data.py:126-152` `_apply_decision` re-validates the prospective `(state, ceiling)` through the same guard before SQL.
- `data.py:276-277`, `data.py:381-382`: both create paths already call `coding_bridge.require(ticket_type)` + `coding_bridge.default_ceiling(ticket_type)` — they accept a `ticket_type` kwarg (defaulting `"coding"`), build `TicketFields.empty(coding_bridge.field_ids(defn))`, and pass `definition=defn` into `external_work.decide_external_work`.
- `machine.py` state_index/gating_field/advance_target/is_terminal all take `definition=...` and are `str`-native (`machine.py:76-129`).
- `ticket_type_guard.resolve_and_validate` (`ticket_type_guard.py:29-52`) is the one load/persist/create validation door; the startup audit reuses it (`data.py:510-534`).

So t_tt03 is **almost entirely an ingress-layer ticket**. Three engine remnants remain coding-bound and are the only non-ingress edits: `external_work._FIELD_ORDER`/`_PREFIX_COUNT` + the coding-only rejection (`external_work.py:23-89`), and the hardcoded `needs_success` initial insert in `create_ticket_from_external_work` (`data.py:424`).

The registry already serves the exact manifest shape we need: `Registry.manifest(type_id)` → `serialize_definition` (`registry.py:73-74`, `manifest.py:29-56`) produces `ManifestDict` (`ticket_types/contracts.py:75-85`). The probe fixture (`tests/support/probe.py`) is complete: `install_probe_registry()` sets the process-global test registry via `coding_bridge.set_registry_for_test`, `PROBE_MANIFEST` (asserted in `tests/unit/test_probe_type.py:102-130`) is the exact served shape.

---

## 1. Split recommendation: SINGLE ticket (do NOT split t_tt03a/t_tt03b)

**Recommendation: keep t_tt03 as one ticket.** Reasoning from file overlap and serial-integration cost:

- The proposed a/b cut (a = manifest + backend ingress + external-work; b = CLI + gate) does **not** reduce file overlap — it *increases* it. `api.py`, `cli/main.py`, and `external_work.py` are each touched by both halves: the gate test drives the very `api.py` ingress the "backend" half rewrites, and the CLI approve/create paths in `cli/main.py` consume the manifest the "backend" half serves. A split forces two serial integrations over the same three files, plus a frozen contract between them (the manifest route shape + the per-type parse helpers) that only one consumer exists for.
- The engine surface is genuinely small (the three remnants above are ~40 lines). The bulk is a mechanical parse-swap at a well-enumerated set of ingress points (Section 3 table) plus one new route and its consumer. That is one coherent unit of work.
- The gate test is the acceptance for the whole ingress change — splitting it from the ingress it exercises means t_tt03a would have no falsifiable acceptance of its own (it'd assert manifest-shape + coding-byte-identity only, deferring the real proof).
- Parallelism buys nothing: b depends on a's every output. There is no independent lane.

**If size forces a cut anyway**, the only clean seam is: **t_tt03a = the manifest endpoint alone** (new route + `/api/meta`-style wiring + CLI manifest fetch helper + its shape test), landed first as a pure addition with zero behavior change; **t_tt03b = everything else** (all ingress parse-swaps, external-work genericization, the gate). That seam has near-zero file overlap (the route is additive) and a's acceptance is self-contained (the served-shape test). This is the fallback, noted for the orchestrator; the primary recommendation is single.

---

## 2. The manifest endpoint

### Route
Add `GET /api/ticket-types` in `src/planner/core/server.py` alongside `/api/meta` (`server.py:215-221`) — NOT in `tickets/api.py`, because it serves the registry directly and has no ticket-row dependency, mirroring how `/api/meta` serves config directly. It resolves the **active** registry via `coding_bridge.registry()` (so a test-installed probe registry is served in-process; a production subprocess serves coding-only).

```
@app.get("/api/ticket-types")
async def ticket_types() -> dict[str, Any]:
    reg = coding_bridge.registry()
    return {"types": [reg.manifest(tid) for tid in reg.type_ids()]}
```

- Shape: `{"types": [ManifestDict, ...]}`, one entry per `reg.type_ids()`, each entry the exact `serialize_definition` dict. For production this is `[<coding manifest>]`; when a test installs probe it is `[<coding>, <probe>]` (order = `type_ids()` insertion order = `[coding, probe]`).
- Import: `server.py` must import `coding_bridge` (F6 boundary is unaffected — `server.py` is composition-root, already imports domain modules freely; it does NOT import `ticket_types` directly, only the `coding_bridge` seam).
- No auth: read-only, same posture as `/api/meta`.

### CLI consumption
Add a thin fetch helper in `cli/main.py` (near `_current_sprint_id`, `main.py:182`):

```
def _ticket_types(as_json: bool) -> dict[str, ManifestDict]:
    data = http.send("GET", "/api/ticket-types", as_json=as_json, request_actor="ordinary")
    return {m["type_id"]: m for m in data["types"]}

def _ticket_type(type_id: str, as_json: bool) -> ManifestDict:
    types = _ticket_types(as_json)
    if type_id not in types:
        http.fail_validation(f"unknown ticket type: {type_id} (known: {', '.join(sorted(types))})", as_json)
    return types[type_id]
```

The CLI consumes the manifest for exactly two things this ticket (Sections 4, 6): validating `ticket create --type`'s value client-side (nice error before the round-trip; the server re-validates) and driving `ticket approve`'s gating-field lookup off the ticket's own type instead of the global `GATING_FIELD` map. Web consumption is out of scope (t_tt04b).

---

## 3. The ingress-point table (the parse-swap)

Every ingress point that parses a global enum, with file:line and the per-type replacement. **The universal rule: resolve the ticket's `ticket_type` FIRST, then validate the position against that type's definition via the manifest/registry, and pass a bare `str` to the engine** (the engine is already `str`-native; the enum coercions are what block probe states like `needs_alpha`).

Type resolution differs by whether the ticket exists:
- **Ticket exists** (propose/accept/scope/state/reconcile/value/note): resolve `ticket_type` by loading the ticket. Route handlers already receive `ticket_id`; add a `read_ticket` before parsing, OR — cheaper — let the writer validate (see per-row note below). Prefer: load once, resolve `defn = coding_bridge.require(ticket.ticket_type)`, validate the position, then call the writer. This keeps the "parse → auth → writer → serialize" shape.
- **Create** (`create_ticket`, external create): type comes from the request body (`--type` / `type` key).

| # | Ingress point | file:line | Today | Change |
|---|---|---|---|---|
| 1 | `create_ticket` (API) | `api.py:320-346` | no type; `create_ticket(...)` defaults `coding` in data | Marshal a **required** `ticket_type` from body (Section 4); pass `ticket_type=body["type"]` into `tickets_actions.create_ticket`; the data layer already rejects unknown via `coding_bridge.require`. Reject missing with `validation` + type list. |
| 2 | `_marshal_create_ticket` | `api.py:167-177` | no `type` key | Add `ticket_type=body_str(raw, "type")` (required — empty string rejected downstream as unknown type). |
| 3 | `propose_field` | `api.py:541-550` | `parse_enum(FieldName, field, "field")` | Load ticket → `defn`; validate `field` against `coding_bridge.views.field_ids(defn)` (or `has_field`); pass the raw `str` field to `file_proposal`. Reject unknown with `validation`, `{"field": field, "type_id": ...}`. |
| 4 | `propose_current_field` | `api.py:522-538` | position-relative, no field parse | No enum parse today; leave as-is (writer resolves gating field per-type via `data.py:693-694`). Confirm byte-identical. |
| 5 | `accept_field` | `api.py:553-573` | `parse_enum(FieldName, field, "field")` + `_parse_next_ceiling`→`TicketState` | Load ticket → `defn`; validate `field` against the type's fields; `_parse_next_ceiling` and `_parse_scope_at_cap` must validate `next_ceiling` against `coding_bridge.views.ceiling_range(defn)` (not global `TicketState`). Pass raw `str`. |
| 6 | `scope_ticket` | `api.py:646-681` | `TicketState(ceiling_raw)` at 661 | Load ticket → `defn`; validate `ceiling_raw in views.ceiling_range(defn)` → `scope_invalid` else; `AtCap` stays global (type-independent). Pass raw `str` ceiling. |
| 7 | `set_state` | `api.py:684-699` | `parse_enum(TicketState, body["to"], "state")` | Load ticket → `defn`; validate `to` is a linear stage of the type (`views.state_index(defn, to)` raises `validation` on unknown) OR the reserved `dropped`. Pass raw `str`. (Direct `/state` is not used by the gate but must stop rejecting probe states.) |
| 8 | `put_value` | `api.py:627-643` | `parse_enum(FieldName, field, "field")` | Load ticket → `defn`; validate `field` against the type's fields. Pass raw `str`. |
| 9 | `put_notes` | `api.py:599-615` | `parse_enum(FieldName, field, "field")` | Load ticket → `defn`; validate `field` against the type's fields. Pass raw `str`. |
| 10 | `create_ticket_from_external_work` (API) | `api.py:349-386` | `parse_enum(TicketState, body["state"], "state")` at 356; `_external_values` hardcodes 6 coding fields | Marshal a required `ticket_type` from body; resolve `defn`; validate `body["state"]` against the type's stages; build provided-values from the type's declared fields (Section 5); pass raw `str` `target_state` + `ticket_type` down. |
| 11 | `reconcile_ticket_from_external_work` (API) | `api.py:389-409` | `parse_enum(TicketState, body["state"], "state")` at 396 | Load ticket → `defn` (type from the existing row, NOT the body); validate `body["state"]` against the type's stages; build provided-values per-type; pass raw `str`. |
| 12 | `list_tickets` `?state=` | `api.py:412-433` (`state_enum = parse_enum(TicketState, ...)` at 417) | global `TicketState` | See Section 7. |
| 13 | `_external_provided_values` | `api.py:267-281` | hardcodes `kickoff/success/approach/plan/implementation/closeout` | Rebuild from the type's `field_ids` (Section 5). |
| 14 | `_marshal_external_reconcile` / `_marshal_external_create` allowed-keys | `api.py:180-264` | `_EXTERNAL_RECONCILE_KEYS` frozenset hardcodes the 6 coding field keys | Make the allowed field-key set per-type (Section 5): the fixed keys (`state`/`kickoff_note`/`recap` + create's `title`/`priority`/…) stay static; the field-value keys derive from the resolved type's `field_ids` minus `kickoff` (kickoff arrives as `kickoff_note`). |

### CLI ingress points

| # | CLI surface | file:line | Change |
|---|---|---|---|
| C1 | `ticket create` | `main.py:443-491` | Add required `--type` option; send `body["type"]`. Optionally validate client-side via `_ticket_type` for a friendly error; server is authoritative. |
| C2 | `ticket approve` | `main.py:597-658` | Replaces `state = TicketState(detail["state"])` + `field = GATING_FIELD.get(state)` (`main.py:621-622`) with a manifest lookup: fetch the ticket's `ticket_type`, look up the stage's `gating_field` from that type's manifest (`stages[state].gating_field`). This is the one CLI spot that hard-couples to coding's global `GATING_FIELD`. |
| C3 | `chief create-ticket-from-external-work` | `main.py:1129-1200` | Add required `--type`; the `--state` `click.Choice([...WORKER_STATE_ORDER])` (`main.py:1132`) becomes free-form `--state` validated server-side per-type (static Click choices cannot know the type). The per-field file options (`--success-file`, `--approach-file`, …, `main.py:1137-1144`) are coding-shaped; see Section 6. |
| C4 | `chief reconcile-ticket-from-external-work` | `main.py:1075-1126` | Same `--state` static-choice removal (`main.py:1078`); per-field file options per Section 6. |
| C5 | `worker note` field allow-list | `main.py:1274` `if field not in _FIELDS` | `_FIELDS` (`main.py:34`) is coding's 5 non-kickoff fields. Leave coding-only for this ticket (worker CLI is not on the gate path and `worker` is t_tt05 territory); flag as a known coding literal (Section 9). Do NOT expand speculatively. |
| C6 | module-level coding constants | `main.py:25-34` (`GATING_FIELD`, `WORKER_STATE_ORDER`, `FieldName`, `TicketState`, `_FIELDS`) | `GATING_FIELD`/`TicketState` import removed once C2 uses the manifest; `WORKER_STATE_ORDER` import removed once C3/C4 drop the static `--state` choice. Keep `AtCap` (type-independent), `_PRIORITIES`. |

---

## 4. Create requires a type

- **API** (`_marshal_create_ticket`, `api.py:167`): add `ticket_type=body_str(raw, "type")`. `body_str` defaults to `""` when absent; `""` is not a registered type, so `coding_bridge.require("")` raises `not_found` → surfaces as "unknown ticket type". **Refinement**: the BRIEF wants "reject a create with no/unknown type, surfacing the valid type list." So handle it explicitly in the create handler (`api.py:321`): if `body["type"] == ""` raise `PlannerError(validation, "ticket create requires a type", {"types": list(reg.type_ids())})`; else pass to the writer, which rejects unknown with `not_found` (`data.py:276`). Consider upgrading the data-layer `require`'s `not_found` message detail to include the type list — but that touches the engine door; simpler to catch unknown at the API by pre-checking `body["type"] in reg.type_ids()` and raising `validation` with the list. **Decision: validate the type at the API handler** (`create_ticket`, `create_ticket_from_external_work`) against `coding_bridge.registry().type_ids()`, raising `ErrorCode.validation` with `{"type": raw, "types": [...]}` for both missing and unknown — one consistent error shape carrying the list, before the writer runs.
- **CLI** (`ticket_create`, `main.py:458`): `--type` required (`click.option("--type", "ticket_type", required=True, ...)`); `body["type"] = ticket_type`.
- **Remove the ingress default bridge**: the data-layer keeps `ticket_type: str = "coding"` as a *signature* default (many internal test callers rely on it — `create_ticket(conn, title=..., kickoff_note=...)` with no type appears across the suite). Do NOT remove the kwarg default in `data.py:272`/`372` (that would break dozens of existing unit tests and is not the ingress). Instead remove the **ingress** default: the API/CLI always send an explicit type, so the wire path never relies on the data default. This satisfies "remove the `ticket_type="coding"` data-layer default bridge at the ingress" — the *ingress* stops defaulting; the internal kwarg default is a test-ergonomics affordance, not an ingress bridge. (Flag for Codex: confirm this reading against the BRIEF; if the owner wants the kwarg default gone too, that is a mechanical sweep of ~40 internal call sites and belongs in its own note.)
- **Ceiling = type default**: already handled — `data.py:277`/`382` set `default_ceiling = coding_bridge.default_ceiling(ticket_type)` and insert it (`data.py:328`/`430`). No change; the gate asserts probe creates at `needs_alpha`.

---

## 5. External-work genericization (finish the t_tt02b deferral)

### 5a. `_external_provided_values` from the type's fields (`api.py:267-281`)
Replace the hardcoded 6-field map with a per-type builder. Signature becomes `_external_values(body, defn)`; it maps `kickoff` ← `body["kickoff_note"]`, then for each declared field id in `views.field_ids(defn)` other than `kickoff`, if that key is present in `body`, include it. Returns `dict[str, str]` (bare str keys — the engine and `external_work.decide_external_work` are already `str`-native via `Mapping[FieldName, str]` which at runtime is just a str-keyed dict; **widen the annotation** in `external_work.py:43`/`data.py:361`/`actions.py:62` from `Mapping[FieldName, str]` to `Mapping[str, str]`).

### 5b. Per-type allowed field keys (`_EXTERNAL_RECONCILE_KEYS`, `api.py:180-202`)
Today `_EXTERNAL_RECONCILE_KEYS` is a static frozenset with coding's 6 fields. Split into:
- static keys always allowed: `{"state", "kickoff_note", "recap"}` (+ create-only: `title`, `priority`, `deadline`, `project`, `project_id`, `sprint_id`, `sprint_item_id`).
- per-type field keys: `views.field_ids(defn)` minus `kickoff`.

The marshal must resolve the type BEFORE checking unknown keys. For **create**, the type is in the body → marshal reads `type` first, resolves `defn`, then computes the allowed set. For **reconcile**, the type comes from the existing ticket → the handler loads the ticket, resolves `defn`, and passes the allowed field-key set into the marshal (or does key-validation in the handler after load). **Decision: move the unknown-key rejection into the handler** (`reconcile_ticket_from_external_work`, `api.py:389`) after `read_ticket` resolves the type; the marshal becomes type-parameterized (`_marshal_external_reconcile(raw, defn)`). This preserves the strict "reject unknown external-work field" behavior (`api.py:205-212`) per-type. Byte-identical for coding (its field set is the same 6).

### 5c. Target-state validation per-type + drop the `TicketState` coercion
- API create (`api.py:356`) and reconcile (`api.py:396`): replace `parse_enum(TicketState, body["state"], "state")` with per-type validation: `state = body["state"]`; assert `state in views.ceiling_range(defn)` (external-work targets are always worker stages, never `needs_kickoff`) else raise the existing external-work message. Pass the raw `str` down.
- Thread `ticket_type` into `create_ticket_from_external_work` (API `api.py:369` → actions `actions.py:75` → data `data.py:356`): the data path already takes `ticket_type` (`data.py:372`) — plumb it from the body through actions (add `ticket_type` kwarg to `actions.create_ticket_from_external_work`, `actions.py:57`). Reconcile takes no `ticket_type` (row-derived) — no change to its signature.
- **Widen `target_state` annotations** from `TicketState` to `str` in `actions.py:61`/`99`, `data.py:360`/`460`, `external_work.py:43`. Runtime is already `str` (data stores `str(new_state)`); this is a type-annotation correctness fix so probe states type-check.

### 5d. Replace `create_ticket_from_external_work`'s hardcoded `needs_success` (`data.py:424`)
The INSERT hardcodes `TicketState.needs_success.value` as the seed state (the row is then moved to `target_state` by the applied external-work decision). For probe this is wrong (`needs_success` is not a probe stage — the pre-persist guard would reject it). Replace with the type's **first worker stage** = `coding_bridge.default_ceiling(ticket_type)` (already computed as `default_ceiling` at `data.py:382`) — equivalently `views.ceiling_range(defn)[0]`. For coding this is `needs_success` (byte-identical); for probe `needs_alpha`. Use the already-bound `default_ceiling` local. (The subsequent `decide_external_work` moves state + ceiling to `target_state`; the seed state only needs to be a valid non-terminal worker stage so the pre-persist guard passes.)

### 5e. GATE-BASED prefix derivation (replace `_PREFIX_COUNT`/`_FIELD_ORDER`, `external_work.py:23-38`)
This is the t_tt02b field-order bug fix. The settled prefix must come from the **non-terminal stages' `gating_field` order**, NOT `field_ids` (the registry does not order-align `field_ids` with the stage order — `FieldDef` order is declared separately from `Stage` order, and a type could legally declare them differently).

Derive both from `defn.stages`:
```
def _gate_field_order(defn) -> tuple[str, ...]:
    # gating fields of the non-terminal stages, in stage order (kickoff-led)
    return tuple(s.gating_field for s in defn.stages if not s.is_terminal and s.gating_field is not None)

def _prefix_count(defn, target_state: str) -> int:
    # number of settled fields for target_state = its index in the linear stage order
    return views.state_index(defn, target_state)
```
- `_gate_field_order(coding)` = `("kickoff", "success", "approach", "plan", "implementation", "closeout")` — identical to the current `_FIELD_ORDER` (`external_work.py:23-30`).
- `_prefix_count(coding, "needs_success")` = `state_index(needs_success)` = 1; `..."done"` = 6 — identical to the current `_PREFIX_COUNT` (`external_work.py:31-38`). Note `state_index` counts `needs_kickoff`=0, `needs_success`=1, …, `done`=6, so `state_index(target)` IS the settled-field count (kickoff through the field gating the stage before `target`). This equals today's map exactly.
- `_gate_field_order(probe)` = `("kickoff", "alpha", "beta")`; `_prefix_count(probe, "needs_alpha")`=1, `"needs_beta"`=2, `"done"`=3.

Rewrite `decide_external_work` (`external_work.py:41-163`) to:
1. Take `definition` (already does, `external_work.py:46`); resolve `field_order = _gate_field_order(defn)`, `expected_count = _prefix_count(defn, target_state)`.
2. **Honor `supports_prefix_reconciliation`** (`WorkflowDefinition.supports_prefix_reconciliation`, `ticket_types/contracts.py:57`): if `not defn.supports_prefix_reconciliation`, raise `PlannerError(validation, "type does not support external-work prefix reconciliation", {"type_id": defn.type_id})`. Both coding and probe declare `True`, so this branch is validated but not hit by the gate's happy path — it is the "a type may decline it" contract. (No production type declines it today; the branch earns its place as the declared contract point, tested via a synthetic `supports=False` type in the unit test, Section 8.)
3. **LIFT the coding-only rejection** (`external_work.py:58-64`) — delete the `defn.type_id != "coding"` raise.
4. Replace `_FIELD_ORDER` iteration (`external_work.py:80,92`) with `field_order`; replace `_PREFIX_COUNT[target_state]` (`external_work.py:89`) with `expected_count`.
5. Replace the `machine.state_index(target_state)` / `machine.state_index(ticket.state)` calls (`external_work.py:73`) with `machine.state_index(target_state, definition=defn)` / `machine.state_index(ticket.state, definition=defn)` (thread the definition — currently they call the coding default).
6. Replace the ceiling-range check (`external_work.py:65`) `coding_bridge.views.ceiling_range(defn)` — already correct, keep; it uses `defn`.
7. `get_slot`/`with_slot` calls (`external_work.py:81,93,120`) already take `str` field ids — pass `field` (bare str) from `field_order`.
8. Delete the module constants `_FIELD_ORDER` and `_PREFIX_COUNT` (`external_work.py:23-38`) and the stale coding-only docstring lines (`external_work.py:52-57`).

### 5f. PERMANENT golden test pinning coding's prefix map
Add a test asserting the gate-based derivation equals coding's canonical map (Section 8, T-EW-golden). This is the guard that the derivation cannot drift back into a hand-copied order.

---

## 6. CLI Chief external-work surfaces (`main.py:1040-1200`)

- `--state` static `click.Choice([...WORKER_STATE_ORDER])` (`main.py:1078`, `main.py:1132`): remove the static choice — a static Click choice cannot know the ticket's type. Make `--state` a plain required string; the server validates it per-type. This means `chief ... --help` no longer lists coding's states — acceptable and correct for an N-type CLI. **Note**: `tests/e2e/test_chief_external_work_cli.py:33-40` asserts `--help` lists `needs_success` and excludes `needs_kickoff`; that test must be updated (it encodes coding's states in `--help`). This is a mechanical test move, flagged in Section 9.
- The per-field file options (`--success-file`, `--approach-file`, `--plan-file`, `--implementation-file`, `--closeout-file`; `main.py:1137-1144`, `main.py:1084-1090`) and `_external_work_body`'s field loop (`main.py:1057-1064`) are coding-shaped. **Decision: keep the coding field options static in the CLI for this ticket.** Rationale (everything earns its existence): a generic `--field NAME=FILE` surface would be new scope not asked for, and the *production* CLI is coding-only. The probe external-work path is proven through the **in-process API** in the gate (Section 8), where field keys are driven by the manifest — not through the CLI subprocess (which cannot see the test registry anyway, Section 8's transport constraint). So the CLI keeps coding's named file options; the genericity proof rides the API. Flag this explicitly to the owner (Section 9) — it is a deliberate scope boundary, not a silent descope.
- `chief create-ticket-from-external-work` gains a required `--type` option → `body["type"]` (the API create-from-external now requires a type). For coding this is `--type coding`. The e2e coding CLI test adds `--type coding`.

---

## 7. The `?state=` filter decision (`api.py:412-433`, `views.py:104-138`)

**Decision: require `ticket_type` when filtering by a non-reserved state; reserved bookends filter across types without a type.**

Rationale: `state` alone is ambiguous across types (`needs_alpha` means nothing to coding; a bare `needs_success` filter across a mixed board is a coding-only concept). The reserved bookends (`needs_kickoff`, `done`, `dropped`) are shared identically by every type (PLAN invariant 1), so they filter across types unambiguously.

Implementation (`list_tickets` handler, `api.py:412`):
- Add an optional `ticket_type: str | None = None` query param.
- If `state is None`: no state filter (unchanged).
- If `state` is one of the three reserved ids (`needs_kickoff`, `done`, `dropped`): filter by that state string across all types — no `ticket_type` required. (These are the same string for every type; the SQL `state = ?` at `views.py:117` already does the right thing.)
- If `state` is a non-reserved id: require `ticket_type`; validate `state` against that type's stages (`views.state_index(require(ticket_type), state)`), then filter by the state string. If `ticket_type` is absent → `PlannerError(validation, "filtering by a non-reserved state requires ticket_type", {"state": state})`.
- The reserved-id set is the three shared bookends; define it once (e.g. `RESERVED_STATES = frozenset({"needs_kickoff", "done", "dropped"})`) near the handler. `dropped` is already the reserved exceptional terminal; `needs_kickoff`/`done` are the universal linear bookends.
- `views.list_tickets` (`views.py:104`) needs no signature change beyond accepting a bare `str` state (drop the `TicketState | None` annotation → `str | None`; the SQL already stringifies). The `ticket_type` filter, if we also want to scope the *list* to a type, is a separate optional param — but the BRIEF only requires disambiguating the state filter, so `ticket_type` is used for **validation**, not necessarily as an additional WHERE clause. **Decision: use `ticket_type` only to validate/disambiguate the state; do not add a type WHERE clause** (no one asked to filter the list *by type* — that is unrequested scope). If a later need arises it is a one-line add.
- CLI (`ticket list`, `main.py:522-552`): add optional `--type` passed as the `ticket_type` param; only meaningful alongside `--state`. Keep `--state` free-form (already is).

**This is the sharpest open decision — see Risk R1.** The alternative (explicit cross-type union) is more permissive but requires inventing a union syntax no caller has asked for; the require-type rule is the minimal correct choice and matches how the rest of the ingress resolves type-first.

---

## 8. The go/no-go gate test

### Transport constraint (the load-bearing design fact)
The CLI (`http.send`) hits a **live server** at `PLAN_SERVER_URL`; the e2e harness spawns a real `panels serve` **subprocess** (`tests/e2e/conftest.py:99-105`). `coding_bridge.set_registry_for_test` sets a **process-global** — it CANNOT reach a subprocess, which builds its own coding-only registry via `coding_registry()`. Therefore:

- **Probe** must be driven through the **in-process FastAPI `TestClient`** (as `tests/unit/test_chief_external_work.py:29-47` does via `create_app(...)`): install the probe registry in the test process (`install_probe_registry()`), then the in-process API handlers resolve probe via `coding_bridge.registry()`. This exercises the **real HTTP ingress** (real routes, real marshallers, real per-type parsing, real writers, real events) — it is genuinely "through the CLI/API" at the API layer, satisfying the BRIEF's "REAL propose/accept, no `/state` jumps."
- **Coding** must also be driven through the real ingress. Drive coding through the same in-process `TestClient` for the gate (byte-identical to production since coding is in every registry), AND keep the existing e2e CLI coding path (`test_chief_external_work_cli.py`, updated for `--type coding`) as the subprocess-level coding proof. The CLI-subprocess proof stays coding-only (the subprocess can't see probe) — this is why Section 6 keeps coding's CLI field options.

**Decision**: the go/no-go gate lives as a **new in-process API test** `tests/unit/test_go_no_go_gate.py`, using `create_app` + `TestClient` + `install_probe_registry()` (fixture with teardown `uninstall_probe_registry()`), driving probe AND coding through real HTTP routes. The "no worker session" assertion queries the DB directly (as `test_probe_type.py` does). This is the falsifiable milestone. The CLI is additionally exercised for coding via the existing e2e test (mechanical `--type coding` update) so the CLI wiring (`--type`, manifest-driven approve) is covered end-to-end for the shipped type.

### The 9 steps (probe, in-process API; assert exact values)

Fixture: `client = TestClient(create_app(...))`; `install_probe_registry()` before, `uninstall_probe_registry()` after; direct DB conn for the no-worker assertion.

1. **Create** `POST /api/tickets` with `{"title": ..., "type": "probe", "kickoff_note": ...}`. Assert response: `state == "needs_kickoff"`, `ceiling == "needs_alpha"` (probe's first worker stage — proves per-type default), `ticket_type == "probe"`, `fields` keys == `["kickoff", "alpha", "beta"]` in order with `kickoff` carrying the kickoff proposal and `alpha`/`beta` empty. Also `GET /api/ticket-types` → assert probe's manifest entry `== PROBE_MANIFEST` (import from `test_probe_type` or re-declare; Section 8 tests).
2. **Propose-with-recap at each non-terminal stage**: `POST /api/tickets/{id}/propose` with `{"body": ..., "recap": ...}` (position-relative, no field in path — uses the current gating field). Do this for `needs_kickoff` (gates `kickoff`), then after each accept, for `needs_alpha` (gates `alpha`), `needs_beta` (gates `beta`).
3. After each propose, `GET` the ticket and assert the proposal parked on the **registry-selected field** for the current stage: `needs_kickoff`→`kickoff`, `needs_alpha`→`alpha`, `needs_beta`→`beta` (the field the type's `gate_map` selects), and `at_cap`/`ceiling` unchanged from create until accept.
4. **Accept** `POST /api/tickets/{id}/accept/{field}` with `{"next_ceiling": <next>, "at_cap": "stop"}` (exact next ceiling per step: kickoff→accept advances to `needs_alpha`, set `next_ceiling="needs_beta"`; alpha→`needs_beta` advance, `next_ceiling="done"`; beta→`done`, `next_ceiling="none"`). Assert `at_cap` echoed.
5. After each accept assert: accepted body settled in the field's `value`; proposal cleared (`fields[field].proposal is None`); `state` advanced to the next stage; `ceiling` == the accepted `next_ceiling` (or the newly-entered state for `"none"`); and the **emitted event order** via `GET /api/tickets/{id}/events` — the exact kind sequence (`proposal_filed`, `recap_updated`?, `field_value_edited`/accept events, `state_changed`, `scope_changed`, `ticket_status_changed`) as the engine emits. (Pin the exact order by first observing it in a scratch run, then asserting the literal list — mirror `test_probe_type`'s event-order style.)
6. **Repeat through `done`**: final accept of `beta` lands `state == "done"`.
7. **No worker session**: query the DB — `chat_session_key IS NULL` on the row AND `SELECT count(*) FROM chat_turns WHERE entity_id = <id>` == 0 (propose/accept never spawn a worker; the gate proves the whole drive is reachable human-only). Mirror `test_probe_type.py`'s no-session assertion.
8. **Invalid-input codes** (assert exact `ErrorCode` + message + detail dict):
   - invalid **field**: `POST /api/tickets/{id}/propose/ghost` → `validation`, detail carries `{"field": "ghost", ...}` (probe has no `ghost` field).
   - invalid **state**: `POST /api/tickets/{id}/state` `{"to": "needs_success"}` on a probe ticket → `validation` "state outside the linear order" (coding's state is foreign to probe).
   - invalid **ceiling**: `POST /api/tickets/{id}/scope` `{"ceiling": "needs_plan", "at_cap": "stop"}` → `scope_invalid` (probe ceiling range is `needs_alpha/needs_beta/done`).
   - **missing type** create: `POST /api/tickets` with no `type` → `validation`, detail `{"types": [...]}`; **unknown type** create: `{"type": "nonesuch"}` → `validation` with the type list.
9. **External-work create + reconcile** for coding AND probe:
   - **coding create**: `POST /api/chief/tickets/from-external-work` `{"type": "coding", "title": ..., "state": "needs_plan", "kickoff_note": ..., "success": ..., "approach": ..., "plan": ...}` → asserts `state == "needs_plan"`, the settled prefix (`success/approach/plan` set, `implementation/closeout` empty), byte-identical to the existing coding external-work test (`test_chief_external_work.py:134-153`).
   - **probe create**: `POST /api/chief/tickets/from-external-work` `{"type": "probe", "title": ..., "state": "needs_beta", "kickoff_note": ..., "alpha": ...}` → asserts `state == "needs_beta"`, prefix `kickoff+alpha` settled, `beta` empty. This exercises `_prefix_count(probe, "needs_beta")`==2 and the per-type field-key marshalling.
   - **probe reconcile** (prefix reconciliation, `supports_prefix_reconciliation`): create a probe external-work ticket at `needs_alpha`, then `POST /api/chief/tickets/{id}/reconcile-from-external-work` `{"state": "needs_beta", "kickoff_note": ..., "alpha": ...}` → asserts forward move, prefix extended. Type resolved from the row.
   - **coding golden**: assert `_gate_field_order(coding)` and the derived prefix counts equal coding's canonical map (T-EW-golden below).

---

## 9. Tests — every acceptance item as asserted values

New / changed test files:

- **`tests/unit/test_go_no_go_gate.py`** (NEW) — the 9-step gate above, probe + coding through in-process HTTP; exact asserted values; no-worker DB assertion.
- **`tests/unit/test_ticket_type_manifest_endpoint.py`** (NEW) — `GET /api/ticket-types`: production (coding-only) returns `{"types": [<coding manifest>]}` asserted as the exact dict; with probe installed returns `[<coding>, <probe>]`; JSON round-trips; the coding entry `== serialize_definition(CODING_DEFINITION)`.
- **`tests/unit/test_type_driven_ingress.py`** (NEW) — per-type validation codes at each ingress (propose/accept/scope/state/value/note) for probe vs coding: exact `ErrorCode` + detail for invalid field/ceiling/state; a coding ticket behaves identically to today; a probe proposal parks on the registry-selected field. Missing/unknown-type create codes.
- **`tests/unit/test_external_work_generic.py`** (NEW) — the gate-based prefix derivation:
  - **T-EW-golden (PERMANENT)**: `_gate_field_order(coding_bridge.coding_definition())` == `("kickoff", "success", "approach", "plan", "implementation", "closeout")`; and for each coding worker state, `_prefix_count(defn, state)` == the pinned map `{needs_success:1, needs_approach:2, needs_plan:3, needs_implementation:4, needs_closeout:5, done:6}`. This is the drift guard.
  - probe derivation: `_gate_field_order(probe)` == `("kickoff", "alpha", "beta")`; `_prefix_count(probe, {needs_alpha:1, needs_beta:2, done:3})`.
  - a synthetic `supports_prefix_reconciliation=False` type raises `validation` "type does not support external-work prefix reconciliation".
- **`tests/unit/test_state_filter.py`** (NEW or fold into ingress test) — `?state=` decision: reserved bookend filters (`done`/`dropped`/`needs_kickoff`) work without `ticket_type`; a non-reserved state without `ticket_type` → `validation`; with the right `ticket_type` it validates and filters; a foreign state for the given type → `validation`.

Changed existing tests (mechanical moves, all flagged as coding-literal encodings):
- **`tests/e2e/test_chief_external_work_cli.py:33-40`** — the `--help` assertion listing `needs_success`/excluding `needs_kickoff` breaks when `--state` drops its static Click choice. Rewrite to assert the CLI accepts a `--state needs_success --type coding` external-work create; drop the `--help`-lists-states assertion (no longer a static choice). Add `--type coding` to the create/reconcile invocations (`test_chief_external_work_cli.py`).
- **`tests/unit/test_chief_external_work.py`** — add `"type": "coding"` to the create-from-external bodies (the API now requires a type on create-from-external). Reconcile bodies unchanged (type row-derived). All asserted values otherwise identical (byte-identical coding behavior).
- Any **`ticket create`** call sites in unit/e2e tests that POST `/api/tickets` without a `type` now need `"type": "coding"` (or the CLI `--type coding`). Sweep: `grep -rn '"/api/tickets"' tests/ | POST` + CLI `ticket create` invocations. These are mechanical.
- **`tests/support/probe.py`** — no change (fixture is complete); imported by the gate.

Byte-identical guarantees to assert:
- coding ingress: existing `test_chief_external_work.py`, `test_ticket_edit_api.py`, `test_ticket_lifecycle.py`, `test_value_edit_api.py`, the coding-drive in `test_probe_type.py` pass with only the mechanical `type` additions.
- coding external-work prefix golden holds (T-EW-golden).

---

## 10. Edit sequence (single ticket, serial within)

1. `external_work.py` — gate-based derivation, lift coding-only rejection, thread `definition` into `state_index`, honor `supports_prefix_reconciliation`, widen annotations, delete `_FIELD_ORDER`/`_PREFIX_COUNT`. (Self-contained engine change; unit-testable in isolation via T-EW-golden + probe derivation.)
2. `data.py:424` — seed state from `default_ceiling` instead of `needs_success`; thread `ticket_type` param already present. Widen `target_state`/`provided_values` annotations.
3. `actions.py` — add `ticket_type` kwarg to `create_ticket_from_external_work`; widen annotations.
4. `api.py` — the parse-swap (Section 3 table rows 1-14): required `type` marshalling on both create paths, per-type field/ceiling/state validation on propose/accept/scope/state/value/note, per-type `_external_values`, per-type allowed-key marshalling, the `?state=` decision.
5. `server.py` — the `GET /api/ticket-types` route.
6. `cli/main.py` — `--type` on `ticket create` + `chief create-from-external`; manifest-driven `ticket approve`; drop static `--state` choices; the `_ticket_types`/`_ticket_type` helpers; drop the now-unused `GATING_FIELD`/`TicketState`/`WORKER_STATE_ORDER` imports.
7. Tests (Section 9), then `./verify`.

---

## 11. Open risks (sharpest first)

**R1 — the `?state=` decision (Section 7).** This is the one place with real design latitude. The plan chooses "require `ticket_type` for a non-reserved state; reserved bookends filter cross-type." Risk: an existing caller (web, a test, a saved query) filters `?state=needs_success` with no type and now gets a `validation` error. Mitigation: `grep -rn 'state=' web/ tests/` for callers; the board (`/api/board`) does NOT use `?state=` (it reads all non-dropped and buckets client-side, `views.py:233-291`), so the main mixed-type consumer is unaffected. Confirm no e2e/web caller passes a bare non-reserved `?state=`. If one does, it becomes a mechanical `+ &ticket_type=coding`. **This risk should be re-confirmed by grep before implementing** and the decision surfaced to the owner.

**R2 — external-work prefix derivation correctness (Section 5e).** The claim "`state_index(target_state)` == the settled-field count" holds only because the linear order is `needs_kickoff(0) → first_worker(1) → …`, so the count of settled fields (kickoff through the field gating the stage *before* target) equals target's index. For coding this reproduces `_PREFIX_COUNT` exactly (verified: `needs_success`→index 1→1 field `kickoff`… wait: settled prefix for `needs_success` is just `kickoff`=1 field, index(`needs_success`)=1 ✓; for `done` it's all 6, index(`done`)=6 ✓). T-EW-golden pins this permanently so a future stage insertion can't silently shift it. Residual risk: a type whose `field_ids` order differs from its gating-field order — the derivation now correctly uses gating-field order (the bug fix), and probe proves it (probe happens to align, but a divergent synthetic type in the unit test would prove the fix; consider adding one). **Recommend the implementer add a synthetic "misaligned field order" type to the derivation test** to prove the fix isn't a probe-happens-to-align accident.

**R3 — type resolution before parse requires a ticket load at ingress (Section 3).** Propose/accept/scope/state/value/note handlers must `read_ticket` to learn the type before validating the position. Today several parse the enum *before* touching the DB (`api.py:545,558,690`). Adding a load is a small extra query but changes the failure ordering: an invalid-field error now comes *after* a not-found ticket error (previously the enum parse could fail first). This is correct (you can't validate a field against a type you haven't resolved) but could shift which error a test sees for a bad-ticket+bad-field combination. Mitigation: the writers already `read_ticket` internally; the handler's load is the same row. Assert the error-precedence explicitly in the ingress test (not-found beats invalid-field). Alternatively, let the **writer** own per-type field validation (it already has `coding_bridge.has_field(defn, field)` at `data.py:983-984`) and keep the handler thin — but the BRIEF wants ingress validation with exact codes, so validate at the handler after load.

**R4 — the CLI/subprocess transport gap for probe (Section 8).** The gate proves probe through the in-process API, not the CLI subprocess (which can't see the test registry). This is architecturally forced and correct, but it means the *CLI's* per-type behavior for a non-coding type is never executed end-to-end (only coding is, via the e2e subprocess). Acceptable: production CLI is coding-only; the CLI's type-genericity is structural (it reads the manifest), and the manifest endpoint + coding CLI path are both tested. Flag to owner as a known coverage boundary — NOT a silent descope.

**R5 — scope creep temptations to resist.** The data-layer `ticket_type="coding"` kwarg default (Section 4), coding's static CLI field options + `worker note`'s `_FIELDS` (Section 6, C5), and a `--field NAME=FILE` generic CLI surface are all things a reviewer might push to "finish." They are deliberately out: the ingress default is removed (wire always sends type); the internal kwarg default is test ergonomics; the CLI stays coding-shaped because production ships coding-only and probe rides the API. Each is flagged, none is silently dropped.
