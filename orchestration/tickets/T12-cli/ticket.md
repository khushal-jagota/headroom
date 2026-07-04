# T12 — CLI wiring: every §8 verb speaks HTTP (stage 4)

## Scope

Replace every NotImplementedError CLI handler with a real HTTP call. The verb tree, flags, env defaults, and exit-code contract are already fixed (T01 plan §14 + amendments); this ticket implements them exactly. No new verbs, no resolution verbs.

Contracts: `cli/main.py` tree as on disk, the route table, `core/errors.py` wire shape. SPEC §8 fully.

## Files owned

- `src/planner/cli/http.py` — one tiny client: base URL from `PLAN_SERVER_URL`; headers from `PLAN_RUN_ID`/`PLAN_CLAIM`/`PLAN_ACTOR`; httpx with sane timeout; connection failure → exit 2; `{"error": ...}` response → stderr (JSON under `--json`, terse line otherwise) + exit 1; success → stdout (raw JSON under `--json`, terse human line otherwise) + exit 0.
- `src/planner/cli/main.py` — handlers filled in; `read_body` per the amended input contract (explicit `-` or `--body-file` only); `day show` defaults to `today`; `plan seed` prints the migration report (human table, or structured JSON under `--json`).

## Acceptance for integration

Scripted self-smoke against a test-mode server on a temp DB: `plan ticket create --title X --json` → id; `PLAN_TICKET_ID=<id> plan propose success -` with multi-line stdin → pending proposal visible via `plan ticket show --json`; `plan queue approvals --json` lists it; `plan item create/set`; `plan day add-ticket/remove-ticket/show`; `plan link add/rm`; exit codes: validation error → 1 with JSON error on stderr; server down → 2. ruff + mypy strict clean; unit suite stays green.
