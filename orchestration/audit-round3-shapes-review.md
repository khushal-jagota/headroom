# Round-3 audit fix — violation 2: request-body shapes moved to contracts (§14)

Date: 2026-07-05. Fixes the second violation in `data/audit-gate-c.log`: the four
domain `api.py` modules declared local pydantic request-body models, breaking the
contracts-first rule (SPEC §14 — implementation imports its types from contracts and
never redeclares a shape locally).

## What changed

All 17 request-body shapes (the 10 the audit named plus the 7 unnamed siblings,
treated identically) are now stdlib `TypedDict`s (`total=False` — every key optional
on the wire, defaults and required keys documented inline) in the owning domain's
`contracts.py`:

- `tickets/contracts.py` (8): CreateTicketBody, ProposeBody, AcceptBody, NoteBody,
  RecapBody, GrantBody, StateBody, LinkBody (links routes are ticket-anchored and
  homed in tickets api, so the shape is homed here)
- `sprints/contracts.py` (5): CreateItemBody, ProposeStatusBody, CreateSprintBody,
  AddendumBody, CreateIdeaBody
- `days/contracts.py` (3): DayPatchBody, AddDayTicketBody, PlanNodeBody
- `dispatch/contracts.py` (1): CloseRunBody

The api routes take `raw: dict[str, Any]` bodies and marshal explicitly into the
contract shape. Shared helpers `body_str` / `body_opt_str` live in `tickets/api.py`
with the rest of the request plumbing (code, not shapes; imported one-way by the
other api modules as before). Null or wrong-typed keys raise
`PlannerError(ErrorCode.validation)` → the structured envelope (HTTP 400).

## Behavior deltas (intended)

- Malformed body **values** (wrong type / explicit null for a string key) now return
  the 400 validation envelope instead of FastAPI's 422 — the wart T10's closer
  flagged, deliberately fixed. Applies to every route that had a pydantic body.
- Everything else preserved: unknown keys ignored (no old model set strict config),
  same defaults, same optionality, same writer args, same domain error codes
  (grant_missing/grant_invalid etc.), missing-body-entirely still 422 (the dict body
  param is required, exactly as the pydantic param was).

No test changes were needed: no test in tests/unit or tests/e2e pinned a 422.

## Gates (all run fresh after the change)

- ruff: clean; mypy strict: clean (84 files)
- `pytest tests/unit -q`: 84 passed, exit 0
- `pytest tests/e2e -q`: 14 passed, exit 0
- `./verify`: **VERIFY: 36/36 PASS**, exit 0
- Ad-hoc smoke (TestClient, 11 checks): bad-type → 400 envelope, null → 400
  envelope, unknown key ignored, defaults hold, grant {} → grant_missing, bad enum →
  validation, missing body → 422 unchanged, item-without-project message unchanged,
  float plan node → validation, sprint create happy path.

## Codex review

One buffered `codex exec` run over the eight changed files against SPEC §14/§9,
asked for concrete violations only (the 422→envelope change declared in-scope and
not flaggable). Codex independently reconstructed the old pydantic models and
differentially compared `model_dump()` output against the new marshallers across
edge-case bodies (empty, explicit nulls, full) — all identical.

Verdict: `NO VIOLATIONS` / `VERDICT: PASS`.

## Dispositions

No violations to dispose. Two edges noted for the record, both accepted:

1. **Float coercion dropped.** Old pydantic lax mode would coerce `{"node": 1.0}`
   to `1`; the marshaller rejects non-int/str/null with the validation envelope.
   The contract says `int | str | None`; the coercion was an undocumented pydantic
   artifact no client (UI JS, CLI) can emit. Accepted as correct strictness.
2. **Missing body still 422.** A request with no JSON body at all fails FastAPI's
   required-parameter check before the handler runs, same as with the pydantic
   models. Unchanged behavior, so out of scope for this fix; unifying it under the
   envelope would mean a global RequestValidationError handler — a separate call.
